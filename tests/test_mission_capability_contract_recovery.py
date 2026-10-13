"""Generic capability selection and safe local-failure mission recovery.

All names are fixtures. No GitHub, third-party app, network, browser or
Windows action is contacted; no permissions are granted.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from jarvis_agent.request_context import RequestContext, ContextToolRegistry
from jarvis_agent.mission_outcome_policy import failure_requires_review
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime


def tool(name):
    return {"type": "function", "function": {
        "name": name, "description": "Original capability",
        "parameters": {"type": "object", "properties": {},
                       "additionalProperties": False}}}


class LocalCapabilityContractTests(unittest.TestCase):
    def setUp(self):
        self.context = RequestContext(enabled=True)
        self.context.begin_turn("Inspect records using an already connected read-only service")
        self.schemas = [tool("mcp__fixture__list_records"),
                        tool("mcp__fixture__read_status"),
                        tool("browser_list_tabs")]
        self.context.prepare_tools(self.schemas, browser_mode=False)
        self.delegate = Mock()
        self.registry = ContextToolRegistry(self.delegate, self.context)

    def test_single_existing_name_can_be_normalized_without_grant_or_dispatch(self):
        result = self.registry.execute(
            "request_tool_capabilities", {"names": "mcp__fixture__list_records"})
        self.assertTrue(result.success)
        detail = json.loads(result.detail)
        self.assertEqual(detail["selected"], ["mcp__fixture__list_records"])
        self.assertTrue(detail["normalized_singleton"])
        self.assertIs(detail["dispatched"], False)
        self.assertIs(detail["permissions_changed"], False)
        self.assertIs(detail["verified"], False)
        self.delegate.execute.assert_not_called()
        restored = self.context.prepare_tools(self.schemas)
        self.assertIn("mcp__fixture__list_records",
                      [s["function"]["name"] for s in restored if s.get("type") == "function"])

    def test_valid_array_preserves_original_contract(self):
        result = self.registry.execute("request_tool_capabilities", {
            "names": ["mcp__fixture__list_records", "mcp__fixture__read_status"]})
        self.assertTrue(result.success)
        self.assertFalse(json.loads(result.detail)["normalized_singleton"])
        self.assertEqual(len(json.loads(result.detail)["selected"]), 2)

    def test_invalid_selection_is_local_known_nondispatch_not_manual_review(self):
        for arguments in (
            {"names": "mcp__fixture__delete_records"},
            {"names": ["mcp__fixture__delete_records"]},
            {"names": []}, {"names": 234},
            {"names": ["mcp__fixture__list_records", "mcp__fixture__list_records"]},
            {"names": {"tool": "mcp__fixture__list_records"}},
            {"names": None},
            {},
        ):
            with self.subTest(arguments=arguments):
                self.context.prepare_tools(self.schemas)
                result = self.registry.execute("request_tool_capabilities", arguments)
                self.assertFalse(result.success)
                detail = json.loads(result.detail)
                self.assertEqual(detail["guard"], "invalid_local_context_request")
                self.assertFalse(detail["dispatched"])
                self.assertFalse(detail["outcome_unknown"])
                self.assertFalse(detail["permissions_changed"])
                self.assertTrue(detail["recoverable"])
                self.assertFalse(failure_requires_review([result]))
                self.delegate.execute.assert_not_called()

    def test_invalid_archived_observation_is_also_known_local_failure(self):
        result = self.registry.execute("read_observation_evidence", {
            "evidence_id": "nonexistent"})
        self.assertFalse(result.success)
        self.assertFalse(failure_requires_review([result]))
        self.assertFalse(json.loads(result.detail)["dispatched"])
        self.delegate.execute.assert_not_called()

    def test_local_read_failure_does_not_block_persistent_mission(self):
        action = self.registry.execute("request_tool_capabilities", {
            "names": "mcp__fixture__not_discovered"})
        with tempfile.TemporaryDirectory() as root:
            agent = Mock(spec=["run", "run_with_context"])
            agent.run.return_value = SimpleNamespace(
                text="Le catalogue local n'a pas cette capacité.", actions=(action,))
            agent.run_with_context.return_value = agent.run.return_value
            runtime = LiveMissionContinuityRuntime(agent, base_dir=Path(root))
            mid = runtime.begin_mission("Inspect a connected dataset, then summarize.")
            runtime.run("Inspect a connected dataset, then summarize.")
            snap = runtime.mission_snapshot(mid)
            self.assertEqual(snap["status"], "waiting_external")
            self.assertFalse(snap["manual_review_required"])
            self.assertFalse(snap["goal_verified"])
            self.assertEqual(snap["last_failed_action_count"], 1)
            # A restart cannot promote local failure to an unknown mutation.
            restarted = LiveMissionContinuityRuntime(agent, base_dir=Path(root))
            restored = restarted.mission_snapshot(mid)
            self.assertFalse(restored["manual_review_required"])
            self.assertFalse(restored["goal_verified"])


if __name__ == "__main__":
    unittest.main()
