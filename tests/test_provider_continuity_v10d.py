"""Provider transitions and response integrity, without network or desktop calls."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import AgentRuntimeUnavailable, CerebrasResponsesAgent, GroqResponsesAgent
from jarvis_agent.config import settings
from jarvis_agent.operator_telemetry import runtime_model_snapshot
from test_agent_runtime import FakeGroqAgent, FakeTools


def response(*, text="", arguments=None, finish_reason="stop", identity="observed-call"):
    calls = [] if arguments is None else [SimpleNamespace(id=identity,
        function=SimpleNamespace(name="open_application", arguments=arguments))]
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=31, completion_tokens=7, total_tokens=38),
        choices=[SimpleNamespace(finish_reason=finish_reason,
                                 message=SimpleNamespace(content=text, tool_calls=calls))])


class ProviderContinuityTests(unittest.TestCase):
    def test_truncated_tool_response_never_dispatches_even_valid_arguments(self):
        tools = FakeTools()
        agent = FakeGroqAgent(tools, [])
        with patch.object(agent, "_chat", return_value=response(arguments='{"name":"Notepad"}', finish_reason="length")):
            with self.assertRaisesRegex(AgentRuntimeUnavailable, "incomplete"):
                agent.run("Ouvre Notepad")
        self.assertEqual(tools.calls, [])
        self.assertFalse(any(message.get("tool_calls") for message in agent._messages))

    def test_invalid_or_nonobject_arguments_are_not_replaced_by_empty_mutation_args(self):
        for args in ('{"name":', '[]', 'null', '"text"', '{"name":"A","name":"B"}', '{"value":NaN}'):
            with self.subTest(args=args):
                tools = FakeTools()
                agent = FakeGroqAgent(tools, [])
                with patch.object(agent, "_chat", return_value=response(arguments=args)):
                    with self.assertRaisesRegex(AgentRuntimeUnavailable, "invalid_tool"):
                        agent.run("Ouvre Notepad")
                self.assertEqual(tools.calls, [])

    def test_missing_arguments_are_not_fabricated_and_whole_batch_is_checked(self):
        tools = FakeTools()
        agent = FakeGroqAgent(tools, [])
        batch = response(arguments='{"name":"Notepad"}')
        batch.choices[0].message.tool_calls.append(SimpleNamespace(id="second",
            function=SimpleNamespace(name="open_application", arguments=None)))
        with patch.object(agent, "_chat", return_value=batch):
            with self.assertRaisesRegex(AgentRuntimeUnavailable, "invalid_tool"):
                agent.run("Ouvre Notepad")
        self.assertEqual(tools.calls, [])

    def test_missing_call_identity_is_not_fabricated(self):
        agent = FakeGroqAgent(FakeTools(), [])
        with patch.object(agent, "_chat", return_value=response(arguments="{}", identity=None)):
            with self.assertRaisesRegex(AgentRuntimeUnavailable, "invalid_tool"):
                agent.run("Ouvre Notepad")

    def test_supervised_browser_compaction_keeps_constraints_and_completed_proofs(self):
        agent = GroqResponsesAgent(FakeTools())
        agent._messages = [{"role": "system", "content": "approved mission"},
            {"role": "user", "content": "Never send without confirmation; preserve witness tab"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "proof-1", "type": "function",
                "function": {"name": "browser_write", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "proof-1", "name": "browser_write", "content": "source proof"}]
        for index in range(7):
            agent._messages.extend([{"role": "user", "content": f"step {index}"},
                                    {"role": "assistant", "content": "continue"}])
        agent._request_turn_start_index = len(agent._messages)
        agent._ephemeral_context = "BROWSER_GROUNDING_READ_ONLY:{}"
        before = copy.deepcopy(agent._messages)
        with patch("jarvis_agent.active_mission_supervisor.execution_scope_active", return_value=True):
            self.assertEqual(agent._messages_for_request(), before)
        self.assertEqual(agent._messages, before)

    def test_actual_402_429_groq_transition_keeps_context_and_does_not_replay_mutation(self):
        tools = FakeTools()
        config = replace(settings, cerebras_api_key="fixture-primary", cerebras_secondary_api_key="fixture-secondary",
                         groq_api_key="fixture-groq", cerebras_fallback_to_groq=True)
        primary, secondary, groq = Mock(), Mock(), Mock()
        primary.chat.completions.create.side_effect = [response(arguments='{"name":"Notepad"}', finish_reason="tool_calls"),
                                                     RuntimeError("402 payment_required")]
        secondary.chat.completions.create.side_effect = RuntimeError("429 quota")
        groq.chat.completions.create.return_value = response(text="Application ouverte.")
        with patch("jarvis_agent.agent_runtime.settings", config), patch("openai.OpenAI", return_value=groq):
            agent = CerebrasResponsesAgent(tools)
            with patch.object(agent, "_get_client", side_effect=lambda: secondary if agent.api_key == "fixture-secondary" else primary):
                result = agent.run_with_context("Ouvre Notepad", "MISSION: open once; preserve permissions; no send")
        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(result.actions[0].name, "open_application")
        sent = groq.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("preserve permissions", sent[0]["content"])
        self.assertTrue(any(message.get("tool_call_id") == "observed-call" for message in sent))
        state = runtime_model_snapshot(agent)
        self.assertEqual(state["effective_provider"], "groq")
        self.assertEqual(state["effective_credential"], "groq_fallback")
        self.assertEqual(state["reported_usage"]["total_tokens"], 38)
        self.assertTrue(agent._provider_rate_gate.billing_blocked("primary"))
        self.assertFalse(agent._provider_rate_gate.available("secondary"))

    def test_no_provider_is_reported_before_an_actual_response(self):
        state = runtime_model_snapshot(CerebrasResponsesAgent(FakeTools()))
        self.assertEqual(state["effective_provider"], "")
        self.assertIsNone(state["reported_usage"])

    def test_missing_api_usage_does_not_reuse_previous_response_counts(self):
        agent = CerebrasResponsesAgent(FakeTools())
        agent._note_model_response(response(text="First"), "cerebras", "primary")
        agent.reliability_last_usage = {"total_tokens": 38}
        final = response(text="Final")
        final.usage = None
        agent._note_model_response(final, "groq", "groq_fallback")
        self.assertIsNone(runtime_model_snapshot(agent)["reported_usage"])

    def test_secondary_lane_is_observed_only_after_its_response(self):
        primary, secondary = Mock(), Mock()
        primary.chat.completions.create.side_effect = RuntimeError("402 payment_required")
        secondary.chat.completions.create.return_value = response(text="Bonjour.")
        config = replace(settings, cerebras_api_key="fixture-primary", cerebras_secondary_api_key="fixture-secondary")
        with patch("jarvis_agent.agent_runtime.settings", config):
            agent = CerebrasResponsesAgent(FakeTools())
            with patch.object(agent, "_get_client", side_effect=lambda: secondary if agent.api_key == "fixture-secondary" else primary):
                agent.run("Bonjour")
        self.assertEqual(runtime_model_snapshot(agent)["effective_credential"], "secondary")
        self.assertEqual(agent.api_key, "fixture-primary")

