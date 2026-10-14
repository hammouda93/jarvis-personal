"""MCP capability discovery without provider-specific usernames or rights escalation.

All servers and identities are synthetic; no network, personal memory or skills.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from jarvis_agent.request_context import RequestContext
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry


class ReadDelegate:
    def ollama_tools(self):
        return []

    def requires_confirmation(self, name):
        return False


def schema(name, *, required=(), properties=None):
    return {
        "name": name, "description": "Fixture observational tool",
        "input_schema": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
        },
    }


def llm_tool(alias, *, required=(), properties=None):
    return {
        "type": "function",
        "function": {
            "name": alias, "description": "Already authorized local catalog",
            "parameters": {
                "type": "object", "properties": properties or {},
                "required": list(required),
            },
        },
    }


class FirstTurnToolRankingTests(unittest.TestCase):
    def test_service_identity_observation_is_not_hidden_by_initial_limit(self):
        ctx = RequestContext(enabled=True)
        ctx.begin_turn("Analyse my repo and commits on fixture service")
        # Important tools arrive after a long inventory, simulating a model
        # that cannot load every approved MCP schema at once.
        tools = [
            llm_tool("mcp__fixture__list_commits", required=["owner"]),
            llm_tool("mcp__fixture__list_repos", required=["owner"]),
            llm_tool("mcp__fixture__list_reviews", required=["owner"]),
            llm_tool("mcp__fixture__list_branches", required=["owner"]),
            llm_tool("mcp__fixture__get_commit", required=["owner"]),
            llm_tool("mcp__other__get_me"),
            llm_tool("mcp__fixture__get_me"),
            llm_tool("mcp__fixture__delete_repo"),
        ]
        chosen = {
            t["function"]["name"] for t in ctx.prepare_tools(tools)
            if t.get("type") == "function"
        }
        self.assertIn("mcp__fixture__get_me", chosen)
        self.assertNotIn("mcp__fixture__delete_repo", chosen)
        self.assertIn("request_tool_capabilities", chosen)
        self.assertNotIn("mcp__other__get_me", chosen)

    def test_same_rules_apply_to_an_unfamiliar_server(self):
        ctx = RequestContext(enabled=True)
        ctx.begin_turn("Review my records in orbitdata")
        definitions = [
            llm_tool("mcp__orbitdata__list_records", required=["account"]),
            llm_tool("mcp__orbitdata__get_me"),
            llm_tool("mcp__other__list_records", required=["account"]),
        ]
        chosen = {
            t["function"]["name"] for t in ctx.prepare_tools(definitions)
            if t.get("type") == "function"
        }
        self.assertIn("mcp__orbitdata__get_me", chosen)
        self.assertIn("mcp__orbitdata__list_records", chosen)


class ToolFailureDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.registry = MCPRegistry(Path(temp.name) / "mcp.json")
        for service in ("orbitdata", "unrelated"):
            self.registry.add_http(service, f"https://{service}.example/mcp")
            self.registry.set_enabled(service, True)
        self.registry.discover("orbitdata", [
            schema("list_records", required=["account", "resource"],
                   properties={
                       "account": {"type": "string"},
                       "resource": {"type": "string"},
                   }),
            schema("get_me"),
            schema("list_spaces", required=["account"],
                   properties={"account": {"type": "string"}}),
            schema("create_record"),
        ])
        self.registry.discover("unrelated", [schema("get_me")])
        for service in ("orbitdata", "unrelated"):
            self.registry.note_successful_discovery(service)
        self.registry.set_runtime_enabled(True)
        self.registry.set_auto_readonly("orbitdata", True)
        self.registry.set_auto_readonly("unrelated", True)
        self.registry.allow_tool("orbitdata", "create_record", True)
        self.transport = Mock()
        self.client = MCPToolRegistry(
            ReadDelegate(), registry=self.registry, transport=self.transport,
            runtime_gate=self.registry.runtime_enabled,
        )

    def test_placeholder_error_identifies_safe_zero_arg_candidates(self):
        receipt = self.client.execute("mcp__orbitdata__list_records", {
            "account": "<owner>", "resource": "db1"
        })
        result = json.loads(receipt.detail)
        self.assertEqual(result["guard"], "unresolved_argument_template")
        self.assertEqual(result["discovery_candidates"],
                         ["mcp__orbitdata__get_me"])
        self.assertFalse(result["dispatched"])
        self.assertFalse(result["outcome_unknown"])
        self.assertTrue(result["recoverable"])
        self.transport.call_tool.assert_not_called()
        self.assertEqual(self.registry.activity().summary("orbitdata")["calls_used"], 0)

    def test_discovery_hints_never_reveal_other_server_or_mutations(self):
        receipt = self.client.execute("mcp__orbitdata__list_records", {
            "account": "<owner>", "resource": "PRIVATE_VALUE"
        })
        result = json.loads(receipt.detail)
        self.assertNotIn("PRIVATE_VALUE", receipt.detail)
        self.assertNotIn("mcp__unrelated__get_me", result["discovery_candidates"])
        self.assertNotIn("mcp__orbitdata__create_record", result["discovery_candidates"])
        self.transport.call_tool.assert_not_called()

    def test_disabled_discovery_tool_is_not_suggested(self):
        self.registry.allow_tool("orbitdata", "get_me", False)
        receipt = self.client.execute("mcp__orbitdata__list_records", {
            "account": "<owner>", "resource": "db1"
        })
        result = json.loads(receipt.detail)
        self.assertEqual(result["discovery_candidates"], [])
        self.transport.call_tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
