"""Replays of reported Windows defects; providers are fixtures, SQLite is real."""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from jarvis_agent.agent_runtime import GroqResponsesAgent
from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent.tools import ToolIntent, route
from test_agent_runtime import FakeGroqAgent, FakeTools


class SearchDestinationTests(unittest.TestCase):
    def test_named_sources_are_not_implicit_google_searches(self):
        for text in (
            "Cherche dans la memoire persistante", "Recherche dans Gmail",
            "Cherche dans mes fichiers", "Cherche dans l'application ouverte",
            "Recherche les inscriptions dans ZetaDesk", "Search in my notes",
            "Cherche sur mon serveur", "Recherche dans la memoire le code Google",
        ):
            with self.subTest(text=text):
                self.assertEqual(route(text).name, "unknown")

    def test_old_search_intents_cannot_bypass_destination_check(self):
        for surface in ("", "browser", "app", "filesystem"):
            for scope in ("context", "web"):
                with self.subTest(surface=surface, scope=scope):
                    self.assertFalse(AssistantWorker._is_simple_direct_action(
                        "Cherche dans la memoire persistante",
                        ToolIntent("browser.search", {"query": "memoire", "scope": scope}),
                        surface,
                    ))

    def test_explicit_web_and_existing_youtube_shortcuts_survive(self):
        self.assertEqual(route("Recherche meteo sur Google").name, "browser.search")
        self.assertEqual(route("Recherche Messi").name, "browser.search")
        self.assertEqual(route("Ouvre YouTube et recherche Messi").name, "browser.search_site")


class MemoryResultTools(FakeTools):
    def __init__(self, name, payload):
        super().__init__()
        self.name, self.payload = name, payload

    def ollama_tools(self):
        return super().ollama_tools() + [{"type": "function", "function": {
            "name": self.name, "parameters": {"type": "object", "properties": {}}}}]

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, arguments, approved))
        return AgentActionResult(name=name, success=True, message="Local read",
                                 detail=json.dumps(self.payload))


class MemoryEvidenceTests(unittest.TestCase):
    def answer(self, payload, text, *, name="semantic_memory_search"):
        agent = FakeGroqAgent(MemoryResultTools(name, payload), [
            {"output": [{"type": "function_call", "call_id": "memory-1", "name": name,
                         "arguments": json.dumps({"query": "Orion-Test"})}]},
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]},
        ])
        return agent.run("Quel est le code du projet Orion-Test ?"), agent

    def test_model_cannot_deny_matching_raw_evidence_when_hits_are_empty(self):
        payload = {"status": "resolved", "hits": [], "read_only": True, "raw_fallback": [{
            "memory_id": 1, "raw": "Mon projet Orion-Test a le code ALPHA-728.",
            "created_at": "2026-10-09T12:00:00+00:00"}]}
        result, agent = self.answer(payload, "Je n'ai trouve aucune trace du code.")
        self.assertIn("ALPHA-728", result.text)
        self.assertNotIn("aucune trace", result.text)
        self.assertEqual(len(agent.tools.calls), 1)

    def test_incomplete_index_is_not_an_empty_agenda(self):
        result, _ = self.answer({"status": "index_incomplete", "hits": [], "read_only": True,
                                "projection_issue_count": 1},
                               "Il n'y a aucun evenement enregistre.",
                               name="semantic_memory_events_on_date")
        self.assertIn("incomplet", result.text.lower())
        self.assertNotIn("Il n'y a aucun evenement enregistre", result.text)

    def test_complete_missing_result_and_grounded_positive_answer_are_not_rewritten(self):
        text = "Je n'ai trouve aucune correspondance pour cette recherche."
        result, _ = self.answer({"status": "missing", "hits": [], "read_only": True}, text)
        self.assertEqual(result.text, text)
        positive = "Le code du projet est ALPHA-728."
        result, _ = self.answer({"status": "resolved", "read_only": True, "hits": [],
            "raw_fallback": [{"memory_id": 1, "raw": positive}]}, positive)
        self.assertEqual(result.text, positive)

    def test_large_result_stays_structured_and_retains_raw_proof(self):
        payload = {"status": "resolved", "read_only": True,
            "hits": [{"memory_id": i, "value": "x" * 800} for i in range(16)],
            "raw_fallback": [{"memory_id": 1, "raw": "Proof CODE-481"}]}
        result = AgentActionResult(name="semantic_memory_search", success=True,
                                   message="Local read", detail=json.dumps(payload))
        envelope = json.loads(GroqResponsesAgent._compact_tool_content(result.name, result))
        detail = json.loads(envelope["detail"])
        self.assertIn("CODE-481", json.dumps(detail))
        self.assertTrue(detail["truncated"])
        self.assertLessEqual(len(envelope["detail"]), 3500)


class RegressionEnvironmentTests(unittest.TestCase):
    def test_live_flags_are_reset_without_mutating_parent_settings(self):
        from scripts.run_final_regression import regression_environment
        inherited = {"JARVIS_MEMORY_AGENT_TOOLS_ENABLED": "1", "JARVIS_MEMORY_SCOPE_GUARD_ENABLED": "1",
                     "JARVIS_MEMORY_CORE_ENABLED": "1", "JARVIS_SEMANTIC_MEMORY_V5_ENABLED": "1"}
        with patch.dict(os.environ, inherited):
            env = regression_environment(Path("fixture"))
            for name in inherited:
                self.assertEqual(env[name], "0")
                self.assertEqual(os.environ[name], "1")
            self.assertEqual(env["CEREBRAS_API_KEY"], "")
            self.assertEqual(env["JARVIS_DATA_DIR"], "fixture")

