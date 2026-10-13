"""Direct-agent memory contracts: no Ollama, no implicit cloud indexing."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.memory_core_store import MemoryCoreStore
from jarvis_agent.memory_retrieval import search, terms


DIRECT = {
    "JARVIS_MEMORY_CORE_ENABLED": "1",
    "JARVIS_SEMANTIC_MEMORY_V5_ENABLED": "1",
    "JARVIS_MEMORY_AGENT_TOOLS_ENABLED": "1",
    "JARVIS_MEMORY_DIRECT_AGENT_ENABLED": "1",
    "JARVIS_MEMORY_ASYNC_PROJECTION_ENABLED": "0",
}


class MinimalNativeRegistry:
    def ollama_tools(self):
        return [self._ollama(
            "remember_information", "Durable write requires user request",
            {"content": {"type": "string"}, "tags": {"type": "string"}},
            ["content"],
        )]

    def _ollama(self, name, description, properties, required):
        return {"type": "function", "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required},
        }}

    def requires_confirmation(self, name):
        return False


class DirectMemoryV10FTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = MemoryCoreStore(Path(self.tmp.name) / "memory.sqlite3")

    def test_self_name_retrieval_exact_source_not_unrelated_recent_row(self):
        self.memory.remember("J'ai un rendez-vous de football", tags="football")
        self.memory.remember("Retiens que mon nom est Salah", tags="nom")
        results = search(self.memory, "Quel est mon nom ?")
        self.assertEqual(len(results), 1)
        self.assertIn("Salah", results[0].content)
        self.assertEqual(search(self.memory, "Quel est mon prénom ?"), [])
        self.assertEqual(search(self.memory, "Quel est mon restaurant ?"), [])
        self.assertEqual(terms("Quel est mon nom ?"), ())

    def test_generic_subject_recovery_requires_matching_source_evidence(self):
        self.memory.remember("Mon prénom est Amine", tags="prénom")
        self.memory.remember("Je m'appelle Amine", tags="identité")
        self.memory.remember("Mon adresse est rue des Jasmins", tags="adresse")
        self.assertTrue(search(self.memory, "Quel est mon prénom ?"))
        self.assertTrue(search(self.memory, "Comment je m'appelle ?"))
        self.assertEqual(search(self.memory, "Quel est mon nom d'utilisateur ?"), [])
        self.assertEqual(search(self.memory, "Qui ?", ), [])
        self.assertEqual(search(self.memory, "Quel est mon restaurant ?"), [])
        self.assertTrue(search(self.memory, "Quelle est mon adresse ?"))

    def test_context_recovery_uses_original_request_without_broad_inventory(self):
        with patch.dict(os.environ, DIRECT):
            self.memory.remember("Mon équipe joue vendredi", tags="équipe")
            self.memory.remember("Ma réunion du club est lundi", tags="réunion")
            self.memory.remember("Retiens que mon nom est Mhedhebi", tags="nom")
            tools = FoundationToolAdapter(MinimalNativeRegistry(), memory=self.memory)
            # A model may shorten a full request to a meaningless token.
            tools.begin_turn("Quand est ma réunion du club ?")
            result = tools.execute("semantic_memory_search", {"query": "quoi"})
            self.assertTrue(result.success)
            payload = json.loads(result.detail)
            self.assertEqual(payload["search_source"], "original_user_request")
            self.assertEqual([x["raw"] for x in payload["raw_fallback"]],
                             ["Ma réunion du club est lundi"])
            tools.begin_turn("Quel est mon restaurant préféré ?")
            no_match = json.loads(tools.execute(
                "semantic_memory_search", {"query": "quoi"}).detail)
            self.assertEqual(no_match["status"], "missing")
            self.assertEqual(no_match["raw_fallback"], [])

    def test_short_model_query_keeps_all_matching_conflicting_evidence(self):
        with patch.dict(os.environ, DIRECT):
            self.memory.remember("Retiens que mon nom est Salah", tags="nom")
            self.memory.remember("Retiens que mon nom est Mhedhebi", tags="nom")
            tools = FoundationToolAdapter(MinimalNativeRegistry(), memory=self.memory)
            tools.begin_turn("Quel est mon nom ?")
            answer = json.loads(tools.execute(
                "semantic_memory_search", {"query": "nom"}).detail)
            self.assertEqual(answer["status"], "resolved")
            self.assertEqual(answer["search_source"], "tool_query")
            raw = [x["raw"] for x in answer["raw_fallback"]]
            self.assertEqual(len(raw), 2)
            self.assertTrue(any("Salah" in x for x in raw))
            self.assertTrue(any("Mhedhebi" in x for x in raw))

    def test_explicit_write_is_model_independent_and_idempotent_per_turn(self):
        from jarvis_agent.foundation_tools import FoundationRuntime
        from jarvis_agent.agent_runtime import AgentRuntimeUnavailable
        class BrokenModel:
            calls = 0
            def run(self, *args, **kwargs):
                self.calls += 1
                raise AgentRuntimeUnavailable("all brains offline")
        with patch.dict(os.environ, DIRECT):
            delegate = BrokenModel()
            adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=self.memory)
            runtime = FoundationRuntime(delegate, adapter)
            sentence = "retiens que demain j'ai un cours de padel"
            result = runtime.run(sentence)
            self.assertEqual(delegate.calls, 0)
            self.assertEqual(result.actions[0].name, "remember_information")
            self.assertTrue(result.actions[0].success)
            import hashlib
            payload = json.loads(result.actions[0].detail)
            self.assertTrue(payload["verified"])
            self.assertEqual(
                payload["raw_sha256"], hashlib.sha256(sentence.encode()).hexdigest()
            )
            self.assertEqual(self.memory.recent_memories(limit=20)[0].content, sentence)
            self.assertEqual(len(self.memory.all_memories()), 1)
            # Adapter protection still rejects a second tool write in the same turn.
            again = adapter.execute("remember_information", {"content": "retry"})
            self.assertFalse(again.success)
            self.assertEqual(len(self.memory.all_memories()), 1)
            fresh = FoundationRuntime(
                BrokenModel(),
                FoundationToolAdapter(
                    MinimalNativeRegistry(), memory=MemoryCoreStore(self.memory.db_path)
                ),
            )
            found = fresh.tools.memory.recent_memories(limit=5)
            self.assertEqual(found[0].content, sentence)

    def test_explicit_memory_recall_survives_restart_without_brain(self):
        from jarvis_agent.foundation_tools import FoundationRuntime
        from jarvis_agent.agent_runtime import AgentRuntimeUnavailable
        class OfflineAgent:
            calls = 0
            def run(self, *args, **kwargs):
                self.calls += 1
                raise AgentRuntimeUnavailable("all providers offline")
        with patch.dict(os.environ, DIRECT):
            original = FoundationRuntime(
                OfflineAgent(),
                FoundationToolAdapter(MinimalNativeRegistry(), memory=self.memory),
            )
            original.run("retiens que demain j'ai un cours de padel")
            fresh = FoundationRuntime(
                OfflineAgent(),
                FoundationToolAdapter(
                    MinimalNativeRegistry(),
                    memory=MemoryCoreStore(self.memory.db_path),
                ),
            )
            result = fresh.run("Tu te souviens de mon cours de padel ?")
            self.assertIn("cours de padel", result.text)
            self.assertIn("Souvenir #", result.text)
            self.assertEqual(fresh.delegate.calls, 0)
            none = fresh.run("Tu te souviens de mon anniversaire de ski ?")
            self.assertIn("aucun souvenir correspondant", none.text)
            self.assertEqual(fresh.delegate.calls, 0)

    def test_memory_fast_path_never_steals_mixed_missions_or_questions(self):
        from jarvis_agent.foundation_tools import FoundationRuntime
        from jarvis_agent.agent_runtime import AgentTurnResult
        class Delegate:
            calls = 0
            def run(self, *args, **kwargs):
                self.calls += 1
                return AgentTurnResult("delegated")
        with patch.dict(os.environ, DIRECT):
            agent = Delegate()
            runtime = FoundationRuntime(
                agent, FoundationToolAdapter(MinimalNativeRegistry(), memory=self.memory)
            )
            for sentence in (
                "retiens que mon cours est demain puis ouvre Chrome",
                "rappelle-moi mon cours de padel",
                "je retiens que tu es là",
                "Pourquoi dois-tu retenir cela ?",
            ):
                result = runtime.run(sentence)
                self.assertEqual(result.text, "delegated")
            self.assertEqual(agent.calls, 4)
            self.assertEqual(self.memory.all_memories(), [])

    def test_raw_relative_date_anchor_survives_restart_without_projection(self):
        from datetime import date
        from jarvis_agent.memory_temporal import (
            raw_temporal_tag, raw_temporal_date_from_tags, events_in_range,
        )
        self.assertEqual(
            raw_temporal_tag("Retiens que demain j'ai un cours de padel",
                             reference_date=date(2026, 10, 13)),
            "jarvis_event_date=2026-10-14",
        )
        self.assertEqual(
            raw_temporal_tag("Retiens que après-demain j'ai un cours de padel",
                             reference_date=date(2026, 10, 13)),
            "jarvis_event_date=2026-10-15",
        )
        self.assertEqual(
            raw_temporal_tag("Retiens que demain ou après-demain, peut-être",
                             reference_date=date(2026, 10, 13)), "",
        )
        with patch.dict(os.environ, DIRECT):
            adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=self.memory)
            note = "retiens que demain j'ai un cours de padel"
            adapter.begin_turn(note)
            with patch("jarvis_agent.memory_temporal.date") as calendar:
                calendar.today.return_value = date(2026, 10, 13)
                # date.today and timedelta are the only non-persisted source.
                calendar.side_effect = date
                write = adapter.execute("remember_information", {"content": "invented"})
            self.assertTrue(write.success, write.message)
            stored = self.memory.recent_memories(limit=3)[0]
            self.assertEqual(stored.content, note)
            self.assertEqual(raw_temporal_date_from_tags(stored.tags), "2026-10-14")
            again = MemoryCoreStore(self.memory.db_path)
            got = events_in_range(again, "2026-10-14", "2026-10-14")
            self.assertEqual(got["status"], "resolved")
            self.assertEqual(got["hits"][0]["raw"], note)
            self.assertTrue(got["hits"][0]["date_mention_only"])

    def test_direct_write_read_and_restart_without_projection(self):
        with patch.dict(os.environ, DIRECT):
            tools = FoundationToolAdapter(MinimalNativeRegistry(), memory=self.memory)
            names = {t["function"]["name"] for t in tools.ollama_tools()}
            self.assertTrue({"remember_information", "semantic_memory_search",
                             "semantic_memory_inspect",
                             "semantic_memory_events_on_date"}.issubset(names))
            self.assertIsNone(tools.semantic_memory_engine)
            tools.begin_turn("Retiens que mon nom est Salah")
            write = tools.execute("remember_information",
                                  {"content": "MODELE NE DOIT PAS DEFORMER LE TEXTE", "tags": "nom"})
            self.assertTrue(write.success, write.message)
            self.assertIn("direct_agent_no_projection", write.detail)
            stored = self.memory.recent_memories(limit=10)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].content, "Retiens que mon nom est Salah")
            self.assertNotIn("MODELE", stored[0].content)
            tools.begin_turn("Quel est mon nom ?")
            result = tools.execute("semantic_memory_search", {"query": "quel est mon nom"})
            self.assertTrue(result.success, result.message)
            payload = json.loads(result.detail)
            self.assertEqual(payload["status"], "resolved")
            self.assertEqual(payload["raw_fallback"][0]["raw"], "Retiens que mon nom est Salah")
            self.assertFalse(payload["projection_required"])
            # Simulate a fresh process adapter on the same existing DB.
            reopened = FoundationToolAdapter(MinimalNativeRegistry(),
                                            memory=MemoryCoreStore(self.memory.db_path))
            reopened.begin_turn("Quel est mon nom ?")
            recovered = reopened.execute("semantic_memory_search", {"query": "Quel est mon nom ?"})
            self.assertEqual(json.loads(recovered.detail)["raw_fallback"][0]["memory_id"],
                             stored[0].id)

    def test_direct_recall_never_returns_unrelated_data(self):
        with patch.dict(os.environ, DIRECT):
            self.memory.remember("Mon plat préféré est le couscous", tags="food")
            tools = FoundationToolAdapter(MinimalNativeRegistry(), memory=self.memory)
            tools.begin_turn("Quel est mon nom ?")
            result = tools.execute("semantic_memory_search", {"query": "Quel est mon nom ?"})
            self.assertTrue(result.success)
            payload = json.loads(result.detail)
            self.assertEqual(payload["status"], "missing")
            self.assertEqual(payload["raw_fallback"], [])

    def test_direct_factory_does_not_construct_semantic_llm(self):
        from jarvis_agent.agent_runtime import build_agent_runtime
        from jarvis_agent.memory import LocalMemory
        import jarvis_agent.memory as memory_module
        with patch.dict(os.environ, {
            **DIRECT,
            "JARVIS_BROWSER_CORE_ENABLED": "0",
            "JARVIS_COMPUTER_CORE_ENABLED": "0",
            "JARVIS_MCP_ENABLED": "0",
            "JARVIS_HERMES_RELIABILITY_ENABLED": "0",
            "JARVIS_ACTIVE_SUPERVISOR_ENABLED": "0",
            "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "0",
            "JARVIS_EFFICIENT_CONTEXT_ENABLED": "0",
        }):
            isolated = LocalMemory(Path(self.tmp.name) / "factory.sqlite3")
            with (
                patch.object(memory_module, "LOCAL_MEMORY", isolated),
                patch("jarvis_agent.memory_semantic_interpreter.build_semantic_memory_interpreter",
                      side_effect=AssertionError("No semantic LLM allowed")),
            ):
                runtime = build_agent_runtime()
                self.assertIsNotNone(runtime)
                foundation = runtime.tools if hasattr(runtime, "tools") else None
                # Construction must succeed without invoking the patched
                # semantic model builder, even if no API provider is reachable.
                self.assertIsNotNone(foundation)


if __name__ == "__main__":
    unittest.main()
