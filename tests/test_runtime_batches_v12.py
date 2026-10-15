"""Tool protocols stay paired across approvals and terminal MCP pauses."""
from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from jarvis_agent.agent_runtime import GroqResponsesAgent, OpenAIResponsesAgent, OllamaToolAgent
from jarvis_agent.mission_outcome_policy import failure_requires_review
from jarvis_agent.native_tools import AgentActionResult
from tests import test_mcp_autonomy_v12 as fixtures


class Gateway(fixtures.NativeStub):
    def openai_tools(self):
        return []

    def requires_confirmation(self, name):
        return name == "mcp__orbitdata__create_record"

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, approved))
        if name.startswith("mcp__"):
            return AgentActionResult(name, False, "External response lost", json.dumps({
                "dispatched": True, "outcome_unknown": True, "verified": False,
                "source": {"server_id": "orbitdata", "tool_name": name.split("__", 2)[2],
                           "trust": "external_unverified", "attempt_id": 1}}))
        return AgentActionResult(name, True, "Read", '{}')


def wire_response(*names, identity="fixture-response", text=""):
    output = [{"type": "function_call", "call_id": f"fixture-{index}",
        "name": name, "arguments": "{}"} for index, name in enumerate(names)]
    if text:
        output.append({"type": "message", "content": [{"type": "output_text", "text": text}]})
    return {"id": identity, "output": output}


class ScriptedResponses(OpenAIResponsesAgent):
    def __init__(self, tools, responses):
        super().__init__(tools)
        self.api_key = "fixture-only"
        self.post = Mock(side_effect=responses)

    def _post(self, payload):
        return self.post(payload)


class RuntimeBatchTests(unittest.TestCase):
    def test_approved_unknown_pauses_chat_before_another_model_request(self):
        tools = Gateway()
        agent = fixtures.ScriptedAgent(tools, [fixtures.response(
            ("mcp__orbitdata__create_record", {}), ("get_current_time", {}))])
        first = agent.run("Crée un record et affiche l'heure.")
        self.assertIn("confirmation", first.text.lower())
        self.assertEqual(tools.calls, [])
        second = agent.run("oui")
        self.assertEqual(second.pause_reason, "mcp_outcome_unknown")
        self.assertEqual(agent.chat.call_count, 1)
        self.assertEqual(tools.calls, [("mcp__orbitdata__create_record", True)])
        calls = {c["id"] for m in agent._messages for c in m.get("tool_calls", [])}
        replies = {m.get("tool_call_id") for m in agent._messages if m.get("role") == "tool"}
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls, replies)

    def test_denied_call_is_known_not_dispatched_and_batch_is_paired(self):
        tools = Gateway()
        agent = fixtures.ScriptedAgent(tools, [fixtures.response(
            ("mcp__orbitdata__create_record", {}), ("get_current_time", {})),
            fixtures.response(text="Action annulée.")])
        agent.run("Prépare une création.")
        result = agent.run("non")
        self.assertEqual(tools.calls, [])
        self.assertFalse(failure_requires_review(result.actions))
        calls = {c["id"] for m in agent._messages for c in m.get("tool_calls", [])}
        replies = {m.get("tool_call_id") for m in agent._messages if m.get("role") == "tool"}
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls, replies)

    def test_responses_pause_retains_unsubmitted_tool_outputs_for_next_user_turn(self):
        for continuation in (True, False):
            with self.subTest(continuation=continuation):
                tools = Gateway()
                agent = ScriptedResponses(tools, [wire_response(
                    "mcp__orbitdata__list_records", "get_current_time"),
                    wire_response(text="Mission suspendue.", identity="second-response")])
                agent.supports_response_continuation = continuation
                first = agent.run("Consulte orbitdata.")
                self.assertEqual(first.pause_reason, "mcp_outcome_unknown")
                self.assertEqual(agent.post.call_count, 1)
                self.assertEqual(tools.calls, [("mcp__orbitdata__list_records", False)])
                agent.run("Explique la suspension.")
                sent = agent.post.call_args.args[0]["input"]
                outputs = {item["call_id"] for item in sent
                           if isinstance(item, dict) and item.get("type") == "function_call_output"}
                self.assertEqual(outputs, {"fixture-0", "fixture-1"})

    def test_responses_confirmation_keeps_prior_read_and_deferred_outputs(self):
        tools = Gateway()
        agent = ScriptedResponses(tools, [wire_response(
            "get_current_time", "mcp__orbitdata__create_record", "list_windows")])
        agent.run("Lis l'heure puis crée le record.")
        result = agent.run("oui")
        self.assertEqual(result.pause_reason, "mcp_outcome_unknown")
        self.assertEqual(agent.post.call_count, 1)
        self.assertEqual(tools.calls, [("get_current_time", False),
                                      ("mcp__orbitdata__create_record", True)])
        outputs = {x["call_id"] for x in agent._pending_tool_outputs}
        self.assertEqual(outputs, {"fixture-0", "fixture-1", "fixture-2"})

    def test_ollama_protocol_also_stops_after_unknown_mcp_read(self):
        tools = Gateway()
        agent = OllamaToolAgent(tools)
        agent._post = Mock(return_value={"message": {"tool_calls": [
            {"function": {"name": name, "arguments": {}}}
            for name in ("mcp__orbitdata__list_records", "get_current_time")]}})
        result = agent.run("Consulte orbitdata.")
        self.assertEqual(result.pause_reason, "mcp_outcome_unknown")
        self.assertEqual(agent._post.call_count, 1)
        self.assertEqual(tools.calls, [("mcp__orbitdata__list_records", False)])

    def test_responses_provider_failure_does_not_lose_unsubmitted_source_outputs(self):
        tools = Gateway()
        agent = ScriptedResponses(tools, [wire_response("mcp__orbitdata__list_records"),
            RuntimeError("429 fixture provider limit"),
            wire_response(text="Mission toujours suspendue.", identity="last-response")])
        agent.run("Consulte orbitdata.")
        with self.assertRaisesRegex(RuntimeError, "429"):
            agent.run("Explique la suspension.")
        self.assertEqual(len(agent._pending_tool_outputs), 1)
        agent.run("Reprends uniquement l'explication.")
        sent = agent.post.call_args.args[0]["input"]
        self.assertTrue(any(isinstance(item, dict) and item.get("call_id") == "fixture-0"
                            for item in sent))
        self.assertEqual(tools.calls, [("mcp__orbitdata__list_records", False)])


if __name__ == "__main__":
    unittest.main()
