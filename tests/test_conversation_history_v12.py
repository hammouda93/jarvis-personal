"""Keep useful observations and valid tool protocols across conversation turns."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from jarvis_agent.config import settings
from jarvis_agent.agent_runtime import GroqResponsesAgent
from jarvis_agent.request_context import RequestContext
from tests import test_mcp_autonomy_v12 as fixtures


def exchange(index):
    return [{"role": "assistant", "content": "", "tool_calls": [
        {"id": f"read-{index}", "type": "function", "function": {
            "name": "mcp__orbitdata__get_record", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"read-{index}",
         "name": "mcp__orbitdata__get_record", "content": f"source-{index}"}]


class ConversationHistoryTests(unittest.TestCase):
    def test_latest_source_remains_a_tool_result_for_the_followup(self):
        agent = GroqResponsesAgent(fixtures.NativeStub())
        agent._messages = [{"role": "system", "content": "Original policy"},
            {"role": "user", "content": "Lis mon record, garde les autres données."},
            *exchange(1), {"role": "assistant", "content": "Record lu."},
            {"role": "user", "content": "Explique-moi sa valeur."},
            {"role": "assistant", "content": "Voici l'explication."}]
        agent._trim_history()
        self.assertIn("source-1", [m.get("content") for m in agent._messages
                                  if m.get("role") == "tool"])
        self.assertEqual(agent._messages[0]["content"], "Original policy")
        # Remote source data must never be relabeled as user or system authority.
        self.assertFalse(any("source-1" in m.get("content", "")
                             for m in agent._messages if m.get("role") in {"user", "system"}))

    def test_tool_groups_are_bounded_and_never_leave_orphan_outputs(self):
        agent = GroqResponsesAgent(fixtures.NativeStub())
        agent._messages = [{"role": "system", "content": "Policy"}]
        for index in range(20):
            agent._messages.extend([{"role": "user", "content": f"Read {index}"},
                *exchange(index), {"role": "assistant", "content": "Read complete"}])
        with patch("jarvis_agent.agent_runtime.settings",
                   replace(settings, agent_history_turns=6)):
            agent._trim_history()
        calls = {c["id"] for m in agent._messages for c in m.get("tool_calls", [])}
        outputs = {m["tool_call_id"] for m in agent._messages if m.get("role") == "tool"}
        self.assertGreater(len(calls), 0)
        self.assertLessEqual(len(calls), 8)
        self.assertEqual(calls, outputs)
        self.assertIn("read-19", calls)
        self.assertEqual(len([m for m in agent._messages if m.get("role") == "user"]), 6)

    def test_session_reset_forgets_external_context_but_not_personal_memory(self):
        agent = GroqResponsesAgent(fixtures.NativeStub())
        agent.request_context = RequestContext(enabled=True)
        agent.request_context.archive_external_observation("mcp__orbitdata__get_me", {
            "source": {"attempt_id": 1}, "mcp_output": "PRIVATE-SOURCE"})
        agent.reset()
        self.assertFalse(agent.request_context.external_evidence)
        self.assertEqual(len(agent._messages), 1)

    def test_partial_mcp_report_uses_exact_identity_of_tools_with_punctuation(self):
        from jarvis_agent.mission_progress import partial_mission_report
        from jarvis_agent.native_tools import AgentActionResult
        receipt = AgentActionResult("mcp__orbitdata__list_records_v2", False, "blocked",
            json.dumps({"guard": "mcp_unknown_requires_review", "dispatched": False,
                "outcome_unknown": True, "blocked_by_previous_attempt": True,
                "server_id": "orbitdata", "tool_name": "list.records-v2",
                "previous_attempt_id": 7}))
        self.assertIn("orbitdata/list.records-v2", partial_mission_report([receipt]))

    def test_parameterized_profile_is_not_reused_as_the_default_identity(self):
        from types import SimpleNamespace
        from jarvis_agent.mcp_mission_read_recovery import prior_identity_observation
        read = SimpleNamespace(name="mcp__orbitdata__get_profile", success=True,
            detail=json.dumps({"dispatched": True, "outcome_unknown": False,
                "arguments_empty": False, "source": {"server_id": "orbitdata",
                    "tool_name": "get_profile", "trust": "external_unverified"}}))
        self.assertFalse(prior_identity_observation([read], read.name, {}))


if __name__ == "__main__":
    unittest.main()
