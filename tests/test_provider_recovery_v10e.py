from dataclasses import replace
import os
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import AgentRuntimeUnavailable, CerebrasResponsesAgent
from jarvis_agent.config import settings
from jarvis_agent.memory_semantic_interpreter import ModelSemanticMemoryInterpreter
from test_agent_runtime import FakeTools
from test_provider_continuity_v10d import response


class ProviderRecoveryTests(unittest.TestCase):
    def test_incomplete_primary_is_not_dispatched_and_secondary_recovers_same_context(self):
        primary, secondary = Mock(), Mock()
        primary.chat.completions.create.return_value = response(arguments='{"name":"Fixture"}', finish_reason="length")
        secondary.chat.completions.create.return_value = response(text="Observation preserved")
        config = replace(settings, cerebras_api_key="fixture-primary", cerebras_secondary_api_key="fixture-secondary",
                         groq_api_key="", cerebras_fallback_to_groq=False)
        with patch("jarvis_agent.agent_runtime.settings", config):
            agent = CerebrasResponsesAgent(FakeTools())
            with patch.object(agent, "_get_client", side_effect=lambda: secondary if agent.api_key == "fixture-secondary" else primary):
                agent.run_with_context("Lis les observations", "MISSION: no external write; preserve 67890")
        self.assertEqual(agent.tools.calls, [])
        self.assertEqual(secondary.chat.completions.create.call_count, 1)
        self.assertEqual(agent.last_effective_credential, "secondary")
        self.assertIn("67890", secondary.chat.completions.create.call_args.kwargs["messages"][0]["content"])

    def test_cold_local_semantics_gets_bounded_time_then_warm_timeout(self):
        with patch.dict(os.environ, {"JARVIS_MEMORY_SEMANTIC_TIMEOUT_S": "8",
                                    "JARVIS_MEMORY_SEMANTIC_COLD_TIMEOUT_S": "45"}):
            interpreter = ModelSemanticMemoryInterpreter(provider="ollama", allow_cloud=False)
        reply = Mock()
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        reply.read.return_value = json.dumps({"done": True, "message": {"content": '{"operation":"pass"}'}}).encode()
        with patch("urllib.request.urlopen", return_value=reply) as http, patch("time.monotonic", return_value=100):
            interpreter._chat_ollama("system", "synthetic text")
            interpreter._chat_ollama("system", "synthetic text")
        self.assertEqual(http.call_args_list[0].kwargs["timeout"], 45)
        self.assertEqual(http.call_args_list[1].kwargs["timeout"], 8)
        packet = json.loads(http.call_args_list[0].args[0].data)
        self.assertEqual(packet["keep_alive"], "5m")

    def test_explicit_timeout_stays_explicit_and_timeout_never_retries(self):
        interpreter = ModelSemanticMemoryInterpreter(provider="ollama", timeout_s=9, allow_cloud=False)
        with patch("urllib.request.urlopen", side_effect=TimeoutError("fixture")) as http:
            with self.assertRaisesRegex(RuntimeError, "ollama_unavailable"):
                interpreter._chat_ollama("system", "synthetic text")
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.kwargs["timeout"], 9)
        self.assertEqual(interpreter._provider_chain(), ("ollama",))

    def test_cold_override_can_coexist_with_legacy_warm_timeout(self):
        with patch.dict(os.environ, {"JARVIS_MEMORY_SEMANTIC_TIMEOUT_S": "8",
                                    "JARVIS_MEMORY_SEMANTIC_COLD_TIMEOUT_S": "45"}):
            interpreter = ModelSemanticMemoryInterpreter(provider="ollama", allow_cloud=False)
        self.assertEqual(interpreter.timeout_s, 8)
        self.assertEqual(interpreter.cold_timeout_s, 45)
