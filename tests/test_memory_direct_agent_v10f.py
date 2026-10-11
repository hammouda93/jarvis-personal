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
        return []

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

    def test_self_first_name_and_call_me_aliases(self):
        self.memory.remember("Mon prénom est Amine", tags="prénom")
        for question in ("Quel est mon prénom ?", "Comment je m'appelle ?"):
            with self.subTest(question=question):
                self.assertEqual(search(self.memory, question)[0].content,
                                 "Mon prénom est Amine")

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
