"""Known failed MCP reads may discover an account without requesting mutation.

Synthetic servers only; no credentials, remote traffic, persistent memory or
provider-specific usernames.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from jarvis_agent.mcp_mission_read_recovery import discovery_followup
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.mission_outcome_policy import failure_requires_review


class LocalStub:
    def ollama_tools(self):
        return []

    def requires_confirmation(self, name):
        return False


class KnownReadDiscoveryTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.registry = MCPRegistry(Path(workspace.name) / "config.json")
        for server in ("ledgerx", "unrelated"):
            self.registry.add_http(server, f"https://{server}.example/mcp")
            self.registry.set_enabled(server, True)
        self.registry.discover("ledgerx", [
            {"name": "list_records", "input_schema": {
                "type": "object",
                "properties": {"account": {"type": "string"}},
                "required": ["account"],
            }},
            {"name": "get_me", "input_schema": {
                "type": "object", "properties": {},
            }},
            {"name": "get_teams", "input_schema": {
                "type": "object", "properties": {},
            }},
            {"name": "create_record", "input_schema": {
                "type": "object", "properties": {},
            }},
        ])
        self.registry.discover("unrelated", [
            {"name": "get_me", "input_schema": {
                "type": "object", "properties": {},
            }},
        ])
        for server in ("ledgerx", "unrelated"):
            self.registry.note_successful_discovery(server)
        self.registry.set_runtime_enabled(True)
        self.registry.set_auto_readonly("ledgerx", True)
        self.registry.set_auto_readonly("unrelated", True)
        self.transport = Mock()
        self.transport.call_tool.return_value = {
            "success": False,
            "message": "Resource not found",
        }
        self.adapter = MCPToolRegistry(
            LocalStub(), registry=self.registry, transport=self.transport,
            runtime_gate=self.registry.runtime_enabled,
        )

    def test_known_read_failure_suggests_same_server_approved_identity(self):
        result = self.adapter.execute("mcp__ledgerx__list_records", {
            "account": "invalid-resource-owner",
        })
        detail = json.loads(result.detail)
        self.assertFalse(result.success)
        self.assertTrue(detail["dispatched"])
        self.assertTrue(detail["safe_read_failure"])
        self.assertFalse(detail["outcome_unknown"])
        self.assertIn("mcp__ledgerx__get_me", detail["discovery_candidates"])
        self.assertNotIn("mcp__unrelated__get_me", detail["discovery_candidates"])
        self.assertNotIn("mcp__ledgerx__create_record", detail["discovery_candidates"])
        self.assertFalse(failure_requires_review([result]))
        recovery = discovery_followup([result])
        self.assertEqual(recovery["failure_kind"], "known_read_error")
        self.assertEqual(recovery["approved_read_candidates"][0],
                         "mcp__ledgerx__get_me")
        self.assertEqual(self.transport.call_tool.call_count, 1)

    def test_unapproved_identity_is_not_suggested(self):
        self.registry.allow_tool("ledgerx", "get_me", False)
        result = self.adapter.execute("mcp__ledgerx__list_records", {
            "account": "invalid-resource-owner",
        })
        detail = json.loads(result.detail)
        self.assertNotIn("mcp__ledgerx__get_me", detail["discovery_candidates"])

    def test_uncertain_or_mutating_result_never_becomes_read_recovery(self):
        self.registry.allow_tool("ledgerx", "create_record", True)
        self.transport.call_tool.return_value = {
            "success": False,
            "message": "Effect unknown",
        }
        result = self.adapter.execute(
            "mcp__ledgerx__create_record", {}, approved=True
        )
        self.assertTrue(json.loads(result.detail)["outcome_unknown"])
        self.assertIsNone(discovery_followup([result]))
        self.assertTrue(failure_requires_review([result]))


if __name__ == "__main__":
    unittest.main()
