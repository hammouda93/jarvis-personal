"""Resolve a semantic intention to a fresh observed target, never to guessed XY."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .semantic_grounding import normalized_text
from .ui_observation import UIEntity, UIObservation


@dataclass(frozen=True)
class TargetIntent:
    ref: str = ""
    role: str = ""
    label: str = ""
    region: str = ""
    within: str = ""
    operation: str = "click"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetIntent:
        return cls(
            ref=str(data.get("ref") or ""), role=str(data.get("role") or data.get("semantic_role") or ""),
            label=str(data.get("label") or ""), region=str(data.get("region") or ""),
            within=str(data.get("within") or ""), operation=str(data.get("operation") or "click"),
        )


@dataclass(frozen=True)
class TargetResolution:
    status: str
    target: UIEntity | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "target": self.target.as_dict() if self.target else None,
                "candidates": list(self.candidates), "reason": self.reason}


def resolve_target(
    intent: TargetIntent, observation: UIObservation, *,
    generation: int | None = None, max_age_s: float | None = None,
) -> TargetResolution:
    if generation is not None and generation != observation.generation:
        return TargetResolution("stale", reason="STALE_GENERATION")
    if max_age_s is not None and time.monotonic()-observation.monotonic_at > max_age_s:
        return TargetResolution("stale", reason="STALE_OBSERVATION")
    if intent.within and intent.within not in {observation.scope.ref, observation.scope.title, observation.scope.page_ref}:
        return TargetResolution("wrong_scope", reason="WRONG_SURFACE")
    if not (intent.ref or intent.role or intent.label):
        return TargetResolution("not_found", reason="TARGET_UNSPECIFIED")
    ranked = []
    for entity in observation.entities:
        if not entity.visible or not entity.enabled:
            continue
        if intent.operation in {"write", "fill"} and not entity.writable:
            continue
        if intent.operation in {"click", "scroll", "key"} and not entity.actionable:
            continue
        if intent.region and normalized_text(intent.region) != normalized_text(entity.region):
            continue
        if intent.ref:
            if entity.ref != intent.ref:
                continue
            score = 100
        else:
            score = 0
            if intent.role:
                if intent.role not in entity.semantic_roles and normalized_text(intent.role) != normalized_text(entity.technical_role):
                    continue
                score += 20
            if intent.label:
                query, label = normalized_text(intent.label), normalized_text(entity.label)
                if not query or not label:
                    continue
                if query == label:
                    score += 30
                elif query in label:
                    score += 15
                else:
                    continue
        # A visual confidence is not a probability. Reject low-support candidates.
        if entity.sensor == "vision" and entity.score < .72:
            continue
        ranked.append((score, entity))
    if not ranked:
        return TargetResolution("stale" if intent.ref else "not_found",
                                reason="REF_NOT_IN_OBSERVATION" if intent.ref else "TARGET_NOT_EXPOSED")
    ranked.sort(key=lambda x: x[0], reverse=True)
    best_score = ranked[0][0]
    contenders = [entity for score, entity in ranked if score == best_score]
    # Structure is preferred only for the same uniquely grounded object.
    if len(contenders) > 1:
        return TargetResolution("ambiguous", candidates=tuple(x.ref for x in contenders),
                                reason="TARGET_AMBIGUOUS")
    return TargetResolution("resolved", contenders[0], reason="EVIDENCE_GROUNDED_TARGET")
