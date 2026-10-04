"""Mission-local surface bindings and scoped evidence, independent of any application."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from .ui_observation import SurfaceIdentity, UIObservation
from .ui_verifier import Postcondition, VerificationVerdict, verify_conditions


def surface_name(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,39}", value):
        raise ValueError("Use a surface name with 1..40 letters, digits or underscores")
    return value


def condition_key(condition: Postcondition) -> str:
    return json.dumps(asdict(condition), sort_keys=True, ensure_ascii=False)


@dataclass
class SurfaceBinding:
    name: str
    identity: SurfaceIdentity
    baseline: UIObservation
    latest: UIObservation


@dataclass(frozen=True)
class ExpectedSurfaceTransition:
    kind: str
    surface: str
    source: SurfaceIdentity
    existing: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Any, source: SurfaceIdentity,
                  inventory: list[dict[str, Any]]) -> ExpectedSurfaceTransition:
        if not isinstance(value, dict) or set(value) != {"kind", "surface"}:
            raise ValueError("expected_transition requires kind and surface only")
        if value["kind"] not in {"owned_window", "popup", "bound_surface"}:
            raise ValueError("Unsupported surface transition")
        name = surface_name(value["surface"])
        if not source.has_stable_identity:
            raise ValueError("A surface transition requires a native HWND/PID or a page_ref")
        if value["kind"] == "popup" and not source.page_ref:
            raise ValueError("A popup transition requires a browser page")
        if value["kind"] == "owned_window" and source.page_ref:
            raise ValueError("An owned_window transition requires a native window")
        if not any(source.same_binding(SurfaceIdentity.from_payload(item)) for item in inventory):
            raise ValueError("TRANSITION_BASELINE_INVENTORY_REQUIRED")
        return cls(value["kind"], name, source, tuple(
            str(item.get("page_ref") or item.get("hwnd") or item.get("handle") or "") for item in inventory))


class MissionSurfaces:
    def __init__(self):
        self.bindings: dict[str, SurfaceBinding] = {}
        self.future: set[str] = set()
        self.frozen = False
        self.message_baselines: dict[str, tuple[UIObservation, str]] = {}

    def bind(self, name: str, observation: UIObservation, *, transition: bool = False,
             allow_weak: bool = False) -> SurfaceBinding:
        name = surface_name(name)
        if not allow_weak and not observation.scope.has_stable_identity:
            raise ValueError("SURFACE_IDENTITY_TOO_WEAK")
        existing = self.bindings.get(name)
        if existing:
            if not existing.identity.same_binding(observation.scope):
                raise ValueError("SURFACE_BINDING_IMMUTABLE")
            existing.latest = observation
            return existing
        if self.frozen and not (transition and name in self.future):
            raise ValueError("SURFACE_BINDINGS_FROZEN")
        if len(self.bindings) + len(self.future - {name}) >= 16:
            raise ValueError("SURFACE_LIMIT_REACHED")
        binding = SurfaceBinding(name, observation.scope, observation, observation)
        self.bindings[name] = binding
        self.future.discard(name)
        return binding

    def freeze(self, conditions: tuple[Postcondition, ...], observation: UIObservation,
               future: Any = None) -> None:
        names = future if future is not None else []
        if not isinstance(names, list) or len(names) > 15:
            raise ValueError("future_surfaces must be a list of at most 15 names")
        declared = {surface_name(name) for name in names}
        if len(declared) != len(names) or "default" in declared or declared & self.bindings.keys():
            raise ValueError("Duplicate or already bound future surface")
        needed = {surface_name(x.surface) for x in conditions if x.surface}
        if needed - self.bindings.keys() - declared - {"default"}:
            raise ValueError("GOAL_SURFACE_NOT_BOUND_OR_DECLARED")
        if len(set(self.bindings) | declared | {"default"}) > 16:
            raise ValueError("SURFACE_LIMIT_REACHED")
        # Validate everything before changing the frozen contract.
        self.bind("default", observation, allow_weak=True)
        self.future = declared
        self.frozen = True

    def observe(self, observation: UIObservation) -> None:
        for binding in self.bindings.values():
            if binding.identity.same_binding(observation.scope):
                binding.latest = observation

    def get(self, name: str) -> SurfaceBinding:
        name = surface_name(name or "default")
        if name not in self.bindings:
            raise ValueError("SURFACE_NOT_BOUND")
        return self.bindings[name]

    def resolve_transition(self, transition: ExpectedSurfaceTransition,
                           inventory: list[dict[str, Any]]) -> SurfaceIdentity:
        bound = self.bindings.get(transition.surface)
        if bound:
            candidates = [SurfaceIdentity.from_payload(item) for item in inventory
                          if bound.identity.same_binding(SurfaceIdentity.from_payload(item))]
        elif transition.kind == "bound_surface" or transition.surface not in self.future:
            raise ValueError("TRANSITION_SURFACE_NOT_DECLARED")
        else:
            candidates = []
            for item in inventory:
                identity = SurfaceIdentity.from_payload(item)
                key = identity.page_ref or identity.window_id
                if key in transition.existing or not identity.has_stable_identity:
                    continue
                if transition.kind == "popup":
                    related = identity.page_ref and identity.opener_page_ref == transition.source.page_ref
                else:
                    related = not identity.page_ref and (
                        identity.owner_window_id == transition.source.window_id or
                        identity.root_owner_window_id == transition.source.window_id)
                if related:
                    candidates.append(identity)
        if len(candidates) != 1:
            raise ValueError("AMBIGUOUS_SURFACE_TRANSITION" if candidates else "EXPECTED_SURFACE_NOT_OBSERVED")
        return candidates[0]

    def record_message_proof(self, conditions: tuple[Postcondition, ...], verdict: VerificationVerdict,
                             before: UIObservation | None, after: UIObservation) -> None:
        if not verdict.passed or before is None or not before.scope.same_surface(after.scope):
            return
        for condition in conditions:
            if condition.kind == "new_text":
                # Match equivalent aliases by pinned identity, never by a title.
                for name, binding in self.bindings.items():
                    if binding.identity.same_binding(after.scope):
                        scoped = Postcondition(**{**asdict(condition), "surface": name})
                        self.message_baselines[condition_key(scoped)] = (before, verdict.action_id)
        while len(self.message_baselines) > 128:
            self.message_baselines.pop(next(iter(self.message_baselines)))

    def verify(self, conditions: tuple[Postcondition, ...], *, action_id: str,
               max_age_s: float) -> VerificationVerdict:
        facts = []
        statuses = []
        observation_ids = []
        reasons = []
        for condition in conditions:
            name = condition.surface or "default"
            binding = self.bindings.get(name)
            if binding is None or time.monotonic() - binding.latest.monotonic_at > max_age_s:
                statuses.append("inconclusive")
                reasons.append("SURFACE_NOT_BOUND" if binding is None else "STALE_SURFACE_EVIDENCE")
                facts.append({"predicate": asdict(condition), "passed": None, "surface": name,
                              "evidence_ids": [], "reason": reasons[-1]})
                continue
            before = binding.baseline
            proof_action = ""
            if condition.kind == "new_text":
                scoped = Postcondition(**{**asdict(condition), "surface": name})
                proof = self.message_baselines.get(condition_key(scoped))
                if proof is None:
                    statuses.append("inconclusive")
                    reasons.append("NEW_TEXT_ACTION_PROOF_REQUIRED")
                    facts.append({"predicate": asdict(condition), "passed": None, "surface": name,
                                  "evidence_ids": [], "reason": reasons[-1]})
                    continue
                before, proof_action = proof
            # Before==after is a valid initial fact for positive goals, not an action transition.
            baseline = before if before.observation_id != binding.latest.observation_id else None
            verdict = verify_conditions((condition,), before=baseline, after=binding.latest,
                                        action_id=action_id, allow_document_transition=bool(binding.identity.page_ref),
                                        expected_surface=binding.identity)
            statuses.append(verdict.status)
            reasons.append(verdict.reason)
            observation_ids.append(binding.latest.observation_id)
            facts.extend({**fact, "surface": name, "proof_action_id": proof_action,
                          "before_observation": before.observation_id} for fact in verdict.predicates)
        status = next((x for x in ("unsafe", "failed", "inconclusive") if x in statuses), "passed")
        return VerificationVerdict(status, action_id, "", observation_ids[-1] if observation_ids else "", tuple(facts),
                                   "ALL_SCOPED_GOALS_PROVEN" if status == "passed" else next(
                                       (reason for reason, state in zip(reasons, statuses) if state == status),
                                       "SCOPED_GOAL_NOT_PROVEN"))

    def summary(self) -> dict[str, Any]:
        return {"bindings": [{"name": name, "identity": item.identity.as_dict(),
                              "baseline_observation": item.baseline.observation_id,
                              "latest_observation": item.latest.observation_id}
                             for name, item in self.bindings.items()],
                "future_surfaces": sorted(self.future), "frozen": self.frozen}
