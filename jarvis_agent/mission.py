from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .registry import ToolRegistry
from .tools import ToolIntent, ToolResult, execute


class MissionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class MissionStep:
    tool: str
    args: dict
    reason: str = ""
    status: StepStatus = StepStatus.PENDING
    intent: ToolIntent | None = None
    result: ToolResult | None = None
    error: str = ""


@dataclass
class Mission:
    objective: str
    steps: list[MissionStep]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: MissionStatus = MissionStatus.PENDING

    @property
    def final_step(self) -> MissionStep | None:
        return self.steps[-1] if self.steps else None

    @property
    def success(self) -> bool:
        return self.status == MissionStatus.SUCCEEDED


@dataclass(frozen=True)
class MissionOutcome:
    mission: Mission
    final_intent: ToolIntent | None
    final_result: ToolResult | None

    @property
    def success(self) -> bool:
        return self.mission.success


MissionLog = Callable[[str], None]


class MissionEngine:
    """Executes a planned mission one typed tool at a time.

    The model never executes code directly. Every planned step must first pass
    through ToolRegistry.prepare(), which converts it to a typed ToolIntent.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(
        self,
        mission: Mission,
        *,
        log: MissionLog | None = None,
    ) -> MissionOutcome:
        mission.status = MissionStatus.RUNNING
        if log:
            log(
                f"[MISSION] id={mission.id} status=running "
                f"steps={len(mission.steps)} objective={mission.objective!r}"
            )

        final_intent: ToolIntent | None = None
        final_result: ToolResult | None = None

        if not mission.steps:
            mission.status = MissionStatus.FAILED
            if log:
                log(f"[MISSION] id={mission.id} status=failed reason=no_steps")
            return MissionOutcome(mission, None, None)

        for index, step in enumerate(mission.steps, start=1):
            step.status = StepStatus.RUNNING
            prepared = self.registry.prepare(step.tool, step.args)
            if prepared.intent is None:
                step.status = StepStatus.FAILED
                step.error = prepared.error
                mission.status = MissionStatus.FAILED
                if log:
                    log(
                        f"[STEP] mission={mission.id} index={index} "
                        f"status=failed tool={step.tool} error={prepared.error}"
                    )
                self._skip_remaining(mission.steps[index:])
                break

            step.intent = prepared.intent
            final_intent = prepared.intent

            if log:
                log(
                    f"[STEP] mission={mission.id} index={index} "
                    f"status=running intent={prepared.intent.name} "
                    f"args={prepared.intent.args}"
                )

            result = execute(prepared.intent)
            step.result = result
            final_result = result

            if result.success:
                step.status = StepStatus.SUCCEEDED
                if log:
                    log(
                        f"[STEP] mission={mission.id} index={index} "
                        f"status=succeeded detail={result.detail!r}"
                    )
            else:
                step.status = StepStatus.FAILED
                mission.status = MissionStatus.FAILED
                if log:
                    log(
                        f"[STEP] mission={mission.id} index={index} "
                        f"status=failed detail={result.detail!r}"
                    )
                self._skip_remaining(mission.steps[index:])
                break

            if result.should_exit or result.end_session or result.follow_up:
                # A state-changing result (sleep/stop/clarification prompt)
                # deliberately ends this mission turn.
                break
        else:
            mission.status = MissionStatus.SUCCEEDED

        if mission.status == MissionStatus.RUNNING:
            failed = any(step.status == StepStatus.FAILED for step in mission.steps)
            mission.status = (
                MissionStatus.FAILED if failed else MissionStatus.SUCCEEDED
            )

        if log:
            log(f"[MISSION] id={mission.id} status={mission.status.value}")

        return MissionOutcome(mission, final_intent, final_result)

    @staticmethod
    def _skip_remaining(steps: list[MissionStep]) -> None:
        for step in steps:
            if step.status == StepStatus.PENDING:
                step.status = StepStatus.SKIPPED
