"""Provider-independent semantic contracts and hybrid scoring for Memory Core V5."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any


KINDS = {
    "fact",
    "preference",
    "intention",
    "event",
    "project",
    "decision",
    "constraint",
    "relationship",
    "observation",
}
CARDINALITIES = {"single", "collection", "history", "unknown"}
ANSWER_MODES = {"single", "collection", "timeline", "inspect"}
ANSWER_FIELDS = {"value", "subject"}
OPERATIONS = {"write", "recall", "inspect", "clarify", "pass"}


def normalize_text(value: str) -> str:
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def normalize_key(value: str) -> str:
    words = [
        token.strip("_")
        for token in re.findall(r"\w+", normalize_text(value), flags=re.UNICODE)
        if token.strip("_")
    ]
    return "_".join(words)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize_text(value)))


def semantic_key_similarity(left: str, right: str) -> float:
    a, b = normalize_key(left), normalize_key(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split("_")), set(b.split("_"))
    overlap = len(ta & tb) / max(1, len(ta | tb))
    sequence = SequenceMatcher(None, a, b).ratio()
    return max(overlap, sequence * 0.88)


def lexical_similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def entity_context_similarity(query_entity: str, fact_entity: str) -> float:
    """Directional alias similarity for named entities.

    The query may be a shorter alias of the stored entity ("Atlas" ->
    "Project Atlas"), but sharing only a generic-looking prefix must not make
    peer entities equivalent ("Project North" != "Project South").

    This stays domain-neutral by comparing token coverage rather than keeping a
    vocabulary of entity types.
    """
    left = normalize_text(query_entity)
    right = normalize_text(fact_entity)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    left_tokens = re.findall(r"[\w]+", left, flags=re.UNICODE)
    right_tokens = re.findall(r"[\w]+", right, flags=re.UNICODE)
    if not left_tokens or not right_tokens:
        return 0.0

    # A query alias can be strictly shorter than the stored canonical surface.
    if set(left_tokens).issubset(set(right_tokens)):
        return 1.0

    token_scores = []
    for wanted in left_tokens:
        best = max(
            (
                SequenceMatcher(None, wanted, actual).ratio()
                for actual in right_tokens
            ),
            default=0.0,
        )
        token_scores.append(best)

    directional = sum(token_scores) / len(token_scores)
    whole = SequenceMatcher(None, left, right).ratio()

    # Whole-string similarity alone is not enough: "Project North" and
    # "Project South" have a long common prefix. Token coverage must also be
    # strong for every part of the query entity.
    weakest = min(token_scores)
    if weakest < 0.72:
        return min(directional, 0.79)
    return max(directional, whole * 0.95)


def _clean_entities(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    result = []
    seen = set()
    for item in value:
        text = str(item or "").strip()[:300]
        key = normalize_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return tuple(result[:32])


def _clean_qualifiers(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        k = normalize_key(str(key))
        text = str(item or "").strip()
        if k and text:
            result[k] = text[:500]
    return result


@dataclass(frozen=True)
class MemoryProjection:
    subject: str
    relation: str
    value: str
    kind: str = "fact"
    qualifiers: dict[str, str] = field(default_factory=dict)
    entities: tuple[str, ...] = ()
    scope: str = "global"
    cardinality: str = "unknown"
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MemoryProjection":
        subject = normalize_key(str(payload.get("subject") or "user")) or "user"
        relation = normalize_key(str(payload.get("relation") or ""))
        value = str(payload.get("value") or payload.get("object") or "").strip()
        kind = normalize_key(str(payload.get("kind") or "fact"))
        if kind not in KINDS:
            kind = "fact"
        cardinality = normalize_key(str(payload.get("cardinality") or "unknown"))
        if cardinality not in CARDINALITIES:
            cardinality = "unknown"
        scope = normalize_key(str(payload.get("scope") or "global")) or "global"
        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(confidence, 1.0))
        if not relation or not value:
            raise ValueError("semantic_projection_requires_relation_and_value")
        return cls(
            subject=subject,
            relation=relation,
            value=value[:1000],
            kind=kind,
            qualifiers=_clean_qualifiers(payload.get("qualifiers")),
            entities=_clean_entities(payload.get("entities")),
            scope=scope,
            cardinality=cardinality,
            confidence=confidence,
        )

    @property
    def identity(self) -> str:
        qualifiers = json.dumps(
            self.qualifiers,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return "|".join(
            (
                self.subject,
                self.relation,
                normalize_text(self.value),
                self.scope,
                "|".join(sorted(normalize_text(item) for item in self.entities)),
                qualifiers,
            )
        )


@dataclass(frozen=True)
class MemoryQueryFrame:
    subject: str = "user"
    relation: str = ""
    object_hint: str = ""
    qualifiers: dict[str, str] = field(default_factory=dict)
    entities: tuple[str, ...] = ()
    scope: str = "global"
    answer_mode: str = "single"
    answer_field: str = "value"
    exact_terms: tuple[str, ...] = ()
    raw_text: str = ""
    confidence: float = 0.0

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any] | None,
        *,
        raw_text: str = "",
    ) -> "MemoryQueryFrame":
        data = payload if isinstance(payload, dict) else {}
        answer_mode = normalize_key(str(data.get("answer_mode") or "single"))
        if answer_mode not in ANSWER_MODES:
            answer_mode = "single"
        answer_field = normalize_key(
            str(data.get("answer_field") or "value")
        )
        if answer_field not in ANSWER_FIELDS:
            answer_field = "value"
        raw_subject = data.get("subject", "user")
        subject = (
            normalize_key(str(raw_subject))
            if raw_subject is not None
            else "user"
        )
        exact = data.get("exact_terms") or ()
        if not isinstance(exact, (list, tuple)):
            exact = ()
        exact_terms = tuple(
            str(item).strip()[:300]
            for item in exact
            if str(item).strip()
        )
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            subject=subject,
            relation=normalize_key(str(data.get("relation") or "")),
            object_hint=str(data.get("object_hint") or "").strip()[:1000],
            qualifiers=_clean_qualifiers(data.get("qualifiers")),
            entities=_clean_entities(data.get("entities")),
            scope=normalize_key(str(data.get("scope") or "global")) or "global",
            answer_mode=answer_mode,
            answer_field=answer_field,
            exact_terms=exact_terms,
            raw_text=str(raw_text or "")[:2000],
            confidence=max(0.0, min(confidence, 1.0)),
        )


@dataclass(frozen=True)
class MemoryTurnInterpretation:
    operation: str
    write_text: str = ""
    query: MemoryQueryFrame | None = None
    session_facts: tuple[MemoryProjection, ...] = ()
    confidence: float = 0.0
    reason: str = ""

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        user_text: str,
    ) -> "MemoryTurnInterpretation":
        operation = normalize_key(str(payload.get("operation") or "pass"))
        if operation not in OPERATIONS:
            operation = "pass"
        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        query = None
        if operation in {"recall", "clarify"}:
            query = MemoryQueryFrame.from_dict(
                payload.get("query"),
                raw_text=user_text,
            )
        elif operation == "inspect":
            query = MemoryQueryFrame(
                answer_mode="inspect",
                raw_text=user_text,
                confidence=max(0.0, min(confidence, 1.0)),
            )
        session_facts = []
        for fact in payload.get("session_facts") or []:
            if not isinstance(fact, dict):
                continue
            try:
                projection = MemoryProjection.from_dict(fact)
            except ValueError:
                continue
            if projection.confidence >= 0.55:
                session_facts.append(projection)

        return cls(
            operation=operation,
            write_text=str(payload.get("write_text") or "").strip()[:4000],
            query=query,
            session_facts=tuple(session_facts[:12]),
            confidence=max(0.0, min(confidence, 1.0)),
            reason=str(payload.get("reason") or "").strip()[:500],
        )


@dataclass(frozen=True)
class SemanticFactRecord:
    fact_id: int
    memory_id: int
    ordinal: int
    projection: MemoryProjection
    provenance: str
    parser_version: str
    status: str
    raw_content: str
    created_at: str


@dataclass(frozen=True)
class SemanticMemoryHit:
    fact: SemanticFactRecord
    score: float
    components: dict[str, float]


_EXACT_QUALIFIER_KEYS = {
    "date",
    "datetime",
    "start_at",
    "end_at",
    "id",
    "account_id",
    "reference_id",
}


def _qualifier_score(query: MemoryQueryFrame, fact: MemoryProjection) -> float:
    if not query.qualifiers:
        return 1.0
    scores = []
    for key, wanted in query.qualifiers.items():
        actual = fact.qualifiers.get(key)
        if actual is None:
            return 0.0
        wanted_norm = normalize_text(wanted)
        actual_norm = normalize_text(actual)
        if wanted_norm == actual_norm:
            scores.append(1.0)
        elif key in _EXACT_QUALIFIER_KEYS:
            return 0.0
        else:
            scores.append(semantic_key_similarity(wanted_norm, actual_norm))
    return sum(scores) / max(1, len(scores))


def query_is_specific_enough(query: MemoryQueryFrame) -> bool:
    if 0.0 < query.confidence < 0.55:
        return False
    return bool(
        query.relation
        or query.object_hint
        or query.qualifiers
        or query.entities
        or query.exact_terms
    )


def score_semantic_fact(
    query: MemoryQueryFrame,
    fact: SemanticFactRecord,
) -> SemanticMemoryHit | None:
    projection = fact.projection

    if query.scope in {"", "global"}:
        if projection.scope != "global":
            return None
    elif projection.scope not in {query.scope, "global"}:
        return None

    combined = " ".join(
        [
            fact.raw_content,
            projection.value,
            projection.relation,
            " ".join(projection.qualifiers.values()),
        ]
    )
    normalized_combined = normalize_text(combined)
    for exact in query.exact_terms:
        if normalize_text(exact) not in normalized_combined:
            return None

    relation = (
        semantic_key_similarity(query.relation, projection.relation)
        if query.relation
        else 0.5
    )
    if query.relation and relation < 0.48:
        return None

    subject = (
        semantic_key_similarity(query.subject, projection.subject)
        if query.subject
        else 1.0
    )
    if query.subject and subject < 0.45:
        return None

    qualifier = _qualifier_score(query, projection)
    if query.qualifiers and qualifier < 0.55:
        return None

    entity_score = 0.5
    if query.entities:
        wanted_entities = [
            normalize_text(item)
            for item in query.entities
            if normalize_text(item)
        ]
        fact_entities = [
            normalize_text(item)
            for item in projection.entities
            if normalize_text(item)
        ]
        entity_matches = []
        for wanted in wanted_entities:
            best = max(
                (
                    entity_context_similarity(wanted, actual)
                    for actual in fact_entities
                ),
                default=0.0,
            )
            if best < 0.82 and wanted in normalized_combined:
                # Legacy/unprojected entity evidence can still be recognized
                # from the raw fact text, but only by literal normalized span.
                best = 0.88
            if best < 0.82:
                return None
            entity_matches.append(best)
        entity_score = (
            sum(entity_matches) / len(entity_matches)
            if entity_matches
            else 0.5
        )

    object_score = (
        max(
            semantic_key_similarity(query.object_hint, projection.value),
            lexical_similarity(query.object_hint, projection.value),
        )
        if query.object_hint
        else 0.5
    )
    lexical = lexical_similarity(query.raw_text, combined)

    components = {
        "relation": relation,
        "subject": subject,
        "qualifier": qualifier,
        "object": object_score,
        "entity": entity_score,
        "lexical": lexical,
    }
    score = (
        relation * 0.46
        + subject * 0.12
        + qualifier * 0.18
        + object_score * 0.08
        + entity_score * 0.12
        + lexical * 0.04
    )
    score *= 0.75 + (projection.confidence * 0.25)
    return SemanticMemoryHit(
        fact=fact,
        score=max(0.0, min(score, 1.0)),
        components=components,
    )
