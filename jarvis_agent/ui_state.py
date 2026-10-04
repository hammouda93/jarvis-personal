"""Temporal UI state and bounded progress; no private model reasoning is stored."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from .ui_observation import UIObservation
from .ui_verifier import VerificationVerdict, reacquire_target


def state_transition(before: UIObservation | None, after: UIObservation) -> dict[str, Any]:
    if before is None:
        return {"before": "", "after": after.observation_id, "changes": [], "same_surface": None}
    if not before.scope.same_surface(after.scope):
        return {"before": before.observation_id, "after": after.observation_id,
                "changes": [{"kind": "surface_changed"}], "same_surface": False}
    changes = []
    matched = set()
    for old in before.entities:
        new = reacquire_target(old, after)
        if new is None:
            continue  # A partial observation does not prove disappearance.
        matched.add(new.ref)
        for field_name in ("value", "selected", "focused", "label", "enabled"):
            if getattr(old, field_name) != getattr(new, field_name):
                changes.append({"kind": field_name, "before_ref": old.ref, "after_ref": new.ref,
                                "before": getattr(old, field_name), "after": getattr(new, field_name)})
    old_labels = {x.label for x in before.entities}
    changes += [{"kind": "appeared", "after_ref": x.ref, "label": x.label}
                for x in after.entities if x.ref not in matched and x.label and x.label not in old_labels]
    return {"before": before.observation_id, "after": after.observation_id,
            "changes": changes[:60], "same_surface": True}


@dataclass
class UIState:
    current: UIObservation | None = None
    previous: UIObservation | None = None
    generation: int = 0
    transition: dict[str, Any] = field(default_factory=dict)
    verified_facts: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, observation: UIObservation) -> None:
        self.previous = self.current
        self.transition = state_transition(self.previous, observation)
        self.current = observation

    def invalidate(self) -> None:
        self.generation += 1

    def add_proof(self, verdict: VerificationVerdict) -> None:
        if verdict.passed:
            self.verified_facts.append(verdict.as_dict())
            self.verified_facts[:] = self.verified_facts[-32:]


class ProgressTracker:
    def __init__(self, *, no_effect_limit: int = 2, inspection_limit: int = 3):
        self.no_effect_limit = max(1, no_effect_limit)
        self.inspection_limit = max(1, inspection_limit)
        self.records = deque(maxlen=64)
        self.inspection_counts: dict[tuple[str, str], int] = {}
        self.no_effect_counts: dict[tuple[str, str], int] = {}

    def record_observation(self, observation: UIObservation, strategy: str) -> bool:
        key = observation.fingerprint(), strategy
        self.inspection_counts[key] = self.inspection_counts.get(key, 0)+1
        if len(self.inspection_counts) > 128:
            self.inspection_counts = {key: self.inspection_counts[key]}
        return self.inspection_counts[key] <= self.inspection_limit

    def record_action(self, *, semantic_signature: str, before: UIObservation | None,
                      after: UIObservation | None, verdict: VerificationVerdict) -> None:
        state_key = before.fingerprint() if before else "unknown"
        key = state_key, semantic_signature
        no_effect = (before is not None and after is not None and before.fingerprint() == after.fingerprint())
        no_effect = no_effect or bool(verdict.predicates and all(
            x.get("observed") == x.get("previous") for x in verdict.predicates))
        if no_effect or verdict.status == "inconclusive":
            self.no_effect_counts[key] = self.no_effect_counts.get(key, 0)+1
        else:
            self.no_effect_counts.pop(key, None)
        self.records.append({"signature": semantic_signature, "before": before.observation_id if before else "",
                             "after": after.observation_id if after else "", "status": verdict.status,
                             "reason": verdict.reason})

    def may_repeat(self, observation: UIObservation | None, signature: str) -> bool:
        key = observation.fingerprint() if observation else "unknown", signature
        return self.no_effect_counts.get(key, 0) < self.no_effect_limit

    def summary(self) -> dict[str, Any]:
        return {"recent_transitions": list(self.records)[-12:],
                "no_effect_branches": sum(1 for x in self.no_effect_counts.values() if x >= self.no_effect_limit)}
