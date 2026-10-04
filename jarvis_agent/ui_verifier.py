"""Typed, scoped postconditions. Tool delivery and goal completion are distinct."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .semantic_grounding import normalized_text
from .ui_geometry import intersection_over_union
from .ui_observation import SurfaceIdentity, UIEntity, UIObservation

CONDITION_KINDS = frozenset({
    "value_equals", "value_contains", "value_endswith", "text_present",
    "text_absent", "new_text", "target_present", "target_absent", "selected",
    "title_contains", "url_contains",
    "focused",
})


@dataclass(frozen=True)
class Postcondition:
    kind: str
    value: str = ""
    role: str = ""
    label: str = ""
    region: str = ""
    surface: str = ""

    @classmethod
    def from_dict(cls, value: Any) -> Postcondition:
        if not isinstance(value, dict) or set(value)-{"kind", "value", "role", "label", "region", "surface"}:
            raise ValueError("Invalid postcondition fields")
        if value.get("kind") not in CONDITION_KINDS:
            raise ValueError("Unsupported postcondition")
        for key in ("value", "role", "label", "region", "surface"):
            if key in value and not isinstance(value[key], str):
                raise ValueError(f"{key} must be a string")
        result = cls(**value)
        if result.kind in {"value_equals", "value_contains", "value_endswith", "selected", "focused",
                           "target_present", "target_absent"} and not (result.role or result.label):
            raise ValueError("A target-specific predicate needs a role or label")
        if result.kind in {"text_present", "text_absent", "new_text", "title_contains",
                           "url_contains", "value_contains", "value_endswith"} and not result.value.strip():
            raise ValueError("An empty string cannot prove a predicate")
        if any(len(getattr(result, key)) > 2000 for key in ("value", "role", "label", "region", "surface")):
            raise ValueError("Postcondition too large")
        return result


def parse_conditions(values: Any) -> tuple[Postcondition, ...]:
    if not isinstance(values, list) or not values or len(values) > 24:
        raise ValueError("Provide 1..24 explicit postconditions")
    return tuple(Postcondition.from_dict(x) for x in values)


@dataclass(frozen=True)
class VerificationVerdict:
    status: str
    action_id: str
    before_observation: str
    after_observation: str
    predicates: tuple[dict[str, Any], ...]
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def matching_entities(condition: Postcondition, observation: UIObservation) -> list[UIEntity]:
    result = []
    for entity in observation.entities:
        if not entity.visible:
            continue
        if condition.role and condition.role not in entity.semantic_roles and (
            normalized_text(condition.role) != normalized_text(entity.technical_role)
        ):
            continue
        if condition.label and normalized_text(condition.label) != normalized_text(entity.label):
            continue
        if condition.region and normalized_text(condition.region) != normalized_text(entity.region):
            continue
        result.append(entity)
    return result


def text_evidence(condition: Postcondition, observation: UIObservation) -> list[tuple[str, str]]:
    entities = matching_entities(condition, observation)
    result = []
    for entity in entities:
        for text in (entity.label, entity.value):
            if text is not None and condition.value in text:
                result.append((text, entity.ref))
                break
    # Unscoped visible text is evidence only for an unscoped predicate.
    if not (condition.role or condition.label or condition.region):
        known = {text for text, _ in result}
        result += [(text, f"{observation.observation_id}:text{i}")
                   for i, text in enumerate(observation.text_regions) if condition.value in text and text not in known]
    return result


def _predicate(
    condition: Postcondition, observation: UIObservation,
) -> tuple[bool | None, list[str], Any]:
    kind = condition.kind
    entities = matching_entities(condition, observation)
    if kind == "target_present":
        return (True, [x.ref for x in entities], len(entities)) if entities else (None, [], 0)
    if kind == "target_absent":
        # Missing elements in a partial tree do not prove absence.
        if entities:
            return False, [x.ref for x in entities], len(entities)
        complete = observation.coverage.get("tree_complete") is True
        return (True if complete else None), [], 0
    if kind in {"text_present", "text_absent", "new_text"}:
        matches = text_evidence(condition, observation)
        if kind == "text_absent":
            if matches:
                return False, [ref for _, ref in matches], len(matches)
            return (True if observation.coverage.get("tree_complete") is True else None), [], 0
        return (True if matches else None), [ref for _, ref in matches], len(matches)
    if kind == "title_contains":
        passed = normalized_text(condition.value) in normalized_text(observation.scope.title)
        return passed, [f"{observation.observation_id}:window"], observation.scope.title
    if kind == "url_contains":
        if not observation.scope.page_ref:
            return None, [], None
        return condition.value in observation.scope.url, [f"{observation.observation_id}:page"], observation.scope.url
    if len(entities) != 1:
        return None, [x.ref for x in entities], "ambiguous" if entities else "not_observed"
    entity = entities[0]
    if kind == "selected":
        return entity.selected, [entity.ref], entity.selected
    if kind == "focused":
        return entity.focused, [entity.ref], entity.focused
    if entity.value is None:
        return None, [entity.ref], None
    if kind == "value_equals":
        passed = entity.value == condition.value
    elif kind == "value_endswith":
        passed = entity.value.endswith(condition.value)
    else:
        passed = condition.value in entity.value
    return passed, [entity.ref], entity.value


def verify_conditions(
    conditions: tuple[Postcondition, ...], *,
    before: UIObservation | None, after: UIObservation | None,
    action_id: str = "", require_transition: bool = False,
    allow_document_transition: bool = False,
    expected_surface: SurfaceIdentity | None = None,
) -> VerificationVerdict:
    before_id = before.observation_id if before else ""
    after_id = after.observation_id if after else ""
    if after is None or not conditions:
        return VerificationVerdict("inconclusive", action_id, before_id, after_id, (), "POSTCONDITION_MISSING")
    same_page_navigation = (
        allow_document_transition and before is not None
        and bool(before.scope.page_ref) and before.scope.page_ref == after.scope.page_ref
    )
    permitted_transition = expected_surface is not None and expected_surface.same_binding(after.scope)
    if expected_surface is not None and not permitted_transition:
        return VerificationVerdict("unsafe", action_id, before_id, after_id, (), "WRONG_EXPECTED_SURFACE")
    if before is not None and not before.scope.same_surface(after.scope) and not same_page_navigation and not permitted_transition:
        return VerificationVerdict("unsafe", action_id, before_id, after_id, (), "WRONG_SURFACE")
    if before is not None and (after.monotonic_at < before.monotonic_at or before_id == after_id):
        return VerificationVerdict("inconclusive", action_id, before_id, after_id, (), "STALE_AFTER_STATE")
    facts = []
    changed = bool(permitted_transition and before and not before.scope.same_binding(after.scope))
    any_failed = False
    any_unknown = False
    for condition in conditions:
        passed, evidence, actual = _predicate(condition, after)
        previous, _, old = _predicate(condition, before) if before else (None, [], None)
        if condition.kind == "new_text":
            if before is None or not before.scope.same_surface(after.scope):
                passed = None
            else:
                passed = True if passed is True and isinstance(actual, int) and isinstance(old, int) and actual > old else None
        changed = changed or (passed is True and (previous is not True or actual != old))
        any_failed = any_failed or passed is False
        any_unknown = any_unknown or passed is None
        facts.append({
            "predicate": asdict(condition), "passed": passed, "evidence_ids": evidence,
            "observed": actual, "previous": old,
            "surface_ref": after.scope.ref, "observation_id": after_id,
        })
    status = "failed" if any_failed else "inconclusive" if any_unknown else "passed"
    reason = "EXPECTED_TRANSITION_OBSERVED" if status == "passed" else "POSTCONDITION_NOT_PROVEN"
    if status == "passed" and require_transition and not changed:
        status, reason = "inconclusive", "EXPECTED_STATE_ALREADY_PRESENT_BEFORE_ACTION"
    return VerificationVerdict(status, action_id, before_id, after_id, tuple(facts), reason)


def reacquire_target(target: UIEntity, observation: UIObservation) -> UIEntity | None:
    """Unique exact identity/label, then stable visual geometry; never nearest text."""
    candidates = [x for x in observation.entities if x.visible and x.writable == target.writable]
    if target.native_id:
        matches = [x for x in candidates if x.native_id == target.native_id and x.technical_role == target.technical_role]
    elif target.label:
        matches = [x for x in candidates if normalized_text(x.label) == normalized_text(target.label)
                   and (set(x.semantic_roles) & set(target.semantic_roles) or x.technical_role == target.technical_role)
                   and (not target.region or x.region == target.region)]
    else:
        matches = [x for x in candidates if x.technical_role == target.technical_role
                   and intersection_over_union(x.bounds, target.bounds) > .7]
    return matches[0] if len(matches) == 1 else None
