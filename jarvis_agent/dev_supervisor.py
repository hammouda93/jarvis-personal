from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .kernel_contracts import (
    CorrectionCandidate,
    KnowledgeScope,
    PromotionTarget,
)


class FailureKind(str, Enum):
    CORE_INVARIANT = "core_invariant"
    TOOL_PRIMITIVE = "tool_primitive"
    APP_PROFILE = "app_profile"
    SKILL = "skill"
    USER_PREFERENCE = "user_preference"
    STT = "stt"
    MODEL_REASONING = "model_reasoning"
    MISSING_CAPABILITY = "missing_capability"
    UI_CHANGED = "ui_changed"
    CONNECTOR = "connector"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FailureAssessment:
    kind: FailureKind
    summary: str
    confidence: float
    proposed_scope: KnowledgeScope
    promotion_target: PromotionTarget
    evidence_event_ids: tuple[str, ...] = ()
    component: str | None = None
    app_id: str | None = None
    domain: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "summary": self.summary,
            "confidence": max(0.0, min(float(self.confidence), 1.0)),
            "proposed_scope": self.proposed_scope.value,
            "promotion_target": self.promotion_target.value,
            "evidence_event_ids": list(self.evidence_event_ids),
            "component": self.component,
            "app_id": self.app_id,
            "domain": self.domain,
        }


def candidate_from_assessment(
    *,
    mission_id: str,
    assessment: FailureAssessment,
    user_id: str | None = None,
    agent_id: str | None = None,
    skill_id: str | None = None,
    test_ids: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
) -> CorrectionCandidate:
    """Create a pending correction; never promotes it automatically."""
    return CorrectionCandidate(
        candidate_id=f"c_{uuid.uuid4().hex}",
        mission_id=mission_id,
        summary=assessment.summary,
        proposed_scope=assessment.proposed_scope,
        promotion_target=assessment.promotion_target,
        source_event_ids=list(assessment.evidence_event_ids),
        user_id=user_id,
        agent_id=agent_id,
        app_id=assessment.app_id,
        domain=assessment.domain,
        skill_id=skill_id,
        test_ids=list(test_ids or []),
        evidence=dict(evidence or {}),
        validated=False,
        rejected=False,
    )


def validation_destination(
    candidate: CorrectionCandidate,
) -> str:
    """Return the destination that would receive a validated correction."""
    mapping = {
        PromotionTarget.CORE_INVARIANT: "core_regression_and_patch",
        PromotionTarget.APP_PROFILE: "app_profile",
        PromotionTarget.DOMAIN_RULE: "domain_knowledge",
        PromotionTarget.AGENT_POLICY: "agent_policy",
        PromotionTarget.SKILL: "verified_skill",
        PromotionTarget.USER_PREFERENCE: "user_preference",
        PromotionTarget.REGRESSION_TEST: "regression_test",
        PromotionTarget.SESSION_ONLY: "session_only",
    }
    return mapping[candidate.promotion_target]
