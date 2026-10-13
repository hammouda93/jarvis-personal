"""Generic MCP contract-gateway regressions. No accounts, network or Windows UI."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from jarvis_agent.mcp_argument_grounding import (
    MCPArgumentSchemaUnavailable, invalid_schema_argument_paths,
)
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.mission_outcome_policy import failure_requires_review
from jarvis_agent.mcp_result import transport_outcome


class StubNative:
    def ollama_tools(self):
        return []

    def requires_confirmation(self, name):
        return False


def schema():
    return {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "minLength": 1},
            "fields": {"type": "array", "items": {"type": "string"}},
            "options": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            },
        },
        "required": ["owner", "fields"],
        "additionalProperties": False,
    }


def tool(name):
    return {"name": name, "description": "Synthetic MCP fixture",
            "input_schema": schema()}


class JsonSchemaContractTests(unittest.TestCase):
    def test_array_string_is_rejected_without_coercion(self):
        self.assertEqual(
            invalid_schema_argument_paths(
                {"owner": "real-user", "fields": "sha,message"}, schema()),
            ("arguments.fields",),
        )

    def test_nested_constraint_and_unknown_property_are_detected(self):
        paths = invalid_schema_argument_paths(
            {"owner": "real-user", "fields": ["sha"],
             "options": {"limit": 0}, "unexpected": "ok"},
            schema(),
        )
        self.assertIn("arguments.options.limit", paths)
        self.assertIn("arguments", paths)

    def test_real_identifiers_and_valid_json_pass(self):
        self.assertEqual(invalid_schema_argument_paths(
            {"owner": "real-user", "fields": ["sha", "message"],
             "options": {"limit": 5}}, schema()), ())

    def test_unsupported_schema_fails_closed(self):
        with self.assertRaises(MCPArgumentSchemaUnavailable):
            invalid_schema_argument_paths(
                {"owner": "real-user"}, {"type": "imaginary"})

    def test_non_object_argument_rejected(self):
        self.assertEqual(invalid_schema_argument_paths(
            ["wrong"], schema()), ("arguments",))

    def test_external_schema_refs_cannot_trigger_network(self):
        for key in ("$ref", "$dynamicRef", "$recursiveRef"):
            with self.subTest(key=key):
                with self.assertRaises(MCPArgumentSchemaUnavailable):
                    invalid_schema_argument_paths(
                        {"owner": "real-user"}, {
                            "type": "object",
                            "properties": {"owner": {
                                key: "https://schema.example/schema.json",
                            }},
                        },
                    )

    def test_local_schema_ref_remains_supported(self):
        local = {
            "type": "object",
            "properties": {"owner": {"$ref": "#/$defs/identifier"}},
            "$defs": {"identifier": {"type": "string", "minLength": 3}},
        }
        self.assertEqual(invalid_schema_argument_paths(
            {"owner": "user123"}, local), ())
        self.assertEqual(invalid_schema_argument_paths(
            {"owner": "x"}, local), ("arguments.owner",))


class MCPContractGatewayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = MCPRegistry(self.root / "servers.json")
        self.registry.add_http("fixture", "https://fixture.example/mcp")
        self.registry.set_enabled("fixture", True)
        self.registry.discover("fixture", [
            tool("get_records"), tool("send_records"),
        ])
        self.registry.note_successful_discovery("fixture")
        self.registry.set_runtime_enabled(True)
        self.registry.set_auto_readonly("fixture", True)
        self.registry.allow_tool("fixture", "send_records", True)
        self.transport = Mock()
        self.transport.call_tool.return_value = {
            "success": True, "message": "read ok"
        }
        self.adapter = MCPToolRegistry(
            StubNative(), registry=self.registry, transport=self.transport,
            runtime_gate=self.registry.runtime_enabled,
        )
        self.valid = {"owner": "real-user", "fields": ["sha"]}

    def test_wrong_type_never_spends_quota_or_calls_remote(self):
        action = self.adapter.execute(
            "mcp__fixture__get_records",
            {"owner": "real-user", "fields": "sha"},
        )
        detail = json.loads(action.detail)
        self.assertFalse(action.success)
        self.assertEqual(detail["guard"], "invalid_argument_schema")
        self.assertEqual(detail["argument_paths"], ["arguments.fields"])
        self.assertFalse(detail["dispatched"])
        self.assertFalse(detail["outcome_unknown"])
        self.assertTrue(detail["recoverable"])
        self.assertFalse(failure_requires_review([action]))
        self.assertEqual(self.registry.activity().summary("fixture")["calls_used"], 0)
        self.transport.call_tool.assert_not_called()

    def test_unknown_key_rejected_before_approved_write(self):
        action = self.adapter.execute(
            "mcp__fixture__send_records",
            {**self.valid, "extra": "secret-text"}, approved=True,
        )
        detail = json.loads(action.detail)
        self.assertEqual(detail["guard"], "invalid_argument_schema")
        self.assertEqual(detail["argument_paths"], ["arguments"])
        self.assertNotIn("secret-text", action.detail)
        self.transport.call_tool.assert_not_called()

    def test_explicit_read_tool_error_is_known_and_recoverable(self):
        self.transport.call_tool.return_value = {
            "success": False, "message": "Invalid field selection",
        }
        action = self.adapter.execute(
            "mcp__fixture__get_records", self.valid,
        )
        detail = json.loads(action.detail)
        self.assertFalse(action.success)
        self.assertTrue(detail["dispatched"])
        self.assertFalse(detail["outcome_unknown"])
        self.assertTrue(detail["safe_read_failure"])
        self.assertFalse(detail["verified"])
        self.assertFalse(failure_requires_review([action]))
        self.assertEqual(self.registry.activity().summary("fixture")["last_outcome"], "failed")

    def test_explicit_mutation_error_remains_uncertain(self):
        self.transport.call_tool.return_value = {
            "success": False, "message": "May have partly processed",
        }
        action = self.adapter.execute(
            "mcp__fixture__send_records", self.valid, approved=True,
        )
        detail = json.loads(action.detail)
        self.assertTrue(detail["outcome_unknown"])
        self.assertTrue(failure_requires_review([action]))
        self.assertEqual(self.registry.activity().summary("fixture")["last_outcome"], "unknown")
        again = self.adapter.execute(
            "mcp__fixture__send_records", self.valid, approved=True,
        )
        self.assertEqual(json.loads(again.detail)["guard"],
                         "mcp_unknown_requires_review")
        self.assertEqual(self.transport.call_tool.call_count, 1)

    def test_ambiguous_remote_result_stays_uncertain_even_for_reads(self):
        self.transport.call_tool.return_value = {
            "success": False, "outcome_unknown": True,
        }
        action = self.adapter.execute(
            "mcp__fixture__get_records", self.valid,
        )
        self.assertTrue(json.loads(action.detail)["outcome_unknown"])
        self.assertTrue(failure_requires_review([action]))

    def test_outcome_contract_requires_local_read_authorization(self):
        self.assertEqual(
            transport_outcome({"success": False}, confirmed_read_only=True),
            (False, False),
        )
        self.assertEqual(transport_outcome({"success": False}), (False, True))
        self.assertEqual(
            transport_outcome({"success": False, "outcome_unknown": True},
                              confirmed_read_only=True),
            (False, True),
        )


if __name__ == "__main__":
    unittest.main()
