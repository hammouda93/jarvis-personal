from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .dev_supervisor import validation_destination
from .kernel_contracts import (
    CorrectionCandidate,
    PromotionTarget,
)
from .supervisor_validation import SupervisorValidationState


@dataclass(frozen=True)
class PromotionDecision:
    allowed: bool
    destination: str
    automatic_write_allowed: bool
    requires_dev_patch_pipeline: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "destination": self.destination,
            "automatic_write_allowed": self.automatic_write_allowed,
            "requires_dev_patch_pipeline": self.requires_dev_patch_pipeline,
            "reason": self.reason,
        }


class CorrectionPromotionGate:
    """Final boundary between validated feedback and executable knowledge.

    Human validation is necessary but not sufficient for Core changes. Core
    corrections must go through the Dev Supervisor patch/regression/replay path.
    """

    _AUTO_KNOWLEDGE_TARGETS = {
        PromotionTarget.APP_PROFILE,
        PromotionTarget.DOMAIN_RULE,
        PromotionTarget.AGENT_POLICY,
        PromotionTarget.SKILL,
        PromotionTarget.USER_PREFERENCE,
        PromotionTarget.SESSION_ONLY,
    }

    def evaluate(
        self,
        candidate: CorrectionCandidate,
        *,
        prevalidation: SupervisorValidationState,
    ) -> PromotionDecision:
        destination = validation_destination(candidate)

        if prevalidation.candidate_id != candidate.candidate_id:
            return PromotionDecision(
                allowed=False,
                destination=destination,
                automatic_write_allowed=False,
                requires_dev_patch_pipeline=False,
                reason="validation_candidate_mismatch",
            )

        if not prevalidation.ready_for_user_validation:
            return PromotionDecision(
                allowed=False,
                destination=destination,
                automatic_write_allowed=False,
                requires_dev_patch_pipeline=False,
                reason="evidence_gate_not_green",
            )

        if candidate.rejected:
            return PromotionDecision(
                allowed=False,
                destination=destination,
                automatic_write_allowed=False,
                requires_dev_patch_pipeline=False,
                reason="user_rejected",
            )

        if not candidate.validated:
            return PromotionDecision(
                allowed=False,
                destination=destination,
                automatic_write_allowed=False,
                requires_dev_patch_pipeline=False,
                reason="user_validation_required",
            )

        if candidate.promotion_target == PromotionTarget.CORE_INVARIANT:
            return PromotionDecision(
                allowed=True,
                destination=destination,
                automatic_write_allowed=False,
                requires_dev_patch_pipeline=True,
                reason="core_requires_patch_test_replay_pipeline",
            )

        if candidate.promotion_target == PromotionTarget.REGRESSION_TEST:
            return PromotionDecision(
                allowed=True,
                destination=destination,
                automatic_write_allowed=False,
                requires_dev_patch_pipeline=False,
                reason="regression_registry_change_requires_controlled_update",
            )

        if candidate.promotion_target in self._AUTO_KNOWLEDGE_TARGETS:
            return PromotionDecision(
                allowed=True,
                destination=destination,
                automatic_write_allowed=True,
                requires_dev_patch_pipeline=False,
                reason="validated_scoped_knowledge",
            )

        return PromotionDecision(
            allowed=False,
            destination=destination,
            automatic_write_allowed=False,
            requires_dev_patch_pipeline=False,
            reason="unsupported_promotion_target",
        )
