from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MissionStepState(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class MissionEntity:
    """A semantic value carried across the whole mission.

    Examples are deliberately role-based rather than app-specific:
    contact/person, document, query, message body, filename, destination, etc.
    """

    role: str
    value: str
    source_text: str = ""
    confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissionStep:
    """One goal-level step, independent from the concrete tool used."""

    step_id: str
    intent: str
    target_role: str = ""
    content_role: str = ""
    depends_on: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    state: MissionStepState = MissionStepState.PLANNED

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["depends_on"] = list(self.depends_on)
        data["required_evidence"] = list(self.required_evidence)
        data["state"] = self.state.value
        return data


@dataclass(frozen=True)
class MissionContract:
    """Structured meaning of a user mission before tool selection.

    The contract describes *what must become true*, not which UI refs, apps,
    buttons or tools to call. It is therefore reusable across Windows apps,
    browsers, connected services and future specialized agents.
    """

    source_text: str
    objective: str
    entities: tuple[MissionEntity, ...] = ()
    steps: tuple[MissionStep, ...] = ()
    constraints: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.objective.strip():
            raise ValueError("mission_objective_required")

        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("duplicate_mission_step_id")

        known = set(step_ids)
        for step in self.steps:
            if not step.step_id.strip():
                raise ValueError("mission_step_id_required")
            if not step.intent.strip():
                raise ValueError("mission_step_intent_required")
            if step.step_id in set(step.depends_on):
                raise ValueError("mission_step_self_dependency")
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(
                    "unknown_mission_step_dependency:"
                    + ",".join(sorted(unknown))
                )

        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {step.step_id: step for step in self.steps}

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            if step_id in visiting:
                raise ValueError("mission_step_dependency_cycle")
            visiting.add(step_id)
            for dep in by_id[step_id].depends_on:
                visit(dep)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)

    def entity_map(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for entity in self.entities:
            role = str(entity.role or "").strip()
            if role:
                result[role] = str(entity.value or "")
        return result

    def completion_requirements(self) -> tuple[str, ...]:
        requirements: list[str] = []
        for step in self.steps:
            requirements.extend(step.required_evidence)
        return tuple(dict.fromkeys(requirements))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "objective": self.objective,
            "entities": [item.as_dict() for item in self.entities],
            "steps": [item.as_dict() for item in self.steps],
            "constraints": list(self.constraints),
            "unresolved": list(self.unresolved),
            "version": int(self.version),
            "metadata": dict(self.metadata),
        }
