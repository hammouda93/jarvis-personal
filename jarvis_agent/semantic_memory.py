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
OPERATIONS = {"write", "recall", "inspect", "pass"}


def normalize_text(value: str) -> str:
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def normalize_key(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", normalize_text(value))
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
                qualifiers,
            )
        )


@dataclass(frozen=True)
class MemoryQueryFrame:
    subject: str = "user"
    relation: str = ""
    object_hint: str = ""
    qualifiers: dict[str, str] = field(default_factory=dict)
    scope: str = "global"
    answer_mode: str = "single"
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
            subject=normalize_key(str(data.get("subject") or "user")) or "user",
            relation=normalize_key(str(data.get("relation") or "")),
            object_hint=str(data.get("object_hint") or "").strip()[:1000],
            qualifiers=_clean_qualifiers(data.get("qualifiers")),
            scope=normalize_key(str(data.get("scope") or "global")) or "global",
            answer_mode=answer_mode,
            exact_terms=exact_terms,
            raw_text=str(raw_text or "")[:2000],
            confidence=max(0.0, min(confidence, 1.0)),
        )


@dataclass(frozen=True)
class MemoryTurnInterpretation:
    operation: str
    write_text: str = ""
    query: MemoryQueryFrame | None = None
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
        if operation == "recall":
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
        return cls(
            operation=operation,
            write_text=str(payload.get("write_text") or "").strip()[:4000],
            query=query,
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
        else:
            scores.append(semantic_key_similarity(wanted_norm, actual_norm))
    return sum(scores) / max(1, len(scores))


def score_semantic_fact(
    query: MemoryQueryFrame,
    fact: SemanticFactRecord,
) -> SemanticMemoryHit | None:
    projection = fact.projection

    if query.scope not in {"", "global"}:
        if projection.scope not in {query.scope, "global"}:
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
        "lexical": lexical,
    }
    score = (
        relation * 0.46
        + subject * 0.12
        + qualifier * 0.18
        + object_score * 0.10
        + lexical * 0.14
    )
    score *= 0.75 + (projection.confidence * 0.25)
    return SemanticMemoryHit(
        fact=fact,
        score=max(0.0, min(score, 1.0)),
        components=components,
    )
