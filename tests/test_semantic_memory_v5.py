from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_agent.agent_runtime import AgentTurnResult
from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.memory_core_store import MemoryCoreStore
from jarvis_agent.memory_semantic_interpreter import (
    ModelSemanticMemoryInterpreter,
    SemanticMemoryInterpreter,
)
from jarvis_agent.semantic_memory import (
    MemoryProjection,
    MemoryQueryFrame,
    MemoryTurnInterpretation,
)
from jarvis_agent.semantic_memory_runtime import (
    SemanticMemoryEngine,
    SemanticMemoryRuntime,
)


def projection(
    relation,
    value,
    *,
    subject="user",
    kind="fact",
    qualifiers=None,
    entities=None,
    scope="global",
    cardinality="unknown",
    confidence=0.98,
):
    return MemoryProjection(
        subject=subject,
        relation=relation,
        value=value,
        kind=kind,
        qualifiers=dict(qualifiers or {}),
        entities=tuple(entities or ()),
        scope=scope,
        cardinality=cardinality,
        confidence=confidence,
    )


class FixtureInterpreter(SemanticMemoryInterpreter):
    parser_version = "fixture-semantic-v1"

    def __init__(
        self,
        *,
        turns=None,
        projections=None,
        refinements=None,
        alignments=None,
    ):
        self.turns = dict(turns or {})
        self.projections = dict(projections or {})
        self.refinements = dict(refinements or {})
        self.alignments = dict(alignments or {})
        self.project_calls = 0
        self.turn_calls = 0
        self.project_catalogs = []

    def interpret_turn(self, user_text):
        self.turn_calls += 1
        return self.turns.get(
            user_text,
            MemoryTurnInterpretation(
                operation="pass",
                confidence=0.99,
                reason="fixture_pass",
            ),
        )

    def project_batch(self, items, *, relation_catalog=()):
        self.project_calls += 1
        self.project_catalogs.append(tuple(relation_catalog))
        result = {}
        for item in items:
            memory_id, text = item[0], item[1]
            result[memory_id] = tuple(
                self.projections.get(text, ())
            )
        return result

    def refine_query(self, previous, clarification):
        return self.refinements.get(
            (previous.relation, clarification),
            previous,
        )

    def align_query_relation(self, query, relation_catalog):
        target = self.alignments.get(query.relation)
        if not target or target not in set(relation_catalog):
            return query
        return MemoryQueryFrame(
            subject=query.subject,
            relation=target,
            object_hint=query.object_hint,
            qualifiers=query.qualifiers,
            entities=query.entities,
            scope=query.scope,
            answer_mode=query.answer_mode,
            exact_terms=query.exact_terms,
            raw_text=query.raw_text,
            confidence=query.confidence,
        )


class NoLLM:
    def __init__(self):
        self.calls = 0
        self.external_turns = []

    def run(self, user_text, *, log=None, phase=None):
        self.calls += 1
        return AgentTurnResult(
            text="delegate:" + user_text,
            actions=(),
        )

    def reset(self):
        return None

    def warm_up(self, *, log=None):
        return None

    def record_external_turn(self, user_text, assistant_text, **kwargs):
        self.external_turns.append(
            (user_text, assistant_text, dict(kwargs))
        )


class ToolSchemaDelegate:
    def ollama_tools(self):
        def tool(name):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            }

        return [
            tool("remember_information"),
            tool("recall_information"),
            tool("open_application"),
        ]

    @staticmethod
    def _ollama(name, description, properties, required):
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


class SemanticStructuredOutputTests(unittest.TestCase):
    def test_cloud_semantic_calls_request_native_json_mode(self):
        class OptionsInterpreter(ModelSemanticMemoryInterpreter):
            def _model_for(self, provider):
                return "openai/gpt-oss-120b"

        interpreter = OptionsInterpreter(
            provider="cerebras",
            allow_cloud=True,
        )

        cerebras = interpreter._structured_output_options("cerebras")
        groq = interpreter._structured_output_options("groq")

        self.assertEqual(
            cerebras["response_format"],
            {"type": "json_object"},
        )
        self.assertNotIn("extra_body", cerebras)
        self.assertEqual(
            groq["response_format"],
            {"type": "json_object"},
        )
        self.assertEqual(
            groq["extra_body"]["reasoning_format"],
            "hidden",
        )
        self.assertEqual(
            groq["extra_body"]["reasoning_effort"],
            "low",
        )

    def test_truncated_structured_output_fails_closed_before_json_parse(self):
        import sys
        from types import SimpleNamespace

        class FakeCompletions:
            def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="length",
                            message=SimpleNamespace(
                                content='{"items":['
                            ),
                        )
                    ]
                )

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(
                    completions=FakeCompletions()
                )

        class TruncationInterpreter(ModelSemanticMemoryInterpreter):
            @staticmethod
            def _provider_config(provider):
                return "https://example.invalid/v1", "test-key"

        fake_openai = SimpleNamespace(OpenAI=FakeClient)
        interpreter = TruncationInterpreter(
            provider="cerebras",
            model="gpt-oss-120b",
            allow_cloud=True,
        )

        with patch.dict(sys.modules, {"openai": fake_openai}):
            with self.assertRaisesRegex(
                RuntimeError,
                "semantic_memory_structured_output_truncated",
            ):
                interpreter._chat_openai_compatible(
                    "cerebras",
                    "Return JSON only.",
                    "{}",
                )

    def test_invalid_primary_json_falls_back_without_silent_repair(self):
        class FallbackInterpreter(ModelSemanticMemoryInterpreter):
            def _provider_chain(self):
                return ("cerebras", "groq")

            def _model_for(self, provider):
                return "gpt-oss-120b"

            def _chat_openai_compatible(
                self,
                provider,
                system,
                user,
            ):
                if provider == "cerebras":
                    return '{"operation":"pass"'
                return json.dumps(
                    {
                        "operation": "pass",
                        "write_text": "",
                        "query": None,
                        "session_facts": [],
                        "confidence": 0.99,
                        "reason": "ordinary conversation",
                    }
                )

        interpreter = FallbackInterpreter(
            provider="cerebras",
            allow_cloud=True,
        )
        result = interpreter.interpret_turn("hello")

        self.assertEqual(result.operation, "pass")
        self.assertEqual(interpreter.last_provider, "groq")
        self.assertEqual(
            interpreter.last_attempts,
            ("cerebras", "groq"),
        )


class SemanticProviderFallbackTests(unittest.TestCase):
    def test_cerebras_failure_falls_back_to_groq_with_provider_native_model(self):
        class FallbackInterpreter(ModelSemanticMemoryInterpreter):
            def _provider_chain(self):
                return ("cerebras", "groq")

            def _model_for(self, provider):
                return {
                    "cerebras": "cerebras-model",
                    "groq": "groq-model",
                }[provider]

            def _chat_openai_compatible(
                self,
                provider,
                system,
                user,
            ):
                if provider == "cerebras":
                    raise RuntimeError("primary unavailable")
                return json.dumps(
                    {
                        "operation": "pass",
                        "write_text": "",
                        "query": None,
                        "session_facts": [],
                        "confidence": 0.99,
                        "reason": "ordinary conversation",
                    }
                )

        interpreter = FallbackInterpreter(
            provider="cerebras",
            model="cerebras-model",
            allow_cloud=True,
        )
        result = interpreter.interpret_turn("hello")

        self.assertEqual(result.operation, "pass")
        self.assertEqual(interpreter.last_provider, "groq")
        self.assertEqual(interpreter.last_model, "groq-model")
        self.assertEqual(
            interpreter.last_attempts,
            ("cerebras", "groq"),
        )

    def test_all_semantic_providers_failure_is_explicit_and_traceable(self):
        class FailedInterpreter(ModelSemanticMemoryInterpreter):
            def _provider_chain(self):
                return ("cerebras", "groq")

            def _chat_openai_compatible(
                self,
                provider,
                system,
                user,
            ):
                raise RuntimeError(provider + " down")

        interpreter = FailedInterpreter(
            provider="cerebras",
            allow_cloud=True,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "semantic_memory_all_providers_failed",
        ):
            interpreter.interpret_turn("hello")

        self.assertEqual(interpreter.last_provider, "")
        self.assertEqual(
            interpreter.last_attempts,
            ("cerebras", "groq"),
        )


class SemanticMemoryPrivacyTests(unittest.TestCase):
    def test_cloud_semantic_provider_requires_explicit_permission(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "semantic_memory_cloud_provider_requires_explicit_opt_in",
        ):
            ModelSemanticMemoryInterpreter(
                provider="cerebras",
                allow_cloud=False,
            )

    def test_explicit_cloud_permission_allows_cloud_provider_construction(self):
        interpreter = ModelSemanticMemoryInterpreter(
            provider="cerebras",
            allow_cloud=True,
        )

        self.assertEqual(interpreter.provider, "cerebras")
        self.assertTrue(interpreter.allow_cloud)

    def test_auto_cloud_agent_becomes_local_semantic_provider_without_opt_in(self):
        from dataclasses import replace
        from jarvis_agent.config import settings as real_settings

        with patch(
            "jarvis_agent.memory_semantic_interpreter.settings",
            replace(real_settings, agent_provider="cerebras"),
        ):
            interpreter = ModelSemanticMemoryInterpreter(
                provider="auto",
                allow_cloud=False,
            )

        self.assertEqual(interpreter.provider, "ollama")
        self.assertFalse(interpreter.allow_cloud)


class SemanticMemoryStoreTests(unittest.TestCase):
    def test_sidecar_preserves_raw_memory_and_supports_multiple_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            item = store.remember("compound raw evidence")
            store.save_projection(
                item.id,
                (
                    projection("project_owner", "Alice"),
                    projection(
                        "project_deadline",
                        "2026-11-01",
                        kind="event",
                        qualifiers={"project": "Atlas"},
                    ),
                ),
                parser_version="test-v1",
                provenance="legacy",
            )

            self.assertEqual(
                store.get_memory(item.id).content,
                "compound raw evidence",
            )
            facts = store.semantic_facts()
            self.assertEqual(len(facts), 2)
            self.assertEqual(
                {fact.projection.relation for fact in facts},
                {"project_owner", "project_deadline"},
            )

    def test_existing_sidecar_schema_is_migrated_with_entities_column(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "memory.sqlite3"
            conn = sqlite3.connect(path)
            try:
                conn.execute(
                    """
                    CREATE TABLE memories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        tags TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE memory_semantic_facts (
                        fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        memory_id INTEGER NOT NULL,
                        ordinal INTEGER NOT NULL,
                        subject TEXT NOT NULL,
                        relation TEXT NOT NULL,
                        object_value TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        qualifiers_json TEXT NOT NULL DEFAULT '{}',
                        scope TEXT NOT NULL DEFAULT 'global',
                        cardinality TEXT NOT NULL DEFAULT 'unknown',
                        confidence REAL NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'active',
                        parser_version TEXT NOT NULL,
                        provenance TEXT NOT NULL DEFAULT 'legacy',
                        raw_hash TEXT NOT NULL,
                        projected_at TEXT NOT NULL,
                        UNIQUE(memory_id, ordinal, parser_version)
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            store = MemoryCoreStore(path)
            with store._connect() as check:
                columns = {
                    row[1]
                    for row in check.execute(
                        "PRAGMA table_info(memory_semantic_facts)"
                    ).fetchall()
                }

            self.assertIn("entities_json", columns)

    def test_semantic_sidecar_survives_process_style_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "memory.sqlite3"
            first = MemoryCoreStore(path)
            item = first.remember("raw preference")
            first.save_projection(
                item.id,
                (
                    projection(
                        "preferred_language",
                        "French",
                        kind="preference",
                        cardinality="single",
                    ),
                ),
                parser_version="test-v1",
                provenance="explicit",
            )

            second = MemoryCoreStore(path)
            facts = second.semantic_facts()

            self.assertEqual(len(facts), 1)
            self.assertEqual(
                facts[0].projection.relation,
                "preferred_language",
            )
            self.assertEqual(facts[0].projection.value, "French")


class SemanticMemoryMaintenanceTests(unittest.TestCase):
    def test_semantic_summary_reports_distinct_active_entity_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            one = store.remember("entity one")
            two = store.remember("entity two")
            store.save_projection(
                one.id,
                (
                    projection(
                        "project_owner",
                        "Alice",
                        entities=("Project Atlas", "Alice"),
                    ),
                ),
                parser_version="test-v1",
                provenance="legacy",
            )
            store.save_projection(
                two.id,
                (
                    projection(
                        "project_deadline",
                        "2026-11-01",
                        entities=("Project Atlas",),
                    ),
                ),
                parser_version="test-v1",
                provenance="legacy",
            )

            summary = store.semantic_summary()

            self.assertEqual(summary["active_entities"], 2)

    def test_force_reindex_rebuilds_only_sidecar_and_preserves_raw_rows(self):
        from jarvis_agent.semantic_memory_cli import (
            raw_snapshot,
            reindex_semantic,
        )

        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            first = store.remember("evidence alpha", tags="one")
            second = store.remember("evidence beta", tags="two")
            before = raw_snapshot(store)

            old = projection(
                "temporary_relation",
                "old",
                confidence=0.99,
            )
            store.save_projection(
                first.id,
                (old,),
                parser_version="old-parser",
                provenance="legacy",
            )

            interpreter = FixtureInterpreter(
                projections={
                    "evidence alpha": (
                        projection(
                            "project_owner",
                            "Alice",
                            kind="project",
                        ),
                    ),
                    "evidence beta": (
                        projection(
                            "project_budget",
                            "25000",
                            kind="project",
                            qualifiers={"currency": "USD"},
                        ),
                    ),
                }
            )
            result = reindex_semantic(
                store,
                interpreter,
                force=True,
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["raw_memory_unchanged"])
            self.assertEqual(raw_snapshot(store), before)
            self.assertEqual(
                {item.id for item in store.recent_memories(limit=10)},
                {first.id, second.id},
            )
            relations = {
                fact.projection.relation
                for fact in store.semantic_facts()
            }
            self.assertEqual(
                relations,
                {"project_owner", "project_budget"},
            )
            self.assertEqual(
                result["after"]["raw_memories"],
                2,
            )
            self.assertEqual(
                result["after"]["semantic_facts"],
                2,
            )

    def test_reindex_rebuilds_single_value_supersession_from_admission_history(self):
        from jarvis_agent.semantic_memory_cli import (
            raw_snapshot,
            reindex_semantic,
        )

        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            old = store.remember("preference old raw")
            new = store.remember("preference new raw")

            store.save_projection(
                old.id,
                (
                    projection(
                        "preferred_editor",
                        "VS Code",
                        kind="preference",
                        cardinality="single",
                        confidence=0.99,
                    ),
                ),
                parser_version="fixture-semantic-v1",
                provenance="explicit",
            )
            store.save_projection(
                new.id,
                (
                    projection(
                        "preferred_editor",
                        "Cursor",
                        kind="preference",
                        cardinality="single",
                        confidence=0.99,
                    ),
                ),
                parser_version="fixture-semantic-v1",
                provenance="explicit",
            )
            before = raw_snapshot(store)

            store.reset_semantic_index()

            self.assertEqual(
                store.memory_provenance(old.id),
                "explicit",
            )
            self.assertEqual(
                store.memory_provenance(new.id),
                "explicit",
            )

            interpreter = FixtureInterpreter(
                projections={
                    "preference old raw": (
                        projection(
                            "preferred_editor",
                            "VS Code",
                            kind="preference",
                            cardinality="single",
                            confidence=0.99,
                        ),
                    ),
                    "preference new raw": (
                        projection(
                            "preferred_editor",
                            "Cursor",
                            kind="preference",
                            cardinality="single",
                            confidence=0.99,
                        ),
                    ),
                }
            )
            result = reindex_semantic(
                store,
                interpreter,
            )

            self.assertTrue(result["raw_memory_unchanged"])
            self.assertEqual(raw_snapshot(store), before)
            statuses = {
                fact.projection.value: fact.status
                for fact in store.semantic_facts(status=None)
            }
            self.assertEqual(statuses["VS Code"], "superseded")
            self.assertEqual(statuses["Cursor"], "active")
            self.assertEqual(
                result["after"]["admissions"]["explicit"],
                2,
            )

    def test_retry_errors_reprojects_only_failed_sidecar_rows(self):
        from jarvis_agent.semantic_memory_cli import reindex_semantic

        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            good = store.remember("good evidence")
            failed = store.remember("failed evidence")
            store.save_projection(
                good.id,
                (
                    projection(
                        "stable_fact",
                        "kept",
                    ),
                ),
                parser_version="fixture-semantic-v1",
                provenance="legacy",
            )
            store.mark_projection_error(
                failed.id,
                parser_version="fixture-semantic-v1",
                error="temporary",
            )

            interpreter = FixtureInterpreter(
                projections={
                    "failed evidence": (
                        projection(
                            "recovered_fact",
                            "recovered",
                        ),
                    ),
                }
            )
            result = reindex_semantic(
                store,
                interpreter,
                retry_errors=True,
            )

            self.assertTrue(result["ok"])
            facts = {
                fact.projection.relation: fact.projection.value
                for fact in store.semantic_facts()
            }
            self.assertEqual(facts["stable_fact"], "kept")
            self.assertEqual(facts["recovered_fact"], "recovered")

    def test_semantic_inspection_includes_history_without_touching_raw_memory(self):
        from jarvis_agent.semantic_memory_cli import (
            inspect_semantic,
            raw_snapshot,
        )

        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            old = store.remember("old raw")
            new = store.remember("new raw")
            store.save_projection(
                old.id,
                (
                    projection(
                        "preferred_language",
                        "English",
                        kind="preference",
                        cardinality="single",
                        confidence=0.99,
                    ),
                ),
                parser_version="test-v1",
                provenance="explicit",
            )
            store.save_projection(
                new.id,
                (
                    projection(
                        "preferred_language",
                        "French",
                        kind="preference",
                        cardinality="single",
                        confidence=0.99,
                    ),
                ),
                parser_version="test-v1",
                provenance="explicit",
            )
            before = raw_snapshot(store)

            payload = inspect_semantic(
                store,
                include_history=True,
            )

            self.assertEqual(raw_snapshot(store), before)
            statuses = {
                row["value"]: row["status"]
                for row in payload["facts"]
            }
            self.assertEqual(statuses["English"], "superseded")
            self.assertEqual(statuses["French"], "active")


class SemanticMemoryRetrievalTests(unittest.TestCase):
    def make_engine(self, raw_to_facts):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = MemoryCoreStore(
            Path(self.temp.name) / "memory.sqlite3"
        )
        interpreter = FixtureInterpreter(projections=raw_to_facts)
        return (
            store,
            interpreter,
            SemanticMemoryEngine(
                store,
                interpreter,
                min_score=0.45,
            ),
        )

    def test_shared_topic_words_do_not_merge_distinct_relations(self):
        store, _, engine = self.make_engine(
            {
                "reference A": (
                    projection(
                        "test_reference",
                        "Arrival",
                        cardinality="single",
                    ),
                ),
                "watch intention": (
                    projection(
                        "wants_to_watch",
                        "Inception",
                        kind="intention",
                        cardinality="collection",
                    ),
                ),
            }
        )
        store.remember("reference A")
        store.remember("watch intention")

        single = engine.resolve(
            MemoryQueryFrame(
                relation="test_reference",
                answer_mode="single",
                raw_text="unrelated wording",
                confidence=0.99,
            )
        )
        collection = engine.resolve(
            MemoryQueryFrame(
                relation="wants_to_watch",
                answer_mode="collection",
                raw_text="different paraphrase",
                confidence=0.99,
            )
        )

        self.assertEqual(
            single["hits"][0].fact.projection.value,
            "Arrival",
        )
        self.assertEqual(
            [
                hit.fact.projection.value
                for hit in collection["hits"]
            ],
            ["Inception"],
        )

    def test_entity_context_disambiguates_same_relation_without_domain_rules(self):
        store, _, engine = self.make_engine(
            {
                "north owner": (
                    projection(
                        "project_owner",
                        "Alice",
                        kind="project",
                        entities=("Project North", "Alice"),
                        cardinality="single",
                    ),
                ),
                "south owner": (
                    projection(
                        "project_owner",
                        "Bob",
                        kind="project",
                        entities=("Project South", "Bob"),
                        cardinality="single",
                    ),
                ),
            }
        )
        store.remember("north owner")
        store.remember("south owner")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="project_owner",
                entities=("Project North",),
                answer_mode="single",
                raw_text="who owns Project North",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            result["hits"][0].fact.projection.value,
            "Alice",
        )
        self.assertGreater(
            result["hits"][0].components["entity"],
            0.9,
        )

    def test_entity_alias_surface_can_match_same_context_without_exact_string(self):
        store, _, engine = self.make_engine(
            {
                "atlas office": (
                    projection(
                        "office_location",
                        "Tunis",
                        entities=("Project Atlas", "Tunis"),
                        cardinality="single",
                    ),
                ),
                "beta office": (
                    projection(
                        "office_location",
                        "Sfax",
                        entities=("Project Beta", "Sfax"),
                        cardinality="single",
                    ),
                ),
            }
        )
        store.remember("atlas office")
        store.remember("beta office")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="office_location",
                entities=("Atlas",),
                answer_mode="single",
                raw_text="where is Atlas based",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            result["hits"][0].fact.projection.value,
            "Tunis",
        )
        self.assertNotIn(
            "Sfax",
            [
                hit.fact.projection.value
                for hit in result["hits"]
            ],
        )

    def test_same_single_relation_different_entities_do_not_supersede_each_other(self):
        store, _, engine = self.make_engine({})
        first = store.remember("north owner")
        store.save_projection(
            first.id,
            (
                projection(
                    "project_owner",
                    "Alice",
                    entities=("Project North",),
                    cardinality="single",
                    confidence=0.99,
                ),
            ),
            parser_version=engine.parser_version,
            provenance="explicit",
        )
        second = store.remember("south owner")
        store.save_projection(
            second.id,
            (
                projection(
                    "project_owner",
                    "Bob",
                    entities=("Project South",),
                    cardinality="single",
                    confidence=0.99,
                ),
            ),
            parser_version=engine.parser_version,
            provenance="explicit",
        )

        active = store.semantic_facts(status="active")

        self.assertEqual(
            {fact.projection.value for fact in active},
            {"Alice", "Bob"},
        )

    def test_entity_order_does_not_change_singleton_slot_identity(self):
        store, _, engine = self.make_engine({})
        first = store.remember("old atlas office")
        store.save_projection(
            first.id,
            (
                projection(
                    "office_location",
                    "Tunis",
                    entities=("Project Atlas", "HQ"),
                    cardinality="single",
                    confidence=0.99,
                ),
            ),
            parser_version=engine.parser_version,
            provenance="explicit",
        )
        second = store.remember("new atlas office")
        store.save_projection(
            second.id,
            (
                projection(
                    "office_location",
                    "Sfax",
                    entities=("HQ", "Project Atlas"),
                    cardinality="single",
                    confidence=0.99,
                ),
            ),
            parser_version=engine.parser_version,
            provenance="explicit",
        )

        statuses = {
            fact.projection.value: fact.status
            for fact in store.semantic_facts(status=None)
        }

        self.assertEqual(statuses["Tunis"], "superseded")
        self.assertEqual(statuses["Sfax"], "active")

    def test_entity_context_survives_sidecar_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "memory.sqlite3"
            first = MemoryCoreStore(path)
            item = first.remember("entity-bearing raw")
            first.save_projection(
                item.id,
                (
                    projection(
                        "office_location",
                        "Tunis",
                        entities=("Project Atlas", "Tunis"),
                        cardinality="single",
                    ),
                ),
                parser_version="entity-test-v1",
                provenance="explicit",
            )

            second = MemoryCoreStore(path)
            facts = second.semantic_facts()

            self.assertEqual(
                facts[0].projection.entities,
                ("Project Atlas", "Tunis"),
            )

    def test_collection_query_keeps_multiple_values_same_relation(self):
        store, _, engine = self.make_engine(
            {
                "intent one": (
                    projection(
                        "wants_to_watch",
                        "Inception",
                        kind="intention",
                        cardinality="collection",
                    ),
                ),
                "intent two": (
                    projection(
                        "wants_to_watch",
                        "Gladiator",
                        kind="intention",
                        cardinality="collection",
                    ),
                ),
            }
        )
        store.remember("intent one")
        store.remember("intent two")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="wants_to_watch",
                answer_mode="collection",
                raw_text="show my watch intentions",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            {
                hit.fact.projection.value
                for hit in result["hits"]
            },
            {"Inception", "Gladiator"},
        )

    def test_exact_date_constraint_is_not_fuzzy(self):
        store, _, engine = self.make_engine(
            {
                "appointment one": (
                    projection(
                        "medical_appointment",
                        "Doctor A",
                        kind="event",
                        qualifiers={"date": "09/10/2026"},
                    ),
                ),
                "appointment two": (
                    projection(
                        "medical_appointment",
                        "Doctor B",
                        kind="event",
                        qualifiers={"date": "10/10/2026"},
                    ),
                ),
            }
        )
        store.remember("appointment one")
        store.remember("appointment two")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="medical_appointment",
                qualifiers={"date": "09/10/2026"},
                exact_terms=("09/10/2026",),
                answer_mode="single",
                raw_text="what is on 09/10/2026",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            result["hits"][0].fact.projection.value,
            "Doctor A",
        )

    def test_timeline_orders_by_event_time_not_memory_insertion_time(self):
        store, _, engine = self.make_engine(
            {
                "later inserted first": (
                    projection(
                        "project_milestone",
                        "Launch",
                        kind="event",
                        qualifiers={"date": "2026-12-01"},
                        cardinality="history",
                    ),
                ),
                "earlier inserted second": (
                    projection(
                        "project_milestone",
                        "Prototype",
                        kind="event",
                        qualifiers={"date": "2026-10-01"},
                        cardinality="history",
                    ),
                ),
            }
        )
        store.remember("later inserted first")
        store.remember("earlier inserted second")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="project_milestone",
                answer_mode="timeline",
                raw_text="show milestone timeline",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            [
                hit.fact.projection.value
                for hit in result["hits"]
            ],
            ["Prototype", "Launch"],
        )

    def test_temporal_qualifier_mismatch_is_exact_not_fuzzy(self):
        store, _, engine = self.make_engine(
            {
                "day one": (
                    projection(
                        "appointment",
                        "A",
                        kind="event",
                        qualifiers={"date": "2026-10-09"},
                    ),
                ),
                "day two": (
                    projection(
                        "appointment",
                        "B",
                        kind="event",
                        qualifiers={"date": "2026-10-10"},
                    ),
                ),
            }
        )
        store.remember("day one")
        store.remember("day two")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="appointment",
                qualifiers={"date": "2026-10-09"},
                answer_mode="single",
                raw_text="appointment on target date",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            result["hits"][0].fact.projection.value,
            "A",
        )

    def test_duplicate_semantic_fact_does_not_create_ambiguity(self):
        store, _, engine = self.make_engine(
            {
                "duplicate one": (
                    projection(
                        "preferred_language",
                        "French",
                        kind="preference",
                        cardinality="single",
                    ),
                ),
                "duplicate two": (
                    projection(
                        "preferred_language",
                        "French",
                        kind="preference",
                        cardinality="single",
                    ),
                ),
            }
        )
        store.remember("duplicate one")
        store.remember("duplicate two")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="preferred_language",
                answer_mode="single",
                raw_text="language paraphrase",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(len(result["hits"]), 1)
        self.assertEqual(
            result["hits"][0].fact.projection.value,
            "French",
        )

    def test_global_query_cannot_leak_project_scoped_memory(self):
        store, _, engine = self.make_engine(
            {
                "global owner": (
                    projection(
                        "preferred_editor",
                        "VS Code",
                        scope="global",
                    ),
                ),
                "alpha owner": (
                    projection(
                        "preferred_editor",
                        "Cursor",
                        scope="project_alpha",
                    ),
                ),
                "beta owner": (
                    projection(
                        "preferred_editor",
                        "Vim",
                        scope="project_beta",
                    ),
                ),
            }
        )
        store.remember("global owner")
        store.remember("alpha owner")
        store.remember("beta owner")

        global_result = engine.resolve(
            MemoryQueryFrame(
                relation="preferred_editor",
                scope="global",
                answer_mode="single",
                raw_text="global editor",
                confidence=0.99,
            )
        )
        alpha_result = engine.resolve(
            MemoryQueryFrame(
                relation="preferred_editor",
                scope="project_alpha",
                answer_mode="single",
                raw_text="alpha editor",
                confidence=0.99,
            )
        )

        self.assertEqual(
            global_result["hits"][0].fact.projection.value,
            "VS Code",
        )
        values = {
            hit.fact.projection.value
            for hit in alpha_result.get("hits", [])
        }
        self.assertNotIn("Vim", values)

    def test_explicit_single_value_update_supersedes_but_preserves_history(self):
        store, _, engine = self.make_engine({})
        first = store.remember("language old")
        store.save_projection(
            first.id,
            (
                projection(
                    "preferred_language",
                    "English",
                    kind="preference",
                    cardinality="single",
                ),
            ),
            parser_version=engine.parser_version,
            provenance="explicit",
        )
        second = store.remember("language new")
        store.save_projection(
            second.id,
            (
                projection(
                    "preferred_language",
                    "French",
                    kind="preference",
                    cardinality="single",
                ),
            ),
            parser_version=engine.parser_version,
            provenance="explicit",
        )

        active = engine.resolve(
            MemoryQueryFrame(
                relation="preferred_language",
                answer_mode="single",
                raw_text="current language",
                confidence=0.99,
            )
        )
        history = engine.resolve(
            MemoryQueryFrame(
                relation="preferred_language",
                answer_mode="timeline",
                raw_text="language history",
                confidence=0.99,
            )
        )

        self.assertEqual(
            active["hits"][0].fact.projection.value,
            "French",
        )
        self.assertEqual(
            {
                hit.fact.projection.value
                for hit in history["hits"]
            },
            {"English", "French"},
        )
        statuses = {
            fact.projection.value: fact.status
            for fact in store.semantic_facts(status=None)
        }
        self.assertEqual(statuses["English"], "superseded")
        self.assertEqual(statuses["French"], "active")

    def test_collection_values_are_never_superseded_by_recency(self):
        store, _, engine = self.make_engine({})
        for raw, value in (
            ("watch one", "Inception"),
            ("watch two", "Gladiator"),
        ):
            item = store.remember(raw)
            store.save_projection(
                item.id,
                (
                    projection(
                        "wants_to_watch",
                        value,
                        kind="intention",
                        cardinality="collection",
                    ),
                ),
                parser_version=engine.parser_version,
                provenance="explicit",
            )

        active = store.semantic_facts()
        self.assertEqual(
            {fact.projection.value for fact in active},
            {"Inception", "Gladiator"},
        )

    def test_underspecified_query_refuses_to_guess(self):
        _, _, engine = self.make_engine(
            {
                "one": (projection("home_city", "Tunis"),),
                "two": (projection("favorite_food", "Couscous"),),
            }
        )
        engine.store.remember("one")
        engine.store.remember("two")

        result = engine.resolve(
            MemoryQueryFrame(
                answer_mode="single",
                raw_text="what was it",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "underspecified")
        self.assertEqual(result["hits"], [])

    def test_unicode_scope_key_is_not_collapsed_to_global(self):
        from jarvis_agent.semantic_memory import MemoryProjection

        item = MemoryProjection.from_dict(
            {
                "subject": "user",
                "relation": "project_note",
                "value": "مرحبا",
                "scope": "مشروع النخبة",
                "confidence": 1,
            }
        )

        self.assertNotEqual(item.scope, "global")
        self.assertIn("مشروع", item.scope)

    def test_relation_catalog_is_passed_to_later_projection_batches(self):
        store, interpreter, engine = self.make_engine(
            {
                "first": (
                    projection(
                        "preferred_editor",
                        "Cursor",
                        kind="preference",
                    ),
                ),
                "second": (
                    projection(
                        "preferred_editor",
                        "VS Code",
                        kind="preference",
                    ),
                ),
            }
        )
        first = store.remember("first")
        engine.project_memory(first.id, provenance="explicit")

        second = store.remember("second")
        engine.project_memory(second.id, provenance="explicit")

        self.assertEqual(interpreter.project_calls, 2)
        self.assertEqual(interpreter.project_catalogs[0], ())
        self.assertIn(
            "preferred_editor",
            interpreter.project_catalogs[1],
        )

    def test_query_relation_can_align_once_to_existing_catalog_relation(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = MemoryCoreStore(Path(temp.name) / "memory.sqlite3")
        interpreter = FixtureInterpreter(
            projections={
                "watch intent": (
                    projection(
                        "wants_to_watch",
                        "Inception",
                        kind="intention",
                        cardinality="collection",
                    ),
                )
            },
            alignments={
                "watchlist_items": "wants_to_watch",
            },
        )
        engine = SemanticMemoryEngine(
            store,
            interpreter,
            min_score=0.45,
        )
        store.remember("watch intent")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="watchlist_items",
                answer_mode="collection",
                raw_text="semantic paraphrase",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            result["effective_relation"],
            "wants_to_watch",
        )
        self.assertEqual(
            result["hits"][0].fact.projection.value,
            "Inception",
        )

    def test_semantic_relation_alignment_precedes_misleading_lexical_similarity(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = MemoryCoreStore(Path(temp.name) / "memory.sqlite3")
        interpreter = FixtureInterpreter(
            projections={
                "wrong lexical neighbor": (
                    projection(
                        "project_owner_company",
                        "Acme Corp",
                        kind="project",
                    ),
                ),
                "semantic target": (
                    projection(
                        "responsible_person",
                        "Alice",
                        kind="project",
                    ),
                ),
            },
            alignments={
                "project_owner_name": "responsible_person",
            },
        )
        engine = SemanticMemoryEngine(
            store,
            interpreter,
            min_score=0.45,
        )
        store.remember("wrong lexical neighbor")
        store.remember("semantic target")

        result = engine.resolve(
            MemoryQueryFrame(
                relation="project_owner_name",
                answer_mode="single",
                raw_text="who is the responsible person",
                confidence=0.99,
            )
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(
            result["effective_relation"],
            "responsible_person",
        )
        self.assertEqual(
            result["hits"][0].fact.projection.value,
            "Alice",
        )

    def test_legacy_compound_row_is_lazily_projected_into_independent_facts(self):
        store, interpreter, engine = self.make_engine(
            {
                "legacy compound": (
                    projection(
                        "project_owner",
                        "Maya",
                        kind="project",
                    ),
                    projection(
                        "project_budget",
                        "25000",
                        kind="project",
                        qualifiers={"currency": "USD"},
                    ),
                ),
            }
        )
        legacy = store.remember("legacy compound")
        self.assertIsNone(store.semantic_state(legacy.id))

        result = engine.resolve(
            MemoryQueryFrame(
                relation="project_budget",
                answer_mode="single",
                raw_text="project financial question",
                confidence=0.99,
            )
        )

        self.assertEqual(
            result["hits"][0].fact.projection.value,
            "25000",
        )
        self.assertEqual(
            store.semantic_state(legacy.id)["status"],
            "indexed",
        )
        self.assertEqual(interpreter.project_calls, 1)


class SemanticMemoryRuntimeTests(unittest.TestCase):
    def build_runtime(self, *, turns, projections, refinements=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = MemoryCoreStore(
            Path(temp.name) / "memory.sqlite3"
        )
        interpreter = FixtureInterpreter(
            turns=turns,
            projections=projections,
            refinements=refinements,
        )
        engine = SemanticMemoryEngine(
            store,
            interpreter,
            min_score=0.45,
        )
        delegate = NoLLM()
        tools = FoundationToolAdapter(
            ToolSchemaDelegate(),
            memory=store,
        )
        runtime = SemanticMemoryRuntime(
            delegate,
            tools,
            engine,
        )
        return store, interpreter, delegate, tools, runtime

    def test_arbitrary_explicit_write_is_admitted_by_semantic_intent_not_regex(self):
        user_text = "Please keep this detail for another day: codeword Zeta"
        raw = "codeword Zeta"
        turns = {
            user_text: MemoryTurnInterpretation(
                operation="write",
                write_text=raw,
                confidence=0.99,
                reason="explicit durable request",
            )
        }
        projections = {
            raw: (
                projection(
                    "reference_codeword",
                    "Zeta",
                    cardinality="single",
                ),
            )
        }
        store, _, delegate, _, runtime = self.build_runtime(
            turns=turns,
            projections=projections,
        )

        with patch.dict(
            "os.environ",
            {"JARVIS_SEMANTIC_MEMORY_V5_ENABLED": "1"},
            clear=False,
        ):
            result = runtime.run(user_text)

        self.assertEqual(delegate.calls, 0)
        self.assertEqual(
            [action.name for action in result.actions],
            ["remember_information"],
        )
        self.assertEqual(
            store.semantic_facts()[0].projection.value,
            "Zeta",
        )

    def test_semantic_recall_never_calls_conversational_llm(self):
        query_text = "Could you remind me which editor I chose for project North?"
        turns = {
            query_text: MemoryTurnInterpretation(
                operation="recall",
                query=MemoryQueryFrame(
                    relation="preferred_editor",
                    scope="project_north",
                    answer_mode="single",
                    raw_text=query_text,
                    confidence=0.99,
                ),
                confidence=0.99,
                reason="personal recall",
            )
        }
        projections = {
            "North editor evidence": (
                projection(
                    "preferred_editor",
                    "Cursor",
                    scope="project_north",
                    kind="preference",
                    cardinality="single",
                ),
            )
        }
        store, _, delegate, _, runtime = self.build_runtime(
            turns=turns,
            projections=projections,
        )
        store.remember("North editor evidence")

        result = runtime.run(query_text)

        self.assertEqual(result.text, "Cursor")
        self.assertEqual(delegate.calls, 0)
        self.assertEqual(
            [action.name for action in result.actions],
            ["semantic_memory_recall"],
        )
        self.assertEqual(len(delegate.external_turns), 1)
        self.assertEqual(
            delegate.external_turns[0][0],
            query_text,
        )
        self.assertEqual(
            delegate.external_turns[0][1],
            "Cursor",
        )
        self.assertEqual(
            delegate.external_turns[0][2]["action_name"],
            "semantic_memory_recall",
        )

    def test_inspect_lists_raw_durable_evidence_without_legacy_recall_tool(self):
        inspect_text = "Show me what is stored in my local memory."
        turns = {
            inspect_text: MemoryTurnInterpretation(
                operation="inspect",
                confidence=0.99,
                reason="memory inspection",
            )
        }
        store, _, delegate, _, runtime = self.build_runtime(
            turns=turns,
            projections={},
        )
        store.remember("raw evidence one")
        store.remember("raw evidence two")

        result = runtime.run(inspect_text)

        self.assertIn("raw evidence one", result.text)
        self.assertIn("raw evidence two", result.text)
        self.assertEqual(delegate.calls, 0)
        self.assertEqual(
            [action.name for action in result.actions],
            ["semantic_memory_inspect"],
        )

    def test_ambiguous_recall_uses_semantic_clarification_context(self):
        question = "Which office location did I mean?"
        clarification = "the Atlas project"
        initial = MemoryQueryFrame(
            relation="office_location",
            answer_mode="single",
            raw_text=question,
            confidence=0.99,
        )
        refined = MemoryQueryFrame(
            relation="office_location",
            qualifiers={"project": "Atlas"},
            answer_mode="single",
            raw_text=question + " " + clarification,
            confidence=0.99,
        )
        turns = {
            question: MemoryTurnInterpretation(
                operation="recall",
                query=initial,
                confidence=0.99,
                reason="personal recall",
            )
        }
        projections = {
            "office beta": (
                projection(
                    "office_location",
                    "Tunis",
                    qualifiers={"project": "Beta"},
                    cardinality="single",
                ),
            ),
            "office atlas": (
                projection(
                    "office_location",
                    "Sfax",
                    qualifiers={"project": "Atlas"},
                    cardinality="single",
                ),
            ),
        }
        store, _, delegate, _, runtime = self.build_runtime(
            turns=turns,
            projections=projections,
            refinements={
                ("office_location", clarification): refined,
            },
        )
        store.remember("office beta")
        store.remember("office atlas")

        first = runtime.run(question)
        self.assertIn("plusieurs", first.text.casefold())

        second = runtime.run(clarification)
        self.assertEqual(second.text, "Sfax")
        self.assertEqual(delegate.calls, 0)

    def test_entity_centric_question_returns_multiple_relations_with_context(self):
        question = "What do you know about Project Atlas?"
        turns = {
            question: MemoryTurnInterpretation(
                operation="recall",
                query=MemoryQueryFrame(
                    relation="",
                    entities=("Project Atlas",),
                    answer_mode="collection",
                    raw_text=question,
                    confidence=0.99,
                ),
                confidence=0.99,
                reason="entity-centric recall",
            )
        }
        projections = {
            "atlas owner": (
                projection(
                    "project_owner",
                    "Alice",
                    kind="project",
                    entities=("Project Atlas", "Alice"),
                ),
            ),
            "atlas deadline": (
                projection(
                    "project_deadline",
                    "2026-11-01",
                    kind="event",
                    entities=("Project Atlas",),
                ),
            ),
            "unrelated owner": (
                projection(
                    "project_owner",
                    "Bob",
                    kind="project",
                    entities=("Project Beta", "Bob"),
                ),
            ),
        }
        store, _, delegate, _, runtime = self.build_runtime(
            turns=turns,
            projections=projections,
        )
        store.remember("atlas owner")
        store.remember("atlas deadline")
        store.remember("unrelated owner")

        result = runtime.run(question)

        self.assertEqual(delegate.calls, 0)
        self.assertIn("project owner: Alice", result.text)
        self.assertIn("project deadline: 2026-11-01", result.text)
        self.assertNotIn("Bob", result.text)

    def test_session_semantic_fact_is_recalled_before_persistent_memory(self):
        statement = "I am currently working on project Atlas Nova."
        question = "What is the name of the project I am working on?"
        session_fact = projection(
            "current_project_name",
            "Atlas Nova",
            kind="project",
            cardinality="single",
        )
        turns = {
            statement: MemoryTurnInterpretation(
                operation="pass",
                session_facts=(session_fact,),
                confidence=0.99,
                reason="ordinary personal statement",
            ),
            question: MemoryTurnInterpretation(
                operation="recall",
                query=MemoryQueryFrame(
                    relation="current_project_name",
                    answer_mode="single",
                    raw_text=question,
                    confidence=0.99,
                ),
                confidence=0.99,
                reason="personal recall",
            ),
        }
        _, _, delegate, _, runtime = self.build_runtime(
            turns=turns,
            projections={},
        )

        first = runtime.run(statement)
        self.assertEqual(first.text, "delegate:" + statement)
        self.assertEqual(delegate.calls, 1)

        second = runtime.run(question)
        self.assertEqual(second.text, "Atlas Nova")
        self.assertEqual(delegate.calls, 1)

    def test_session_single_value_update_replaces_only_same_semantic_slot(self):
        first_statement = "My current workspace is North."
        second_statement = "My current workspace is South."
        question = "Which workspace am I using now?"
        turns = {
            first_statement: MemoryTurnInterpretation(
                operation="pass",
                session_facts=(
                    projection(
                        "current_workspace",
                        "North",
                        cardinality="single",
                    ),
                ),
                confidence=0.99,
                reason="session statement",
            ),
            second_statement: MemoryTurnInterpretation(
                operation="pass",
                session_facts=(
                    projection(
                        "current_workspace",
                        "South",
                        cardinality="single",
                    ),
                ),
                confidence=0.99,
                reason="session statement",
            ),
            question: MemoryTurnInterpretation(
                operation="recall",
                query=MemoryQueryFrame(
                    relation="current_workspace",
                    answer_mode="single",
                    raw_text=question,
                    confidence=0.99,
                ),
                confidence=0.99,
                reason="session recall",
            ),
        }
        _, _, _, _, runtime = self.build_runtime(
            turns=turns,
            projections={},
        )

        runtime.run(first_statement)
        runtime.run(second_statement)
        result = runtime.run(question)

        self.assertEqual(result.text, "South")

    def test_reset_clears_session_semantics_but_not_persistent_store(self):
        statement = "My temporary codeword is Orion."
        question = "What is my temporary codeword?"
        turns = {
            statement: MemoryTurnInterpretation(
                operation="pass",
                session_facts=(
                    projection(
                        "temporary_codeword",
                        "Orion",
                        cardinality="single",
                    ),
                ),
                confidence=0.99,
                reason="session statement",
            ),
            question: MemoryTurnInterpretation(
                operation="recall",
                query=MemoryQueryFrame(
                    relation="temporary_codeword",
                    answer_mode="single",
                    raw_text=question,
                    confidence=0.99,
                ),
                confidence=0.99,
                reason="session recall",
            ),
        }
        _, _, delegate, _, runtime = self.build_runtime(
            turns=turns,
            projections={},
        )

        runtime.run(statement)
        self.assertEqual(runtime.run(question).text, "Orion")

        runtime.reset()
        missing = runtime.run(question)

        self.assertIn("pas assez de contexte", missing.text)
        self.assertEqual(delegate.calls, 1)

    def test_non_memory_turn_passes_to_normal_runtime(self):
        text = "Explain the offside rule."
        _, _, delegate, _, runtime = self.build_runtime(
            turns={},
            projections={},
        )

        result = runtime.run(text)

        self.assertEqual(result.text, "delegate:" + text)
        self.assertEqual(delegate.calls, 1)

    def test_legacy_memory_tools_are_hidden_from_model_in_v5(self):
        tools = FoundationToolAdapter(
            ToolSchemaDelegate(),
            memory=object(),
        )
        with patch.dict(
            "os.environ",
            {"JARVIS_SEMANTIC_MEMORY_V5_ENABLED": "1"},
            clear=False,
        ):
            names = {
                item["function"]["name"]
                for item in tools.ollama_tools()
            }

        self.assertNotIn("remember_information", names)
        self.assertNotIn("recall_information", names)
        self.assertIn("open_application", names)


class SemanticMemoryFactoryIntegrationTests(unittest.TestCase):
    def test_factory_builds_semantic_memory_v5_before_provider_runtime(self):
        from dataclasses import replace

        from jarvis_agent.agent_runtime import build_agent_runtime
        from jarvis_agent.config import settings

        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "factory.sqlite3")
            user_write = "Keep this durable: reference Sigma"
            user_recall = "What durable reference did I save?"
            interpreter = FixtureInterpreter(
                turns={
                    user_write: MemoryTurnInterpretation(
                        operation="write",
                        write_text="reference Sigma",
                        confidence=0.99,
                        reason="explicit durable request",
                    ),
                    user_recall: MemoryTurnInterpretation(
                        operation="recall",
                        query=MemoryQueryFrame(
                            relation="saved_reference",
                            answer_mode="single",
                            raw_text=user_recall,
                            confidence=0.99,
                        ),
                        confidence=0.99,
                        reason="personal recall",
                    ),
                },
                projections={
                    "reference Sigma": (
                        projection(
                            "saved_reference",
                            "Sigma",
                            cardinality="single",
                        ),
                    )
                },
            )
            adapter = FoundationToolAdapter(
                ToolSchemaDelegate(),
                memory=store,
            )

            for provider, class_name in (
                ("ollama", "OllamaToolAgent"),
                ("openai", "OpenAIResponsesAgent"),
                ("groq", "GroqResponsesAgent"),
                ("cerebras", "CerebrasResponsesAgent"),
            ):
                with self.subTest(provider=provider), patch.dict(
                    "os.environ",
                    {
                        "JARVIS_MEMORY_CORE_ENABLED": "1",
                        "JARVIS_SEMANTIC_MEMORY_V5_ENABLED": "1",
                        "JARVIS_BROWSER_CORE_ENABLED": "0",
                        "JARVIS_COMPUTER_CORE_ENABLED": "0",
                    },
                    clear=False,
                ), patch(
                    "jarvis_agent.agent_runtime.settings",
                    replace(
                        settings,
                        agent_provider=provider,
                        structured_tracing_enabled=False,
                    ),
                ), patch(
                    "jarvis_agent.agent_runtime." + class_name,
                    return_value=NoLLM(),
                ), patch(
                    "jarvis_agent.foundation_tools.build_foundation_tools",
                    return_value=adapter,
                ), patch(
                    "jarvis_agent.memory_semantic_interpreter."
                    "build_semantic_memory_interpreter",
                    return_value=interpreter,
                ):
                    runtime = build_agent_runtime()
                    write = runtime.run(user_write)
                    self.assertEqual(
                        [action.name for action in write.actions],
                        ["remember_information"],
                    )
                    runtime.reset()
                    recall = runtime.run(user_recall)
                    self.assertEqual(recall.text, "Sigma")
                    self.assertEqual(
                        [action.name for action in recall.actions],
                        ["semantic_memory_recall"],
                    )


class SemanticMemoryLiveRunnerTests(unittest.TestCase):
    def test_live_evaluator_is_importable_as_repo_root_module(self):
        import importlib

        module = importlib.import_module(
            "scripts.evaluate_semantic_memory_v5"
        )

        self.assertTrue(callable(module.main))


class SemanticInterpreterContractTests(unittest.TestCase):
    def test_invalid_or_weak_projection_is_rejected_without_mutating_raw_memory(self):
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            item = store.remember("raw evidence survives")
            interpreter = FixtureInterpreter(
                projections={
                    "raw evidence survives": (
                        projection(
                            "uncertain_relation",
                            "guess",
                            confidence=0.20,
                        ),
                    )
                }
            )
            engine = SemanticMemoryEngine(store, interpreter)

            facts = engine.project_memory(
                item.id,
                provenance="explicit",
            )

            self.assertEqual(facts, ())
            self.assertEqual(
                store.get_memory(item.id).content,
                "raw evidence survives",
            )
            self.assertEqual(
                store.semantic_state(item.id)["status"],
                "unprojected",
            )

    def test_query_confidence_can_force_clarification_even_with_relation(self):
        store = None
        with tempfile.TemporaryDirectory() as folder:
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            interpreter = FixtureInterpreter(
                projections={
                    "known": (
                        projection("home_city", "Tunis"),
                    )
                }
            )
            engine = SemanticMemoryEngine(
                store,
                interpreter,
                min_score=0.45,
            )
            store.remember("known")

            result = engine.resolve(
                MemoryQueryFrame(
                    relation="home_city",
                    answer_mode="single",
                    raw_text="maybe something about home",
                    confidence=0.20,
                )
            )

            self.assertEqual(result["status"], "underspecified")


if __name__ == "__main__":
    unittest.main()
