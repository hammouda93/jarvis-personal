"""Semantic indexing, retrieval and runtime adapter for Memory Core V5."""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .memory_core_store import MemoryCoreStore
from .memory_semantic_interpreter import SemanticMemoryInterpreter
from .semantic_memory import (
    MemoryProjection,
    MemoryQueryFrame,
    MemoryTurnInterpretation,
    SemanticFactRecord,
    SemanticMemoryHit,
    normalize_text,
    query_is_specific_enough,
    score_semantic_fact,
)


class SemanticMemoryEngine:
    def __init__(
        self,
        store: MemoryCoreStore,
        interpreter: SemanticMemoryInterpreter,
        *,
        min_score: float = 0.52,
        ambiguity_margin: float = 0.07,
        batch_size: int = 16,
    ) -> None:
        self.store = store
        self.interpreter = interpreter
        self.min_score = max(0.0, min(float(min_score), 1.0))
        self.ambiguity_margin = max(
            0.0,
            min(float(ambiguity_margin), 0.4),
        )
        self.batch_size = max(1, min(int(batch_size), 128))

    @property
    def parser_version(self) -> str:
        return str(self.interpreter.parser_version)

    def interpret_turn(self, text: str) -> MemoryTurnInterpretation:
        return self.interpreter.interpret_turn(text)

    def project_memory(
        self,
        memory_id: int,
        *,
        provenance: str,
    ) -> tuple[MemoryProjection, ...]:
        item = self.store.get_memory(memory_id)
        if item is None:
            raise KeyError(f"memory_not_found:{memory_id}")
        projected = self.interpreter.project_batch(
            [(item.id, item.content)]
        )
        facts = tuple(
            fact
            for fact in projected.get(item.id, ())
            if fact.confidence >= 0.55
        )
        self.store.save_projection(
            item.id,
            facts,
            parser_version=self.parser_version,
            provenance=provenance,
        )
        return tuple(facts)

    def ensure_indexed(
        self,
        *,
        log=None,
        max_items: int = 192,
    ) -> dict[str, int]:
        indexed = errors = unprojected = 0
        remaining = max(0, int(max_items))
        while remaining > 0:
            items = self.store.pending_projection_items(
                self.parser_version,
                limit=min(self.batch_size, remaining),
            )
            if not items:
                break
            request = [(item.id, item.content) for item in items]
            try:
                projections = self.interpreter.project_batch(request)
            except Exception as exc:
                for item in items:
                    self.store.mark_projection_error(
                        item.id,
                        parser_version=self.parser_version,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    errors += 1
                if log:
                    log(
                        "[SEMANTIC_MEMORY] indexing_error="
                        f"{type(exc).__name__}:{exc}"
                    )
                break

            for item in items:
                facts = tuple(
                    fact
                    for fact in projections.get(item.id, ())
                    if fact.confidence >= 0.55
                )
                try:
                    self.store.save_projection(
                        item.id,
                        facts,
                        parser_version=self.parser_version,
                        provenance="legacy",
                    )
                    if facts:
                        indexed += 1
                    else:
                        unprojected += 1
                except Exception as exc:
                    self.store.mark_projection_error(
                        item.id,
                        parser_version=self.parser_version,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    errors += 1
            remaining -= len(items)
            if len(items) < self.batch_size:
                break

        if log:
            log(
                "[SEMANTIC_MEMORY] indexed="
                f"{indexed} unprojected={unprojected} errors={errors}"
            )
        return {
            "indexed": indexed,
            "unprojected": unprojected,
            "errors": errors,
        }

    def _dedupe_hits(
        self,
        hits: list[SemanticMemoryHit],
    ) -> list[SemanticMemoryHit]:
        result = []
        seen = set()
        for hit in hits:
            key = hit.fact.projection.identity
            if key in seen:
                continue
            seen.add(key)
            result.append(hit)
        return result

    def _score_records(
        self,
        query: MemoryQueryFrame,
        records,
        *,
        limit: int = 12,
        session_priority: bool = False,
    ) -> list[SemanticMemoryHit]:
        hits = []
        for fact in records:
            hit = score_semantic_fact(query, fact)
            if hit is None or hit.score < self.min_score:
                continue
            if session_priority:
                components = dict(hit.components)
                components["session_priority"] = 0.05
                hit = SemanticMemoryHit(
                    fact=hit.fact,
                    score=min(1.0, hit.score + 0.05),
                    components=components,
                )
            hits.append(hit)
        hits.sort(
            key=lambda item: (
                item.score,
                item.fact.memory_id,
                item.fact.fact_id,
            ),
            reverse=True,
        )
        return self._dedupe_hits(hits)[: max(1, min(int(limit), 50))]

    def search(
        self,
        query: MemoryQueryFrame,
        *,
        log=None,
        limit: int = 12,
    ) -> list[SemanticMemoryHit]:
        self.ensure_indexed(log=log)
        status = None if query.answer_mode == "timeline" else "active"
        hits = self._score_records(
            query,
            self.store.semantic_facts(
                scope=query.scope,
                status=status,
            ),
            limit=limit,
        )
        if log:
            summary = [
                {
                    "memory_id": hit.fact.memory_id,
                    "relation": hit.fact.projection.relation,
                    "value": hit.fact.projection.value,
                    "score": round(hit.score, 3),
                    "components": {
                        key: round(value, 3)
                        for key, value in hit.components.items()
                    },
                }
                for hit in hits[:8]
            ]
            log(
                "[SEMANTIC_MEMORY] candidates="
                + json.dumps(summary, ensure_ascii=False)
            )
        return hits

    def resolve(
        self,
        query: MemoryQueryFrame,
        *,
        log=None,
        session_facts=(),
    ) -> dict[str, Any]:
        if query.answer_mode == "inspect":
            return {
                "status": "inspect",
                "items": [
                    asdict(item)
                    for item in self.store.recent_memories(limit=30)
                ],
            }

        if not query_is_specific_enough(query):
            return {"status": "underspecified", "hits": []}

        session_hits = self._score_records(
            query,
            session_facts,
            limit=20,
            session_priority=True,
        )
        persistent_hits = self.search(query, log=log)

        if query.answer_mode == "single" and session_hits:
            hits = session_hits
        else:
            hits = self._dedupe_hits(
                [*session_hits, *persistent_hits]
            )
            hits.sort(
                key=lambda item: (
                    item.score,
                    item.fact.memory_id,
                    item.fact.fact_id,
                ),
                reverse=True,
            )

        if not hits:
            return {"status": "missing", "hits": []}

        if query.answer_mode in {"collection", "timeline"}:
            selected = []
            top = hits[0].score
            for hit in hits:
                if hit.score + 0.12 < top:
                    continue
                selected.append(hit)
            if query.answer_mode == "timeline":
                selected.sort(
                    key=lambda item: (
                        item.fact.created_at,
                        item.fact.memory_id,
                    )
                )
            return {
                "status": "resolved",
                "mode": query.answer_mode,
                "hits": selected,
            }

        top = hits[0]
        if len(hits) > 1:
            second = hits[1]
            same_relation = (
                top.fact.projection.relation
                == second.fact.projection.relation
            )
            different_value = (
                normalize_text(top.fact.projection.value)
                != normalize_text(second.fact.projection.value)
            )
            if (
                second.score >= top.score - self.ambiguity_margin
                and (
                    not same_relation
                    or different_value
                )
            ):
                return {
                    "status": "ambiguous",
                    "hits": hits[:4],
                }

        return {
            "status": "resolved",
            "mode": "single",
            "hits": [top],
        }

    def refine(
        self,
        previous: MemoryQueryFrame,
        clarification: str,
    ) -> MemoryQueryFrame:
        return self.interpreter.refine_query(
            previous,
            clarification,
        )


def _memory_id_from_detail(detail: str) -> int | None:
    match = re.search(r"\bmemory_id=(\d+)\b", str(detail or ""))
    return int(match.group(1)) if match else None


def _distinct_values(hits: list[SemanticMemoryHit]) -> list[str]:
    result = []
    seen = set()
    for hit in hits:
        value = hit.fact.projection.value.strip()
        key = normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


class SemanticMemoryRuntime:
    """Memory operation interpreter placed before the conversational agent."""

    def __init__(
        self,
        delegate,
        tools,
        engine: SemanticMemoryEngine,
        *,
        connector_resolver=None,
    ) -> None:
        self.delegate = delegate
        self.tools = tools
        self.engine = engine
        self.connector_resolver = connector_resolver
        self._pending_query: MemoryQueryFrame | None = None
        self._session_facts: list[SemanticFactRecord] = []
        self._session_sequence = 0

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def reset(self) -> None:
        self._pending_query = None
        self._session_facts.clear()
        self._session_sequence = 0
        self.delegate.reset()

    def warm_up(self, *, log=None) -> None:
        # Indexing is intentionally lazy. Startup must not block on semantic
        # migration of legacy memories.
        self.delegate.warm_up(log=log)

    def record_external_turn(
        self,
        user_text,
        assistant_text,
        **kwargs,
    ) -> None:
        method = getattr(self.delegate, "record_external_turn", None)
        if method:
            method(user_text, assistant_text, **kwargs)

    def _record_session_facts(
        self,
        facts: tuple[MemoryProjection, ...],
        raw_text: str,
    ) -> None:
        for projection in facts:
            if projection.confidence < 0.55:
                continue
            if projection.cardinality == "single":
                self._session_facts = [
                    existing
                    for existing in self._session_facts
                    if not (
                        existing.projection.subject == projection.subject
                        and existing.projection.relation == projection.relation
                        and existing.projection.scope == projection.scope
                        and existing.projection.qualifiers
                        == projection.qualifiers
                    )
                ]
            if any(
                existing.projection.identity == projection.identity
                for existing in self._session_facts
            ):
                continue
            self._session_sequence += 1
            self._session_facts.append(
                SemanticFactRecord(
                    fact_id=-self._session_sequence,
                    memory_id=-self._session_sequence,
                    ordinal=0,
                    projection=projection,
                    provenance="session",
                    parser_version=self.engine.parser_version,
                    status="active",
                    raw_content=str(raw_text or ""),
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        self._session_facts = self._session_facts[-64:]

    @staticmethod
    def _memory_action(name: str, payload: dict[str, Any]):
        from .native_tools import AgentActionResult

        return AgentActionResult(
            name=name,
            success=True,
            message="Memory Core V5",
            detail=json.dumps(payload, ensure_ascii=False),
        )

    def _result(self, text: str, actions=()):
        from .agent_runtime import AgentTurnResult

        return AgentTurnResult(
            text=text,
            actions=tuple(actions),
        )

    @staticmethod
    def _inspection_reply(items: list[dict[str, Any]]) -> str:
        if not items:
            return "Je n'ai aucune information durable en mémoire locale."
        lines = []
        seen = set()
        for item in items:
            content = str(item.get("content") or "").strip()
            key = normalize_text(content)
            if not content or key in seen:
                continue
            seen.add(key)
            lines.append(content)
            if len(lines) >= 20:
                break
        if not lines:
            return "Je n'ai aucune information durable en mémoire locale."
        return "Mémoire locale : " + " ; ".join(lines)

    @staticmethod
    def _reply_from_resolution(
        resolution: dict[str, Any],
    ) -> tuple[str, bool]:
        status = resolution.get("status")
        hits = list(resolution.get("hits") or [])
        if status in {"missing", "underspecified"}:
            return (
                "Je n'ai pas assez de contexte pour identifier le souvenir. "
                "Pouvez-vous préciser ce que vous cherchez ?",
                True,
            )
        if status == "ambiguous":
            choices = _distinct_values(hits)
            detail = " ; ".join(choices[:4])
            return (
                "J'ai plusieurs souvenirs plausibles"
                + (f" : {detail}" if detail else "")
                + ". Lequel voulez-vous préciser ?",
                True,
            )
        values = _distinct_values(hits)
        if not values:
            return (
                "J'ai retrouvé le souvenir, mais sa valeur sémantique "
                "n'est pas exploitable.",
                False,
            )
        mode = resolution.get("mode")
        if mode in {"collection", "timeline"}:
            return " ; ".join(values), False
        return values[0], False

    def run(self, user_text, *, log=None, phase=None):
        begin = getattr(self.tools, "begin_turn", None)
        if begin:
            begin(user_text)

        if self._pending_query is not None:
            try:
                refined = self.engine.refine(
                    self._pending_query,
                    user_text,
                )
                resolution = self.engine.resolve(
                    refined,
                    log=log,
                    session_facts=self._session_facts,
                )
                reply, pending = self._reply_from_resolution(resolution)
                self._pending_query = refined if pending else None
                if log:
                    log(
                        "[MEMORY_V5] route=clarification "
                        f"relation={refined.relation} "
                        f"status={resolution.get('status')}"
                    )
                return self._result(reply)
            except Exception as exc:
                if log:
                    log(
                        "[MEMORY_V5] clarification_error="
                        f"{type(exc).__name__}:{exc}"
                    )
                self._pending_query = None

        try:
            intent = self.engine.interpret_turn(user_text)
        except Exception as exc:
            if log:
                log(
                    "[MEMORY_V5] interpreter_error="
                    f"{type(exc).__name__}:{exc}"
                )
            # Semantic memory is fail-open for ordinary conversation: if the
            # semantic classifier is unavailable, do not fabricate memory.
            return self.delegate.run(
                user_text,
                log=log,
                phase=phase,
            )

        if log:
            log(
                "[MEMORY_V5] operation="
                f"{intent.operation} confidence={intent.confidence:.3f} "
                f"reason={intent.reason}"
            )

        if intent.session_facts and intent.operation in {"pass", "write"}:
            self._record_session_facts(
                intent.session_facts,
                user_text,
            )
            if log:
                log(
                    "[MEMORY_V5] session_facts="
                    f"{len(intent.session_facts)}"
                )

        if intent.operation == "pass" or intent.confidence < 0.55:
            return self.delegate.run(
                user_text,
                log=log,
                phase=phase,
            )

        if phase:
            phase("acting")

        if intent.operation == "inspect":
            resolution = {
                "status": "inspect",
                "items": [
                    asdict(item)
                    for item in self.engine.store.recent_memories(limit=30)
                ],
            }
            action = self._memory_action(
                "semantic_memory_inspect",
                {
                    "count": len(resolution["items"]),
                    "source": "persistent",
                },
            )
            return self._result(
                self._inspection_reply(resolution["items"]),
                (action,),
            )

        if intent.operation == "write":
            raw = intent.write_text.strip()
            if not raw:
                return self._result(
                    "Quelle information exacte souhaitez-vous mémoriser ?"
                )
            authorize = getattr(
                self.tools,
                "authorize_semantic_memory_write",
                None,
            )
            if authorize:
                authorize(user_text)
            action = self.tools.execute(
                "remember_information",
                {"content": raw},
            )
            if not action.success:
                return self._result(
                    "La mémorisation a échoué : " + action.message,
                    (action,),
                )
            memory_id = _memory_id_from_detail(action.detail)
            if memory_id is not None:
                try:
                    facts = self.engine.project_memory(
                        memory_id,
                        provenance="explicit",
                    )
                    if log:
                        log(
                            "[MEMORY_V5] write_memory_id="
                            f"{memory_id} projected_facts={len(facts)}"
                        )
                except Exception as exc:
                    self.engine.store.mark_projection_error(
                        memory_id,
                        parser_version=self.engine.parser_version,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    if log:
                        log(
                            "[MEMORY_V5] write_projection_error="
                            f"{type(exc).__name__}:{exc}"
                        )
            self._pending_query = None
            return self._result(
                action.message,
                (action,),
            )

        query = intent.query
        if query is None:
            return self.delegate.run(
                user_text,
                log=log,
                phase=phase,
            )

        try:
            resolution = self.engine.resolve(
                query,
                log=log,
                session_facts=self._session_facts,
            )
        except Exception as exc:
            if log:
                log(
                    "[MEMORY_V5] recall_error="
                    f"{type(exc).__name__}:{exc}"
                )
            return self._result(
                "La mémoire sémantique n'est pas disponible pour le moment."
            )

        if resolution.get("status") == "missing" and self.connector_resolver:
            try:
                external = list(self.connector_resolver(user_text) or [])
            except Exception:
                external = []
            if external:
                action = self._memory_action(
                    "semantic_memory_connector_recall",
                    {
                        "source": "connector",
                        "count": len(external[:8]),
                    },
                )
                return self._result(
                    " ; ".join(str(item) for item in external[:8]),
                    (action,),
                )

        reply, pending = self._reply_from_resolution(resolution)
        self._pending_query = query if pending else None
        if log:
            log(
                "[MEMORY_V5] recall_status="
                f"{resolution.get('status')} relation={query.relation} "
                f"mode={query.answer_mode}"
            )
        hit_payload = []
        for hit in list(resolution.get("hits") or [])[:8]:
            hit_payload.append(
                {
                    "memory_id": hit.fact.memory_id,
                    "fact_id": hit.fact.fact_id,
                    "relation": hit.fact.projection.relation,
                    "provenance": hit.fact.provenance,
                    "score": round(hit.score, 4),
                }
            )
        action = self._memory_action(
            "semantic_memory_recall",
            {
                "status": resolution.get("status"),
                "relation": query.relation,
                "scope": query.scope,
                "answer_mode": query.answer_mode,
                "hits": hit_payload,
            },
        )
        return self._result(reply, (action,))
