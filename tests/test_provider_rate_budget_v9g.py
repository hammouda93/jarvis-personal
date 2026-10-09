"""Provider admission checks avoid repeated 429 and predictable Groq 413."""
from __future__ import annotations

from types import SimpleNamespace
from email.utils import formatdate
import unittest
from unittest.mock import patch

from jarvis_agent.provider_rate_budget import ProviderRateGate, groq_fallback_preflight
from jarvis_agent.agent_runtime import (
    AgentRuntimeUnavailable, CerebrasResponsesAgent, GroqResponsesAgent,
)


class ProviderRateBudgetTests(unittest.TestCase):
    def test_groq_sdk_hidden_retries_are_disabled(self):
        agent = GroqResponsesAgent.__new__(GroqResponsesAgent)
        agent.api_key, agent.base_url, agent._client = "fixture", "https://fixture.example", None
        with patch("openai.OpenAI") as constructor:
            agent._get_client()
        self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)

    def test_actual_request_compaction_preserves_transcript_and_records_returned_usage(self):
        import json
        from unittest.mock import Mock
        agent = GroqResponsesAgent.__new__(GroqResponsesAgent)
        agent.provider_name = "groq"
        agent._provider_rate_gate = ProviderRateGate()
        content = json.dumps({"success": True, "detail": "x" * 2000})
        messages = [{"role": "system", "content": "mission constraints"},
            {"role": "tool", "name": "browser_find", "tool_call_id": "one", "content": content},
            {"role": "tool", "name": "browser_find", "tool_call_id": "two", "content": content}]
        agent._messages_for_request = lambda: messages
        agent._tool_definitions = lambda **kwargs: []
        agent.model, agent.reasoning_effort = "fixture", "low"
        client = Mock()
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=110, completion_tokens=9))
        client.chat.completions.create.return_value = response
        agent._get_client = lambda: client
        with patch("jarvis_agent.active_mission_supervisor.reserve_model_request"), \
                patch("jarvis_agent.active_mission_supervisor.record_model_usage") as usage:
            self.assertIs(agent._chat(), response)
        sent = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn('"request_deduplicated":true', sent[1]["content"])
        self.assertEqual(sent[2]["content"], content)
        self.assertEqual(messages[1]["content"], content)
        usage.assert_called_once_with(response.usage, "groq")
    def test_numeric_and_date_retry_after_survive_exception_wrapping(self):
        for value, expected in (("120", 120), ("2.5", 2.5), (formatdate(1120, usegmt=True), 120)):
            error = RuntimeError("provider failure")
            error.response = SimpleNamespace(status_code=429, headers={"Retry-After": value})
            wrapper = AgentRuntimeUnavailable("wrapped provider failure")
            wrapper.__cause__ = error
            gate = ProviderRateGate(clock=lambda: 100, wall_time=lambda: 1000)
            self.assertTrue(gate.note_failure("primary", wrapper))
            self.assertEqual(gate.remaining("primary"), expected)
            self.assertTrue(gate.available("secondary"))

    def test_invalid_retry_after_uses_default_and_structured_status_wins_over_text(self):
        for value in ("nan", "inf", "-1", "0", "invalid date", "x" * 300):
            error = RuntimeError("429")
            error.response = SimpleNamespace(status_code=429, headers={"retry-after": value})
            gate = ProviderRateGate(clock=lambda: 100)
            gate.note_failure("p", error)
            self.assertEqual(gate.remaining("p"), 60)
        error.response.status_code = 500
        self.assertFalse(ProviderRateGate().note_failure("p", error))

    def test_groq_fallback_429_is_not_reissued_and_budgets_are_not_reserved_again(self):
        from unittest.mock import Mock
        agent = CerebrasResponsesAgent.__new__(CerebrasResponsesAgent)
        agent._provider_rate_gate = ProviderRateGate(clock=lambda: 100)
        agent._messages_for_request = lambda: [{"role": "user", "content": "hello"}]
        agent._tool_definitions = lambda **kwargs: []
        config = SimpleNamespace(cerebras_fallback_to_groq=True, groq_api_key="fixture",
            groq_base_url="https://fixture.example", ai_request_timeout_s=15, groq_agent_model="fixture",
            groq_reasoning_effort="low")
        client = Mock()
        error = RuntimeError("429")
        error.response = SimpleNamespace(status_code=429, headers={"retry-after": "120"})
        client.chat.completions.create.side_effect = error
        with patch("openai.OpenAI", return_value=client), patch("jarvis_agent.agent_runtime.settings", config), \
                patch("jarvis_agent.active_mission_supervisor.reserve_model_request") as reserve:
            for _ in range(2):
                with self.assertRaises(AgentRuntimeUnavailable):
                    agent._chat_via_groq_fallback(tool_choice="auto", ms_football_only=False, msf_tool_names=None)
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual(reserve.call_count, 1)
        self.assertEqual(agent._provider_rate_gate.remaining("groq_fallback"), 120)

    def test_standalone_groq_has_same_cooldown_and_no_second_network_call(self):
        from unittest.mock import Mock
        agent = GroqResponsesAgent.__new__(GroqResponsesAgent)
        agent.provider_name = "groq"
        agent._provider_rate_gate = ProviderRateGate(clock=lambda: 100)
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("429")
        agent._get_client = lambda: client
        agent._messages_for_request = lambda: [{"role": "user", "content": "hello"}]
        agent._tool_definitions = lambda **kwargs: []
        agent.model, agent.reasoning_effort = "fixture", "low"
        with patch("jarvis_agent.active_mission_supervisor.reserve_model_request"):
            for _ in range(2):
                with self.assertRaises(AgentRuntimeUnavailable):
                    agent._chat()
        self.assertEqual(client.chat.completions.create.call_count, 1)
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
