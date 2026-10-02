from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol


class ReplayStatus(str, Enum):
    CREATED = "created"
    RESETTING = "resetting"
    RUNNING = "running"
    EVALUATING = "evaluating"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True)
class ReplayAction:
    action_type: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "arguments": dict(self.arguments),
        }


@dataclass
class ReplayPlan:
    replay_id: str
    mission_id: str
    environment_id: str
    actions: list[ReplayAction]
    expected_state: dict[str, Any] = field(default_factory=dict)
    test_ids: list[str] = field(default_factory=list)
    record_video: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "replay_id": self.replay_id,
            "mission_id": self.mission_id,
            "environment_id": self.environment_id,
            "actions": [item.as_dict() for item in self.actions],
            "expected_state": dict(self.expected_state),
            "test_ids": list(self.test_ids),
            "record_video": bool(self.record_video),
        }


@dataclass
class ReplayResult:
    replay_id: str
    status: ReplayStatus
    success: bool
    action_results: list[dict[str, Any]] = field(default_factory=list)
    final_observation: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class ReplaySandbox(Protocol):
    """Provider-neutral interface for future LiteCUA/VM/container replay."""

    sandbox_id: str

    def reset(self, environment_id: str) -> None:
        ...

    def start_recording(self, replay_id: str) -> None:
        ...

    def execute(self, action: ReplayAction) -> dict[str, Any]:
        ...

    def observe(self) -> dict[str, Any]:
        ...

    def evaluate(
        self,
        *,
        expected_state: dict[str, Any],
        action_results: list[dict[str, Any]],
        final_observation: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def stop_recording(self, replay_id: str) -> list[str]:
        ...


class ReplayRunner:
    """Deterministic replay lifecycle independent of the sandbox backend."""

    def __init__(self, sandbox: ReplaySandbox):
        self.sandbox = sandbox

    def run(self, plan: ReplayPlan) -> ReplayResult:
        action_results: list[dict[str, Any]] = []
        artifacts: list[str] = []
        try:
            self.sandbox.reset(plan.environment_id)
            if plan.record_video:
                self.sandbox.start_recording(plan.replay_id)

            for action in plan.actions:
                result = self.sandbox.execute(action)
                action_results.append(dict(result or {}))
                if result.get("success") is False:
                    final_observation = self.sandbox.observe()
                    if plan.record_video:
                        artifacts.extend(
                            self.sandbox.stop_recording(plan.replay_id)
                        )
                    return ReplayResult(
                        replay_id=plan.replay_id,
                        status=ReplayStatus.FAILED,
                        success=False,
                        action_results=action_results,
                        final_observation=final_observation,
                        artifacts=artifacts,
                        error=str(result.get("error") or "action_failed"),
                    )

            final_observation = self.sandbox.observe()
            evaluation = self.sandbox.evaluate(
                expected_state=dict(plan.expected_state),
                action_results=action_results,
                final_observation=final_observation,
            )
            success = bool(evaluation.get("success"))
            if plan.record_video:
                artifacts.extend(
                    self.sandbox.stop_recording(plan.replay_id)
                )
            return ReplayResult(
                replay_id=plan.replay_id,
                status=(
                    ReplayStatus.PASSED
                    if success
                    else ReplayStatus.FAILED
                ),
                success=success,
                action_results=action_results,
                final_observation=final_observation,
                evaluation=dict(evaluation),
                artifacts=artifacts,
            )
        except Exception as exc:
            if plan.record_video:
                try:
                    artifacts.extend(
                        self.sandbox.stop_recording(plan.replay_id)
                    )
                except Exception:
                    pass
            return ReplayResult(
                replay_id=plan.replay_id,
                status=ReplayStatus.ERROR,
                success=False,
                action_results=action_results,
                artifacts=artifacts,
                error=str(exc)[:1200],
            )
