from __future__ import annotations

import threading
from typing import Any

from .event_journal import StructuredEventJournal
from .kernel_contracts import EventKind, MissionStatus


class TracingToolRegistry:
    """Transparent proxy that journals exact tool args/results for one mission."""

    def __init__(
        self,
        delegate: Any,
        *,
        journal: StructuredEventJournal,
    ):
        self._delegate = delegate
        self.journal = journal
        self._local = threading.local()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def knowledge(self):
        return getattr(self._delegate, "knowledge", None)

    def set_mission(
        self,
        mission_id: str | None,
        *,
        agent_id: str | None = None,
    ) -> None:
        self._local.mission_id = mission_id
        self._local.agent_id = agent_id

    @property
    def active_mission_id(self) -> str:
        return getattr(self._local, "mission_id", "") or ""

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        approved: bool = False,
    ):
        mission_id = getattr(self._local, "mission_id", None)
        agent_id = getattr(self._local, "agent_id", None)
        request_event_id = None
        if mission_id:
            request_event_id = self.journal.append_event(
                mission_id=mission_id,
                kind=EventKind.TOOL_REQUESTED,
                agent_id=agent_id,
                component="native_tools",
                payload={
                    "tool_name": str(name),
                    "arguments": dict(arguments or {}),
                    "approved": bool(approved),
                },
            )

        result = self._delegate.execute(
            name,
            arguments,
            approved=approved,
        )

        if mission_id:
            self.journal.append_event(
                mission_id=mission_id,
                kind=EventKind.TOOL_RESULT,
                agent_id=agent_id,
                component="native_tools",
                parent_event_id=request_event_id,
                success=bool(getattr(result, "success", False)),
                payload={
                    "tool_name": str(name),
                    "message": str(
                        getattr(result, "message", "") or ""
                    ),
                    "detail": str(
                        getattr(result, "detail", "") or ""
                    ),
                    "end_session": bool(
                        getattr(result, "end_session", False)
                    ),
                    "should_exit": bool(
                        getattr(result, "should_exit", False)
                    ),
                },
            )
            controller = getattr(self._delegate, "_computer_use", None)
            if controller is not None:
                kinds = {"observation": EventKind.OBSERVATION, "proof": EventKind.PROOF,
                         "transition": EventKind.UI_STATE_TRANSITION, "decision": EventKind.INTENT_RESOLVED}
                for event in controller.drain_events():
                    self.journal.append_event(
                        mission_id=mission_id, kind=kinds.get(event["kind"], EventKind.INTENT_RESOLVED),
                        agent_id=agent_id, component="computer_use_engine", parent_event_id=request_event_id,
                        payload=event,
                    )
        return result


class StructuredTracingRuntime:
    """Transparent AgentRuntime wrapper for opt-in structured traces."""

    def __init__(
        self,
        delegate: Any,
        tools: TracingToolRegistry,
        *,
        journal: StructuredEventJournal | None = None,
        owner_agent_id: str = "interaction",
    ):
        self.delegate = delegate
        self.tools = tools
        self.journal = journal or tools.journal
        self.owner_agent_id = owner_agent_id

    def reset(self) -> None:
        self.delegate.reset()

    def cancel(self) -> None:
        cancel = getattr(self.delegate, "cancel", None)
        if callable(cancel):
            cancel()

    def warm_up(self, *, log=None) -> None:
        self.delegate.warm_up(log=log)

    def run(self, user_text: str, *, log=None, phase=None):
        mission_id = self.journal.create_mission(
            goal_summary=str(user_text or "")[:600],
            owner_agent_id=self.owner_agent_id,
        )
        self.journal.append_event(
            mission_id=mission_id,
            kind=EventKind.USER_INPUT,
            agent_id=self.owner_agent_id,
            component="interaction_runtime",
            payload={"text": str(user_text or "")[:2400]},
        )
        self.tools.set_mission(
            mission_id,
            agent_id=self.owner_agent_id,
        )

        try:
            result = self.delegate.run(
                user_text,
                log=log,
                phase=phase,
            )
        except Exception as exc:
            self.journal.append_event(
                mission_id=mission_id,
                kind=EventKind.LLM_RESULT,
                agent_id=self.owner_agent_id,
                component="interaction_runtime",
                success=False,
                payload={
                    "error": str(exc)[:1200],
                },
            )
            self.journal.finish_mission(
                mission_id,
                success=False,
                summary=str(exc)[:1200],
                agent_id=self.owner_agent_id,
            )
            raise
        finally:
            self.tools.set_mission(None)

        failures = [
            action
            for action in getattr(result, "actions", ())
            if not bool(getattr(action, "success", False))
        ]
        goal_completed = getattr(result, "goal_completed", None)
        mission_status = getattr(result, "mission_status", "")
        proven = goal_completed is True
        answered = mission_status == "answered" and not failures
        succeeded = proven or answered
        final_status = MissionStatus.COMPLETED if succeeded else (
            MissionStatus.WAITING_USER if mission_status == "awaiting_approval" else MissionStatus.BLOCKED)
        self.journal.append_event(
            mission_id=mission_id,
            kind=EventKind.LLM_RESULT,
            agent_id=self.owner_agent_id,
            component="interaction_runtime",
            success=succeeded,
            payload={
                "response_text": str(
                    getattr(result, "text", "") or ""
                )[:2400],
                "action_count": len(
                    getattr(result, "actions", ()) or ()
                ),
                "failed_action_count": len(failures),
                "goal_completed": goal_completed,
                "mission_status": mission_status or "unproven",
                "verification": getattr(result, "verification", None),
                "end_session": bool(
                    getattr(result, "end_session", False)
                ),
                "should_exit": bool(
                    getattr(result, "should_exit", False)
                ),
            },
        )
        self.journal.finish_mission(
            mission_id,
            success=succeeded,
            summary=str(getattr(result, "text", "") or "")[:1200],
            agent_id=self.owner_agent_id,
            final_status=final_status,
        )
        return result
