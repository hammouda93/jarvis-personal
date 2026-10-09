"""Provider admission checks avoid repeated 429 and predictable Groq 413."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jarvis_agent.provider_rate_budget import ProviderRateGate, groq_fallback_preflight
from jarvis_agent.agent_runtime import (
    AgentRuntimeUnavailable, CerebrasResponsesAgent, GroqResponsesAgent,
)


class ProviderRateBudgetTests(unittest.TestCase):
    def test_rejected_requests_do_not_extend_the_original_cooldown(self):
        now = [0.0]
        agent = CerebrasResponsesAgent.__new__(CerebrasResponsesAgent)
        agent._provider_rate_gate = ProviderRateGate(cooldown_seconds=60, clock=lambda: now[0])
        agent._provider_rate_gate.note_failure("primary", RuntimeError("429"))
        config = SimpleNamespace(cerebras_secondary_api_key="", cerebras_fallback_to_groq=False, groq_api_key="")
        with patch("jarvis_agent.agent_runtime.settings", config), patch.object(GroqResponsesAgent, "_chat", return_value="ok") as chat:
            now[0] = 59.0
            with self.assertRaises(AgentRuntimeUnavailable):
                agent._chat()
            self.assertEqual(agent._provider_rate_gate.remaining("primary"), 1.0)
            now[0] = 61.0
            self.assertEqual(agent._chat(), "ok")
        self.assertEqual(chat.call_count, 1)

    def test_cooldown_after_429_but_not_after_unrelated_error(self):
        now = [100.0]
        gate = ProviderRateGate(cooldown_seconds=60.0, clock=lambda: now[0])
        self.assertFalse(gate.note_failure("main", RuntimeError("500 unavailable")))
        self.assertTrue(gate.available("main"))
        self.assertTrue(gate.note_failure("main", RuntimeError("429 token_quota_exceeded")))
        self.assertFalse(gate.available("main"))
        self.assertEqual(gate.remaining("main"), 60.0)
        self.assertTrue(gate.available("secondary"))
        now[0] += 59
        self.assertFalse(gate.available("main"))
        now[0] += 2
        self.assertTrue(gate.available("main"))

    def test_preflight_blocks_large_payload_and_allows_short_messages(self):
        ok, estimate, limit = groq_fallback_preflight(
            [{"role": "user", "content": "X" * 40000}], [],
            limit_tokens=7000)
        self.assertFalse(ok)
        self.assertGreater(estimate, limit)
        ok, estimate, limit = groq_fallback_preflight(
            [{"role": "user", "content": "bonjour"}], [],
            limit_tokens=7000)
        self.assertTrue(ok)
        self.assertLess(estimate, limit)

    def test_cerebras_secondary_works_but_primary_is_not_retried_during_cooldown(self):
        agent = CerebrasResponsesAgent.__new__(CerebrasResponsesAgent)
        agent._provider_rate_gate = ProviderRateGate(cooldown_seconds=60)
        agent.api_key = "dummy-primary"
        agent.base_url = "https://primary.example.org"
        agent._client = None
        settings = SimpleNamespace(
            cerebras_secondary_api_key="dummy-secondary",
            cerebras_secondary_base_url="https://secondary.example.org",
            cerebras_fallback_to_groq=False, groq_api_key="")
        with patch("jarvis_agent.agent_runtime.settings", settings), patch.object(
                GroqResponsesAgent, "_chat",
                side_effect=[AgentRuntimeUnavailable("API error 429: limit"),
                             "secondary ok", "secondary again"]) as mocked:
            self.assertEqual(agent._chat(), "secondary ok")
            self.assertEqual(agent._chat(), "secondary again")
        self.assertEqual(mocked.call_count, 3)
        self.assertGreater(agent._provider_rate_gate.remaining("primary"), 0)
        self.assertLessEqual(agent._provider_rate_gate.remaining("primary"), 60)
        self.assertFalse(agent._provider_rate_gate.available("primary"))

    def test_primary_402_disables_only_primary_for_process(self):
        agent = CerebrasResponsesAgent.__new__(CerebrasResponsesAgent)
        agent._provider_rate_gate = ProviderRateGate(cooldown_seconds=60)
        agent.api_key = "dummy-primary"
        agent.base_url = "https://primary.example.org"
        agent._client = None
        settings = SimpleNamespace(
            cerebras_secondary_api_key="dummy-secondary",
            cerebras_secondary_base_url="https://secondary.example.org",
            cerebras_fallback_to_groq=False, groq_api_key="")
        with patch("jarvis_agent.agent_runtime.settings", settings), patch.object(
                GroqResponsesAgent, "_chat",
                side_effect=[
                    AgentRuntimeUnavailable("cerebras API error 402: payment_required"),
                    "secondary ok", "secondary again"]) as mocked:
            self.assertEqual(agent._chat(), "secondary ok")
            self.assertEqual(agent._chat(), "secondary again")
        # Only three actual calls: primary once and secondary twice.
        self.assertEqual(mocked.call_count, 3)
        self.assertTrue(agent._provider_rate_gate.billing_blocked("primary"))
        self.assertTrue(agent._provider_rate_gate.available("secondary"))
        self.assertFalse(agent._provider_rate_gate.available("primary"))

    def test_oversize_groq_request_is_rejected_before_network(self):
        agent = CerebrasResponsesAgent.__new__(CerebrasResponsesAgent)
        agent._messages_for_request = lambda: [
            {"role": "user", "content": "X" * 40000}]
        agent._tool_definitions = lambda **kwargs: []
        settings = SimpleNamespace(
            cerebras_fallback_to_groq=True, groq_api_key="not-a-real-key",
            groq_base_url="https://api.groq.com/openai/v1",
            ai_request_timeout_s=15.0, groq_agent_model="openai/gpt-oss-120b")
        with patch("jarvis_agent.agent_runtime.settings", settings):
            with self.assertRaisesRegex(AgentRuntimeUnavailable,
                                        "Fallback Groq écarté avant API"):
                agent._chat_via_groq_fallback(
                    tool_choice="auto", ms_football_only=False,
                    msf_tool_names=None)


if __name__ == "__main__":
    unittest.main()
