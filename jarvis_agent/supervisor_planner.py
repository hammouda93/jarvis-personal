from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .dev_supervisor import validation_destination
from .kernel_contracts import CorrectionCandidate, PromotionTarget
from .regression_registry import (
    DEFAULT_REGRESSION_REGISTRY,
    RegressionRegistry,
)


@dataclass(frozen=True)
class SupervisorPlan:
    candidate_id: str
    destination: str
    selected_test_ids: tuple[str, ...]
    requires_replay: bool
    requires_user_validation: bool = True
    notes: tuple[str, ...] = ()


class SupervisorPlanner:
    """Deterministic planning around a correction candidate.

    Diagnosis/patch generation may later use an LLM, but promotion gates and
    test/replay requirements stay explicit and testable.
    """

    def __init__(
        self,
        registry: RegressionRegistry | None = None,
    ):
        self.registry = registry or DEFAULT_REGRESSION_REGISTRY

    def plan(
        self,
        candidate: CorrectionCandidate,
        *,
        changed_paths: Iterable[str] = (),
        tags: Iterable[str] = (),
    ) -> SupervisorPlan:
        selected = {
            str(test_id)
            for test_id in candidate.test_ids
            if str(test_id)
        }
        selected.update(
            spec.test_id
            for spec in self.registry.select(
                tags=tags,
                changed_paths=changed_paths,
            )
        )

        replay_targets = {
            PromotionTarget.CORE_INVARIANT,
            PromotionTarget.APP_PROFILE,
            PromotionTarget.AGENT_POLICY,
            PromotionTarget.SKILL,
            PromotionTarget.REGRESSION_TEST,
        }
        requires_replay = (
            candidate.promotion_target in replay_targets
        )

        notes: list[str] = []
        if not selected:
            notes.append("no_registered_regression_selected")
        if requires_replay:
            notes.append("sandbox_replay_required_before_promotion")
        if candidate.promotion_target == PromotionTarget.USER_PREFERENCE:
            notes.append("keep_user_scoped")

        return SupervisorPlan(
            candidate_id=candidate.candidate_id,
            destination=validation_destination(candidate),
            selected_test_ids=tuple(sorted(selected)),
            requires_replay=requires_replay,
            requires_user_validation=True,
            notes=tuple(notes),
        )
