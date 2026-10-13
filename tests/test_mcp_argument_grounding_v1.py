"""Generic MCP argument-grounding tests; no live credentials or network."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from jarvis_agent.mcp_argument_grounding import unresolved_argument_paths, missing_required_arguments
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.mission_outcome_policy import failure_requires_review


class StubNative:
    def ollama_tools(self):
        return []
    def requires_confirmation(self, name):
        return False


def tool(name):
    return {"name": name, "description": "Fixture",
            "input_schema": {"type": "object",
                             "properties": {
                                 "tenant": {"type": "string"},
                                 "resource": {"type": "string"}},
                             "required": ["tenant", "resource"]}}


class ArgumentPatternTests(unittest.TestCase):
    def test_nested_placeholders_produce_paths_only(self):
        value = {
            "tenant": "<user_placeholder>",
            "payload": {"items": [
                {"resource": "${resource_id}"},
                {"repo": "https://svc.example/{repo}/commits"}]}}
        self.assertEqual(set(unresolved_argument_paths(value)), {
            "tenant", "payload.items[0].resource", "payload.items[1].repo"})
        self.assertEqual(unresolved_argument_paths({
            "site": "https://svc.test/%3Cowner%3E/list"}), ("site",))

    def test_real_names_text_and_html_are_not_placeholders(self):
        for value in ("hammouda93", "jarvis-personal",
                      "https://github.com/hammouda93/jarvis-personal",
                      "example-project", "Your account settings",
                      "<strong>Important</strong> — read only",
                      {"query": "Who owns example.com?"}):
            with self.subTest(value=value):
                self.assertEqual(unresolved_argument_paths({"field": value}), ())

    def test_missing_required_fields_no_defaults(self):
        schema = tool("list_records")["input_schema"]
        self.assertEqual(missing_required_arguments({"tenant": "org"}, schema),
                         ("resource",))
        self.assertEqual(missing_required_arguments(
            {"tenant": "", "resource": None}, schema), ("tenant", "resource"))
        self.assertEqual(missing_required_arguments(
            {"tenant": "org", "resource": "item"}, schema), ())


class MCPArgumentDispatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.registry = MCPRegistry(Path(tmp.name) / "servers.json")
        self.registry.add_http("fixture", "https://fixture.example/mcp")
        self.registry.set_enabled("fixture", True)
        self.registry.discover("fixture", [tool("list_records"), tool("create_record")])
        self.registry.note_successful_discovery("fixture")
        self.registry.set_runtime_enabled(True)
        self.registry.set_auto_readonly("fixture", True)
        self.registry.allow_tool("fixture", "create_record", True)
        self.transport = Mock()
        self.transport.call_tool.return_value = {
            "success": True, "message": "External unverified response"}
        self.adapter = MCPToolRegistry(StubNative(), registry=self.registry,
                                       transport=self.transport,
                                       runtime_gate=self.registry.runtime_enabled)

    def test_ungrounded_read_is_nondispatched_recoverable(self):
        action = self.adapter.execute("mcp__fixture__list_records", {
            "tenant": "<user_placeholder>", "resource": "valid"})
        self.assertFalse(action.success)
        detail = json.loads(action.detail)
        self.assertEqual(detail["guard"], "unresolved_argument_template")
        self.assertEqual(detail["argument_paths"], ["tenant"])
        self.assertFalse(detail["dispatched"])
        self.assertFalse(detail["outcome_unknown"])
        self.assertTrue(detail["recoverable"])
        self.assertFalse(failure_requires_review([action]))
        self.assertNotIn("<user_placeholder>", action.detail)
        self.assertEqual(self.registry.activity().summary("fixture")["calls_used"], 0)
        self.transport.call_tool.assert_not_called()

    def test_approved_mutation_also_rejects_placeholder(self):
        action = self.adapter.execute("mcp__fixture__create_record", {
            "tenant": "org", "resource": "${resource_id}"}, approved=True)
        self.assertFalse(json.loads(action.detail)["dispatched"])
        self.assertEqual(self.registry.activity().summary("fixture")["calls_used"], 0)
        self.transport.call_tool.assert_not_called()

    def test_missing_required_field_is_known_local_failure(self):
        action = self.adapter.execute("mcp__fixture__list_records", {
            "tenant": "valid"})
        self.assertEqual(json.loads(action.detail)["guard"], "missing_required_arguments")
        self.assertFalse(failure_requires_review([action]))
        self.transport.call_tool.assert_not_called()

    def test_real_ids_dispatch_through_existing_permission(self):
        action = self.adapter.execute("mcp__fixture__list_records", {
            "tenant": "hammouda93", "resource": "jarvis-personal"})
        self.assertTrue(action.success)
        self.assertTrue(json.loads(action.detail)["dispatched"])
        self.assertEqual(self.transport.call_tool.call_count, 1)
        self.assertEqual(self.registry.activity().summary("fixture")["calls_used"], 1)

    def test_unknown_external_outcome_stays_locked(self):
        self.transport.call_tool.return_value = {"success": False, "message": "error"}
        first = self.adapter.execute("mcp__fixture__list_records", {
            "tenant": "org", "resource": "item"})
        self.assertTrue(failure_requires_review([first]))
        invalid = self.adapter.execute("mcp__fixture__list_records", {
            "tenant": "<user_placeholder>", "resource": "item"})
        self.assertFalse(json.loads(invalid.detail)["dispatched"])
        second = self.adapter.execute("mcp__fixture__list_records", {
            "tenant": "org", "resource": "item"})
        self.assertFalse(second.success)
        self.assertEqual(json.loads(second.detail)["guard"],
                         "mcp_unknown_requires_review")
        self.assertEqual(self.transport.call_tool.call_count, 1)


if __name__ == "__main__":
    unittest.main()
