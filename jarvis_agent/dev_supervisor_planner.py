from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .component_registry import (
    ComponentRegistry,
    DEFAULT_COMPONENT_REGISTRY,
)
from .incident_bundle import IncidentBundle
from .regression_registry import (
    RegressionRegistry,
    DEFAULT_REGRESSION_REGISTRY,
)


@dataclass(frozen=True)
class SupervisorActionPlan:
    mission_id: str
    component_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    replay_recommended: bool
    sandbox_required: bool
    human_validation_required: bool
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "component_ids": list(self.component_ids),
            "test_ids": list(self.test_ids),
            "replay_recommended": self.replay_recommended,
            "sandbox_required": self.sandbox_required,
            "human_validation_required": self.human_validation_required,
            "reasons": list(self.reasons),
        }


class DevSupervisorPlanner:
    """Create a bounded validation plan from an incident bundle.

    This planner never edits code. It only identifies components, tests and
    whether replay/sandbox evidence should be required before human validation.
    """

    def __init__(
        self,
        *,
        components: ComponentRegistry | None = None,
        regressions: RegressionRegistry | None = None,
    ):
        self.components = components or DEFAULT_COMPONENT_REGISTRY
        self.regressions = regressions or DEFAULT_REGRESSION_REGISTRY

    def plan(
        self,
        incident: IncidentBundle,
        *,
        changed_paths: list[str] | None = None,
    ) -> SupervisorActionPlan:
        component_ids = set(incident.component_ids)
        if changed_paths:
            component_ids.update(
                item.component_id
                for item in self.components.for_paths(changed_paths)
            )

        tests: set[str] = set()
        tags: set[str] = set()
        for component_id in component_ids:
            component = self.components.get(component_id)
            if component is None:
                continue
            tests.update(component.default_test_ids)
            tags.update(component.tags)

        if changed_paths:
            tests.update(
                item.test_id
                for item in self.regressions.select(
                    changed_paths=changed_paths
                )
            )
        if tags:
            tests.update(
                item.test_id
                for item in self.regressions.select(tags=tags)
            )

        computer_surface = bool(
            tags
            & {
                "windows",
                "uia",
                "vision",
                "computer_use",
                "browser",
            }
        )
        external_side_effect = any(
            str((event.get("payload") or {}).get("risk") or "")
            in {"external_side_effect", "destructive"}
            for event in incident.tool_events
        )
        replay_recommended = (
            computer_surface
            or external_side_effect
            or bool(incident.errors)
        )
        sandbox_required = external_side_effect or bool(
            {"windows", "computer_use", "browser"} & tags
        )

        reasons: list[str] = []
        if incident.errors:
            reasons.append("incident_contains_errors")
        if incident.feedback:
            reasons.append("user_feedback_present")
        if computer_surface:
            reasons.append("computer_use_surface_changed")
        if external_side_effect:
            reasons.append("external_side_effect_evidence")
        if tests:
            reasons.append("targeted_regressions_available")
        if not component_ids:
            reasons.append("component_unknown")

        return SupervisorActionPlan(
            mission_id=incident.mission_id,
            component_ids=tuple(sorted(component_ids)),
            test_ids=tuple(sorted(tests)),
            replay_recommended=replay_recommended,
            sandbox_required=sandbox_required,
            human_validation_required=True,
            reasons=tuple(reasons),
        )
