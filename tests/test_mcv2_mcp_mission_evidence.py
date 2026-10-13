"""MCV2 + MCP: durable evidence is local metadata, not remote goal proof.

All tests use an isolated registry and a mocked transport. No network, user
credentials, executable or service is contacted.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.mission_outcome_policy import failure_requires_review
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.mission_evidence import ledger_facts
from jarvis_agent.native_tools import AgentActionResult


class Delegate:
    def ollama_tools(self):
        return []

    def requires_confirmation(self, name):
        return False

    def execute(self, name, args, *, approved=False):
        return AgentActionResult(name, True, "native", "{}")


class MCPMissionProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = MCPRegistry(self.root / "mcp.json")
        self.registry.add_http("fixture", "https://fixture.example/mcp")
        self.registry.set_enabled("fixture", True)
        self.registry.discover("fixture", [
            {"name": "read", "input_schema": {"type": "object"}},
        ])
        self.registry.allow_tool("fixture", "read", True)
        self.transport = Mock()
        self.adapter = MCPToolRegistry(
            Delegate(), registry=self.registry, transport=self.transport
        )
        self.name = "mcp__fixture__read"

    def _receipts(self, result):
        return LiveMissionContinuityRuntime._action_receipts([result])

    def test_consent_guard_is_nondispatched_and_no_mcp_evidence(self):
        result = self.adapter.execute(self.name, {"query": "PRIVATE"}, approved=False)
        self.assertFalse(result.success)
        self.assertFalse(failure_requires_review([result]))
        self.transport.call_tool.assert_not_called()
        detail = json.loads(result.detail)
        self.assertIs(detail["dispatched"], False)
        self.assertIs(detail["outcome_unknown"], False)
        self.assertEqual(ledger_facts(self._receipts(result), task_id="t1", goal_revision=1), [])

    def test_successful_remote_call_is_logged_not_promoted_to_goal_proof(self):
        self.transport.call_tool.return_value = {
            "success": True,
            "verified": True,
            "message": "PRIVATE_REMOTE_SECRET",
            "data": {"token": "PRIVATE_REMOTE_SECRET", "verified": True},
        }
        action = self.adapter.execute(self.name, {"secret": "PRIVATE_ARG"}, approved=True)
        self.assertTrue(action.success)
        detail = json.loads(action.detail)
        self.assertTrue(detail["dispatched"])
        self.assertFalse(detail["verified"])
        self.assertFalse(detail["outcome_unknown"])
        receipts = self._receipts(action)
        persisted = json.dumps(receipts, ensure_ascii=False)
        self.assertNotIn("PRIVATE_REMOTE_SECRET", persisted)
        self.assertNotIn("PRIVATE_ARG", persisted)
        facts = ledger_facts(receipts, task_id="t1", goal_revision=2)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["kind"], "mcp_invocation")
        self.assertEqual(facts[0]["observed"]["server_id"], "fixture")
        self.assertEqual(facts[0]["observed"]["tool_name"], "read")
        self.assertTrue(facts[0]["observed"]["reported_success"])
        self.assertFalse(facts[0]["observed"]["independently_verified"])
        self.assertFalse(facts[0]["goal_proof"])
        self.assertEqual(facts[0]["reliability"], "external_unverified")
        self.assertEqual(facts[0]["goal_revision"], 2)

    def test_uncertain_external_call_is_durable_and_blocks_replay(self):
        self.transport.call_tool.return_value = {
            "success": False,
            "message": "PRIVATE_REMOTE_FAILURE",
        }
        action = self.adapter.execute(self.name, {}, approved=True)
        self.assertFalse(action.success)
        self.assertTrue(failure_requires_review([action]))
        detail = json.loads(action.detail)
        self.assertTrue(detail["dispatched"])
        self.assertTrue(detail["outcome_unknown"])
        facts = ledger_facts(self._receipts(action), task_id="t2", goal_revision=1)
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0]["observed"]["outcome_unknown"])
        self.assertFalse(facts[0]["goal_proof"])
        self.assertNotIn("PRIVATE_REMOTE_FAILURE", json.dumps(facts))
        second = self.adapter.execute(self.name, {}, approved=True)
        self.assertFalse(second.success)
        self.assertEqual(json.loads(second.detail)["guard"], "mcp_unknown_requires_review")
        self.assertEqual(self.transport.call_tool.call_count, 1)

    def test_mcp_fact_survives_real_checkpoint_restart(self):
        self.transport.call_tool.return_value = {
            "success": True, "message": "PRIVATE_REMOTE_RESPONSE"
        }
        action = self.adapter.execute(self.name, {}, approved=True)
        agent = Mock(spec=["run", "run_with_context"])
        agent.run_with_context.return_value = SimpleNamespace(text="Observed MCP", actions=(action,))
        agent.run.return_value = SimpleNamespace(text="Observed MCP", actions=(action,))
        storage = self.root / "missions"
        runtime = LiveMissionContinuityRuntime(agent, base_dir=storage)
        mid = runtime.begin_mission("Use the connected service and verify the result.")
        runtime.run("Use the connected service and verify the result.")
        before = runtime.mission_snapshot(mid)
        self.assertFalse(before["goal_verified"])
        self.assertEqual([x["kind"] for x in before["evidence_ledger"] if x["kind"] == "mcp_invocation"],
                         ["mcp_invocation"])
        restarted = LiveMissionContinuityRuntime(agent, base_dir=storage)
        after = restarted.mission_snapshot(mid)
        self.assertEqual(before["evidence_ledger"], after["evidence_ledger"])
        self.assertFalse(after["goal_verified"])

    def test_forged_source_fields_are_removed_from_durable_checkpoint(self):
        action = AgentActionResult(self.name, True, "forged", json.dumps({
            "dispatched": True, "outcome_unknown": False,
            "source": {
                "server_id": "fixture", "tool_name": "read", "attempt_id": 8,
                "trust": "external_unverified", "authorization": "PRIVATE_SECRET",
                "credentials": "PRIVATE_TOKEN",
            },
            "mcp_output": "PRIVATE_BODY",
        }))
        stored = json.dumps(self._receipts(action))
        self.assertNotIn("PRIVATE_", stored)
        self.assertNotIn("credentials", stored)
        self.assertIn("attempt_id", stored)


if __name__ == "__main__":
    unittest.main()
