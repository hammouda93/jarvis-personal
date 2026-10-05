from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_agent.agent_runtime import AgentTurnResult
from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.memory_core_store import MemoryCoreStore
from jarvis_agent.memory_semantic_interpreter import SemanticMemoryInterpreter
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
        scope=scope,
        cardinality=cardinality,
        confidence=confidence,
    )


class FixtureInterpreter(SemanticMemoryInterpreter):
    parser_version = "fixture-semantic-v1"

    def __init__(self, *, turns=None, projections=None, refinements=None):
        self.turns = dict(turns or {})
        self.projections = dict(projections or {})
        self.refinements = dict(refinements or {})
        self.project_calls = 0
        self.turn_calls = 0

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

    def project_batch(self, items):
        self.project_calls += 1
        return {
            memory_id: tuple(self.projections.get(text, ()))
            for memory_id, text in items
        }

    def refine_query(self, previous, clarification):
        return self.refinements.get(
            (previous.relation, clarification),
            previous,
        )


class NoLLM:
    def __init__(self):
        self.calls = 0

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

    def record_external_turn(self, *args, **kwargs):
        return None


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
        self.assertEqual(result.actions, ())

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


if __name__ == "__main__":
    unittest.main()
