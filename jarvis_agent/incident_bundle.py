from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .component_registry import (
    ComponentRegistry,
    DEFAULT_COMPONENT_REGISTRY,
)
from .event_journal import StructuredEventJournal


@dataclass(frozen=True)
class IncidentBundle:
    mission_id: str
    user_inputs: tuple[dict[str, Any], ...] = ()
    decisions: tuple[dict[str, Any], ...] = ()
    tool_events: tuple[dict[str, Any], ...] = ()
    observations: tuple[dict[str, Any], ...] = ()
    proofs: tuple[dict[str, Any], ...] = ()
    feedback: tuple[dict[str, Any], ...] = ()
    errors: tuple[dict[str, Any], ...] = ()
    component_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    summary: dict[str, Any] = field(default_factory=dict)


class IncidentBundleBuilder:
    """Build a bounded, inspectable failure package for Dev Supervisor.

    It relies on explicit operational events and never stores or reconstructs
    hidden model chain-of-thought.
    """

    def __init__(
        self,
        journal: StructuredEventJournal,
        *,
        components: ComponentRegistry | None = None,
    ):
        self.journal = journal
        self.components = components or DEFAULT_COMPONENT_REGISTRY

    @staticmethod
    def _select(
        trace: list[dict[str, Any]],
        kinds: set[str],
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            event
            for event in trace
            if str(event.get("kind") or "") in kinds
        )

    def build(
        self,
        mission_id: str,
        *,
        changed_paths: list[str] | None = None,
        max_events: int = 200,
    ) -> IncidentBundle:
        trace = self.journal.mission_trace(mission_id)
        if len(trace) > max(1, int(max_events)):
            trace = trace[-max(1, int(max_events)):]

        user_inputs = self._select(
            trace,
            {"user.input"},
        )
        decisions = self._select(
            trace,
            {
                "intent.resolved",
                "agent.selected",
                "llm.request",
                "llm.result",
            },
        )
        tool_events = self._select(
            trace,
            {
                "tool.requested",
                "tool.result",
                "syscall.queued",
                "syscall.started",
                "syscall.completed",
            },
        )
        observations = self._select(
            trace,
            {"observation"},
        )
        proofs = self._select(
            trace,
            {"proof"},
        )
        feedback = self._select(
            trace,
            {"user.feedback", "correction.candidate"},
        )
        errors = tuple(
            event
            for event in trace
            if event.get("success") is False
            or bool((event.get("payload") or {}).get("error"))
        )

        component_ids: set[str] = set()
        for event in trace:
            component = str(event.get("component") or "").strip()
            if component and self.components.get(component) is not None:
                component_ids.add(component)
        if changed_paths:
            component_ids.update(
                item.component_id
                for item in self.components.for_paths(changed_paths)
            )

        tools = []
        for event in tool_events:
            payload = dict(event.get("payload") or {})
            name = (
                payload.get("tool")
                or payload.get("tool_name")
                or payload.get("capability")
            )
            if name:
                tools.append(str(name))

        summary = {
            "event_count": len(trace),
            "error_count": len(errors),
            "tool_names": sorted(set(tools)),
            "has_user_feedback": bool(feedback),
            "has_proof": bool(proofs),
            "last_event_kind": (
                str(trace[-1].get("kind") or "")
                if trace
                else ""
            ),
        }

        return IncidentBundle(
            mission_id=str(mission_id),
            user_inputs=user_inputs,
            decisions=decisions,
            tool_events=tool_events,
            observations=observations,
            proofs=proofs,
            feedback=feedback,
            errors=errors,
            component_ids=tuple(sorted(component_ids)),
            event_ids=tuple(
                str(event.get("event_id") or "")
                for event in trace
                if event.get("event_id")
            ),
            summary=summary,
        )
