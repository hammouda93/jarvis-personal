from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import Settings
from .kernel_contracts import EventKind, MissionContext, MissionStatus
from .kernel_stack import PassiveKernelStack, build_passive_kernel_stack
from .task_graph import MissionTaskGraph, TaskNode, TaskStatus


_SHADOW_AGENT_HINTS: dict[str, tuple[str, ...]] = {
    "app.open": ("windows",),
    "folder.open": ("windows",),
    "folder.open_named": ("windows",),
    "folder.open_prompt": ("windows",),
    "browser.open_url": ("browser",),
    "browser.search": ("browser",),
    "browser.search_prompt": ("browser",),
    "search_web": ("browser",),
    "open_folder": ("windows",),
    "system.time": ("interaction",),
    "get_current_time": ("interaction",),
    "assistant.stop": ("interaction",),
    "assistant.sleep": ("interaction",),
    "return_to_standby": ("interaction",),
}


def _default_shadow_dir() -> Path:
    root = Path(
        os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "kernel_shadow"


@dataclass(frozen=True)
class ShadowObservation:
    mission_id: str
    success: bool
    action_count: int
    candidate_agents: tuple[str, ...]
    needs_review: bool = False
    recovered_after_failure: bool = False


@dataclass(frozen=True)
class ShadowSupervisorAssessment:
    needs_review: bool
    recovered_after_failure: bool
    failed_tools: tuple[str, ...]
    unmapped_tools: tuple[str, ...]
    ambiguous_tools: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "needs_review": self.needs_review,
            "recovered_after_failure": self.recovered_after_failure,
            "failed_tools": list(self.failed_tools),
            "unmapped_tools": list(self.unmapped_tools),
            "ambiguous_tools": list(self.ambiguous_tools),
        }


class KernelShadowObserver:
    """Observe the live runtime without participating in control.

    This is the first convergence step between the current authoritative
    AgentRuntime and the passive Kernel foundations. It records the mission,
    actual live actions and passive agent candidates into an isolated Kernel
    stack, but it never submits a KernelRequest, runs the dispatcher, changes
    routing, calls a tool, or mutates the live AgentRuntime result.
    """

    def __init__(
        self,
        *,
        base_dir: str | Path,
        owner_user_id: str,
        settings: Settings,
    ):
        self.owner_user_id = str(owner_user_id or "local-user")
        self.stack: PassiveKernelStack = build_passive_kernel_stack(
            base_dir=base_dir,
            owner_user_id=self.owner_user_id,
            settings=settings,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "KernelShadowObserver":
        configured_dir = str(settings.kernel_shadow_dir or "").strip()
        return cls(
            base_dir=(
                Path(configured_dir).expanduser()
                if configured_dir
                else _default_shadow_dir()
            ),
            owner_user_id=settings.kernel_shadow_user_id,
            settings=settings,
        )

    def _candidate_agents(self, tool_names: Iterable[str]) -> tuple[str, ...]:
        names = {
            str(name or "").strip()
            for name in tool_names
            if str(name or "").strip()
        }
        candidates: list[str] = []
        for name in sorted(names):
            candidates.extend(_SHADOW_AGENT_HINTS.get(name, ()))
        for manifest in self.stack.registry.agents():
            if names.intersection(set(manifest.allowed_tools)):
                candidates.append(manifest.agent_id)
        return tuple(dict.fromkeys(candidates))

    @staticmethod
    def _text_agent_hint(user_text: str) -> str | None:
        text = str(user_text or "").lower()
        browser_terms = (
            "youtube", "chrome", "google", "navigateur", "browser",
            "onglet", "tab", "site", "url", "page web", "internet",
        )
        windows_terms = (
            "bloc-note", "bloc note", "bloc-notes", "notepad",
            "vscode", "vs code", "cursor", "application", "dossier",
            "fichier", "fenêtre", "fenetre",
        )
        if any(term in text for term in browser_terms):
            return "browser"
        if any(term in text for term in windows_terms):
            return "windows"
        return None

    def _mission_agent_hint(
        self,
        user_text: str,
        payloads: Sequence[dict[str, Any]],
    ) -> str | None:
        text_hint = self._text_agent_hint(user_text)
        if text_hint:
            return text_hint

        decisive_tools = {
            "browser.open_url": "browser",
            "browser.search": "browser",
            "browser.search_prompt": "browser",
            "search_web": "browser",
            "open_url": "browser",
            "app.open": "windows",
            "folder.open": "windows",
            "folder.open_named": "windows",
            "open_application": "windows",
            "open_file": "windows",
            "open_folder": "windows",
        }
        for payload in payloads:
            tool_name = str(payload.get("tool_name") or "")
            hinted = decisive_tools.get(tool_name)
            if hinted:
                return hinted
        return None

    def _resolved_agent(
        self,
        tool_name: str,
        *,
        mission_agent_hint: str | None,
    ) -> tuple[str, tuple[str, ...]]:
        candidates = self._candidate_agents([tool_name])
        if len(candidates) == 1:
            return candidates[0], candidates
        if (
            mission_agent_hint is not None
            and mission_agent_hint in candidates
        ):
            return mission_agent_hint, candidates
        return "interaction", candidates

    def _supervisor_assessment(
        self,
        payloads: Sequence[dict[str, Any]],
        *,
        turn_success: bool,
    ) -> ShadowSupervisorAssessment:
        failed_tools: list[str] = []
        unmapped_tools: list[str] = []
        ambiguous_tools: list[str] = []

        for payload in payloads:
            tool_name = str(payload.get("tool_name") or "")
            if not bool(payload.get("success")):
                failed_tools.append(tool_name)
            candidates = tuple(payload.get("candidate_agents") or ())
            resolved_agent = str(
                payload.get("resolved_agent") or "interaction"
            )
            if not candidates:
                unmapped_tools.append(tool_name)
            elif len(candidates) > 1 and resolved_agent == "interaction":
                ambiguous_tools.append(tool_name)

        recovered_after_failure = bool(failed_tools) and bool(turn_success)
        needs_review = bool(
            failed_tools
            or unmapped_tools
            or ambiguous_tools
            or not turn_success
        )
        return ShadowSupervisorAssessment(
            needs_review=needs_review,
            recovered_after_failure=recovered_after_failure,
            failed_tools=tuple(failed_tools),
            unmapped_tools=tuple(unmapped_tools),
            ambiguous_tools=tuple(ambiguous_tools),
        )

    @staticmethod
    def _action_payload(
        action: Any,
        *,
        explicit_name: str | None = None,
    ) -> dict[str, Any]:
        return {
            "tool_name": str(
                explicit_name
                or getattr(action, "name", "")
                or "direct_action"
            ),
            "success": bool(getattr(action, "success", False)),
            "message": str(getattr(action, "message", "") or "")[:1200],
            "detail": str(getattr(action, "detail", "") or "")[:2400],
            "end_session": bool(getattr(action, "end_session", False)),
            "should_exit": bool(getattr(action, "should_exit", False)),
        }

    def observe_turn(
        self,
        user_text: str,
        *,
        source: str,
        actions: Sequence[Any] = (),
        action_names: Sequence[str] | None = None,
        response_text: str = "",
        success: bool | None = None,
    ) -> ShadowObservation:
        """Mirror one completed live turn into the passive Kernel stores.

        The live result has already been decided/executed before this method is
        called. No Kernel execution path is invoked here.
        """
        source = str(source or "live_runtime")
        action_list = list(actions or ())
        explicit_names = list(action_names or ())
        payloads = [
            self._action_payload(
                action,
                explicit_name=(
                    explicit_names[index]
                    if index < len(explicit_names)
                    else None
                ),
            )
            for index, action in enumerate(action_list)
        ]
        tool_names = [item["tool_name"] for item in payloads]
        candidate_agents = self._candidate_agents(tool_names)
        mission_agent_hint = self._mission_agent_hint(
            user_text,
            payloads,
        )
        for payload in payloads:
            resolved_agent, per_action_candidates = self._resolved_agent(
                payload["tool_name"],
                mission_agent_hint=mission_agent_hint,
            )
            payload["candidate_agents"] = list(per_action_candidates)
            payload["resolved_agent"] = resolved_agent

        mission_id = self.stack.journal.create_mission(
            goal_summary=str(user_text or "")[:600],
            user_id=self.owner_user_id,
            owner_agent_id="interaction",
        )
        self.stack.journal.set_status(mission_id, MissionStatus.RUNNING)
        self.stack.journal.append_event(
            mission_id=mission_id,
            kind=EventKind.USER_INPUT,
            agent_id="interaction",
            component="kernel_shadow",
            payload={
                "text": str(user_text or "")[:2400],
                "source": source,
                "shadow": True,
                "authoritative": False,
            },
        )

        if tool_names or candidate_agents:
            self.stack.journal.append_event(
                mission_id=mission_id,
                kind=EventKind.AGENT_SELECTED,
                agent_id="interaction",
                component="kernel_shadow",
                payload={
                    "mode": "shadow",
                    "authoritative": False,
                    "actual_live_tools": tool_names,
                    "candidate_agents": list(candidate_agents),
                    "mission_agent_hint": mission_agent_hint,
                    "resolved_agents": [
                        payload["resolved_agent"]
                        for payload in payloads
                    ],
                },
            )

        for payload in payloads:
            self.stack.journal.append_event(
                mission_id=mission_id,
                kind=EventKind.TOOL_RESULT,
                agent_id="interaction",
                component="live_runtime_shadow",
                success=payload["success"],
                payload=payload,
            )

        shadow_graph = MissionTaskGraph(mission_id)
        previous_task_id: str | None = None
        for index, payload in enumerate(payloads, start=1):
            per_action_candidates = tuple(
                payload.get("candidate_agents") or ()
            )
            task_id = f"live_{index:03d}"
            node = TaskNode(
                task_id=task_id,
                mission_id=mission_id,
                capability="shadow.observed_action",
                agent_id=str(
                    payload.get("resolved_agent") or "interaction"
                ),
                dependencies=(
                    {previous_task_id}
                    if previous_task_id is not None
                    else set()
                ),
                status=(
                    TaskStatus.COMPLETED
                    if payload["success"]
                    else TaskStatus.FAILED
                ),
                priority=100 + index,
                payload={
                    "mode": "shadow",
                    "authoritative": False,
                    "tool_name": payload["tool_name"],
                    "candidate_agents": list(per_action_candidates),
                    "resolved_agent": str(
                        payload.get("resolved_agent") or "interaction"
                    ),
                    "mission_agent_hint": mission_agent_hint,
                },
                result={
                    "success": payload["success"],
                    "message": payload["message"],
                    "detail": payload["detail"],
                },
                error=(
                    ""
                    if payload["success"]
                    else payload["message"] or payload["detail"]
                ),
            )
            shadow_graph.add(node)
            previous_task_id = task_id

        if payloads:
            self.stack.graph_store.save(shadow_graph)

        failures = [item for item in payloads if not item["success"]]
        turn_success = (
            bool(success)
            if success is not None
            else not bool(failures)
        )
        final_status = (
            MissionStatus.COMPLETED
            if turn_success
            else MissionStatus.FAILED
        )
        supervisor = self._supervisor_assessment(
            payloads,
            turn_success=turn_success,
        )

        observed_state = {
            "shadow": True,
            "authoritative": False,
            "source": source,
            "action_count": len(payloads),
            "failed_action_count": len(failures),
            "actual_live_tools": tool_names,
            "candidate_agents": list(candidate_agents),
            "mission_agent_hint": mission_agent_hint,
            "resolved_agents": [
                payload["resolved_agent"]
                for payload in payloads
            ],
            "shadow_task_count": len(payloads),
            "shadow_task_graph_persisted": bool(payloads),
            "supervisor": supervisor.as_dict(),
        }
        context = MissionContext(
            mission_id=mission_id,
            user_goal=str(user_text or "")[:600],
            status=final_status,
            user_id=self.owner_user_id,
            owner_agent_id="interaction",
            observed_state=observed_state,
            tags=["shadow", "live-runtime", source],
        )
        self.stack.mission_store.save(context)

        self.stack.journal.append_event(
            mission_id=mission_id,
            kind=EventKind.OBSERVATION,
            agent_id="interaction",
            component="kernel_shadow",
            success=turn_success,
            payload={
                **observed_state,
                "response_text": str(response_text or "")[:2400],
            },
        )
        self.stack.journal.append_event(
            mission_id=mission_id,
            kind=EventKind.OBSERVATION,
            agent_id="supervisor",
            component="shadow_supervisor",
            success=turn_success,
            payload={
                "mode": "shadow",
                "authoritative": False,
                **supervisor.as_dict(),
            },
        )
        self.stack.journal.finish_mission(
            mission_id,
            success=turn_success,
            summary=str(response_text or "")[:1200],
            agent_id="interaction",
        )

        return ShadowObservation(
            mission_id=mission_id,
            success=turn_success,
            action_count=len(payloads),
            candidate_agents=candidate_agents,
            needs_review=supervisor.needs_review,
            recovered_after_failure=(
                supervisor.recovered_after_failure
            ),
        )

    def observe_agent_turn(
        self,
        user_text: str,
        turn: Any,
        *,
        source: str = "agent_runtime",
    ) -> ShadowObservation:
        return self.observe_turn(
            user_text,
            source=source,
            actions=tuple(getattr(turn, "actions", ()) or ()),
            response_text=str(getattr(turn, "text", "") or ""),
            # A returned AgentTurnResult means the live turn itself completed.
            # Individual tool failures stay visible in the shadow graph/events
            # and can later be assessed by the Supervisor without rewriting
            # the authoritative live outcome.
            success=True,
        )

    def observe_direct_turn(
        self,
        user_text: str,
        *,
        intent_name: str,
        result: Any,
        response_text: str = "",
        source: str = "direct_runtime",
    ) -> ShadowObservation:
        return self.observe_turn(
            user_text,
            source=source,
            actions=(result,),
            action_names=(str(intent_name),),
            response_text=response_text,
            success=bool(getattr(result, "success", False)),
        )
