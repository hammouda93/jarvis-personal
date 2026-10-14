"""Live-log regressions: prior MCP unknown must be explained, not silently reset.

All entities synthetic. No provider, network, accounts, mission mutation or
persistent Memory V5 writes. Read tools remain operator-scoped.
"""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.request_context import RequestContext
from jarvis_agent.mission_progress import partial_mission_report
from jarvis_agent.native_tools import AgentActionResult


class EmptyNative:
    def ollama_tools(self):
        return []
    def requires_confirmation(self, name):
        return False


def tool(name, *, required=()):
    return {"type": "function", "function": {
        "name": name,
        "description": "Authorized synthetic observational tool",
        "parameters": {"type": "object", "properties": {},
                       "required": list(required)},
    }}


class StaleMCPReadReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.registry = MCPRegistry(Path(temp.name) / "servers.json")
        self.registry.add_http("orbitdata", "https://orbitdata.example/mcp")
        self.registry.set_enabled("orbitdata", True)
        self.registry.discover("orbitdata", [
            {"name": "list_records", "input_schema": {
                "type": "object", "properties": {
                    "account": {"type": "string"},
                }, "required": ["account"]}},
            {"name": "get_me", "input_schema": {
                "type": "object", "properties": {}
            }},
        ])
        self.registry.note_successful_discovery("orbitdata")
        self.registry.set_runtime_enabled(True)
        self.registry.set_auto_readonly("orbitdata", True)
        self.transport = Mock()
        self.adapter = MCPToolRegistry(
            EmptyNative(), registry=self.registry, transport=self.transport,
            runtime_gate=self.registry.runtime_enabled,
        )
        self.activity = self.registry.activity()
        self.old_attempt = self.activity.reserve(
            "orbitdata", "call", self.registry.get_server("orbitdata"),
            tool="list_records")
        self.activity.finish(self.old_attempt, "unknown")

    def test_existing_unknown_blocks_new_read_without_dispatch(self):
        result = self.adapter.execute(
            "mcp__orbitdata__list_records", {"account": "example"})
        data = json.loads(result.detail)
        self.assertFalse(result.success)
        self.assertEqual(data["guard"], "mcp_unknown_requires_review")
        self.assertFalse(data["dispatched"])
        self.assertTrue(data["outcome_unknown"])
        self.assertTrue(data["blocked_by_previous_attempt"])
        self.assertEqual(data["previous_attempt_id"], self.old_attempt)
        self.assertEqual(data["previous_outcome"], "unknown")
        self.assertEqual(data["server_id"], "orbitdata")
        self.assertEqual(data["tool_name"], "list_records")
        self.assertTrue(data["reviewable"])
        self.transport.call_tool.assert_not_called()

    def test_partial_report_distinguishes_reads_and_old_blocker(self):
        identity = AgentActionResult(
            "mcp__orbitdata__get_me", True, "read", json.dumps({
                "source": {"server_id": "orbitdata", "tool_name": "get_me",
                           "trust": "external_unverified"},
                "dispatched": True, "outcome_unknown": False,
                "mcp_output": '{"login":"PRIVATE-EXAMPLE"}',
            }))
        blocked = self.adapter.execute(
            "mcp__orbitdata__list_records", {"account": "example"})
        report = partial_mission_report((identity, blocked))
        self.assertIn("Lectures MCP reçues", report)
        self.assertIn("mcp__orbitdata__get_me", report)
        self.assertIn("Lecture MCP bloquée : orbitdata/list_records", report)
        self.assertIn("Tentative précédente #" + str(self.old_attempt), report)
        self.assertIn("MCP Control Center", report)
        self.assertIn("Ce nouvel appel n'a pas été envoyé", report)
        self.assertNotIn("PRIVATE-EXAMPLE", report)
        self.assertIn("non certifiée", report)


class FirstTurnRankingTests(unittest.TestCase):
    def test_possessive_request_exposes_identity_before_required_owner_lists(self):
        context = RequestContext(enabled=True)
        context.begin_turn("Inspecte mon dépôt orbitdata: commits et pull requests")
        definitions = [
            tool("mcp__orbitdata__list_pull_requests", required=("owner",)),
            tool("mcp__orbitdata__list_commits", required=("owner",)),
            tool("mcp__orbitdata__get_me"),
            tool("mcp__other__get_me"),
        ]
        chosen = [
            d["function"]["name"]
            for d in context.prepare_tools(definitions)
            if d.get("type") == "function"
        ]
        self.assertIn("mcp__orbitdata__get_me", chosen)
        self.assertIn("mcp__orbitdata__list_pull_requests", chosen)
        self.assertLess(chosen.index("mcp__orbitdata__get_me"),
                        chosen.index("mcp__orbitdata__list_pull_requests"))
        self.assertLess(chosen.index("mcp__orbitdata__get_me"),
                        chosen.index("mcp__orbitdata__list_commits"))
        # Other connected services remain governed by their own grants.

    def test_service_tools_are_never_granted_by_ranking(self):
        context = RequestContext(enabled=True)
        context.begin_turn("Inspecte mon compte orbitdata")
        selected = {
            d["function"]["name"] for d in context.prepare_tools([
                tool("mcp__orbitdata__get_me"),
                tool("mcp__orbitdata__delete_account"),
            ])
        }
        self.assertNotIn("mcp__orbitdata__delete_account", selected)


if __name__ == "__main__":
    unittest.main()
