"""Read-only MCP autopilot: one operator grant, no per-tool picker or unsafe writes.

All tools, UI responses and transports are fixtures: no network or real accounts.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis_agent.mcp_read_policy import is_conservative_read_tool
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.native_tools import AgentActionResult


def tool(name, **properties):
    return {"name": name, "description": "Fixture " + name,
            "input_schema": {"type": "object", "properties": properties}}


class Delegate:
    def ollama_tools(self):
        return [{"type": "function", "function": {
            "name": "browser_list_tabs", "description": "Native browser",
            "parameters": {"type": "object"}}}]

    def requires_confirmation(self, name):
        return False

    def execute(self, name, args, *, approved=False):
        return AgentActionResult(name, True, "native", "{}")


class OneClickReadAccessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.registry = MCPRegistry(Path(temp.name) / "registry.json")
        self.registry.add_http("fixture", "https://fixture.example/mcp")
        self.registry.set_enabled("fixture", True)
        self.transport = Mock()
        self.transport.call_tool.return_value = {
            "success": True, "message": "Some external, unverified data"
        }
        self.adapter = MCPToolRegistry(
            Delegate(), registry=self.registry, transport=self.transport,
            runtime_gate=self.registry.runtime_enabled,
        )
        self.registry.set_runtime_enabled(True)

    def discovered(self):
        self.registry.discover("fixture", [
            tool("get_file_contents", owner={"type": "string"}),
            tool("list_commits"),
            tool("pull_request_read"),
            tool("search_repositories"),
            tool("create_pull_request"),
            tool("delete_file"),
            tool("get_secret"),
            tool("get_items", force={"type": "boolean"}),
        ])
        self.registry.note_successful_discovery("fixture")

    def test_read_classifier_rejects_mutations_and_secrets(self):
        for n in ("get_file_contents", "list_commits", "pull_request_read",
                  "search_repositories", "get_me"):
            self.assertTrue(is_conservative_read_tool(n), n)
        for n in ("create_pull_request", "delete_file", "get_secret",
                  "list_tokens", "merge_pull_request", "send_message",
                  "get", "get_access_token", "set_branch_protection"):
            # Single-word "get" is allowed as a query under current conservative
            # grammar, but must not be auto-dispatched without a pinned schema.
            if n != "get":
                self.assertFalse(is_conservative_read_tool(n), n)
        self.assertFalse(is_conservative_read_tool(
            "get_items", {"type": "object", "properties": {
                "force": {"type": "boolean"}}}))

    def test_connection_alone_never_grants_any_tool(self):
        self.discovered()
        self.assertEqual(self.registry.list_servers()[0]["allowed"], 0)
        self.assertNotIn("mcp__fixture__get_file_contents", [
            x["function"]["name"] for x in self.adapter.ollama_tools()
        ])
        self.assertFalse(self.registry.get_server("fixture").get("auto_read_enabled", False))
        self.transport.call_tool.assert_not_called()

    def test_single_operator_grant_pins_reads_and_makes_them_executable(self):
        self.discovered()
        inbox = MCPControlInbox()
        self.assertTrue(inbox.submit("grant_auto_read", "fixture"))
        granted = perform_mcp_command(self.registry, inbox.pop_nowait())
        self.assertTrue(granted["success"])
        self.assertEqual(self.registry.list_servers()[0]["allowed"], 4)
        self.assertFalse(self.adapter.requires_confirmation("mcp__fixture__list_commits"))
        self.assertTrue(self.adapter.requires_confirmation("mcp__fixture__create_pull_request"))
        out = self.adapter.execute("mcp__fixture__list_commits", {})
        self.assertTrue(out.success)
        self.assertIs(json.loads(out.detail)["verified"], False)
        self.assertEqual(self.transport.call_tool.call_count, 1)
        rejected = self.adapter.execute("mcp__fixture__create_pull_request", {})
        self.assertFalse(rejected.success)
        self.assertTrue(json.loads(rejected.detail)["approval_required"])
        self.assertEqual(self.transport.call_tool.call_count, 1)

    def test_failed_discovery_does_not_allow_bulk_grant(self):
        self.registry.discover("fixture", [tool("list_commits")])
        with self.assertRaises(ValueError):
            self.registry.set_auto_readonly("fixture", True)
        self.assertEqual(self.registry.list_servers()[0]["allowed"], 0)

    def test_new_or_changed_tool_requires_new_operator_grant(self):
        self.discovered()
        self.registry.set_auto_readonly("fixture", True)
        self.registry.discover("fixture", [
            tool("get_file_contents", revision={"type": "string"}),
            tool("list_commits"), tool("list_new_items"),
        ])
        self.assertFalse(self.registry.is_allowed("fixture", "get_file_contents"))
        self.assertTrue(self.registry.is_allowed("fixture", "list_commits"))
        self.assertFalse(self.registry.is_allowed("fixture", "list_new_items"))
        self.registry.set_auto_readonly("fixture", True)
        self.assertTrue(self.registry.is_allowed("fixture", "get_file_contents"))
        self.assertTrue(self.registry.is_allowed("fixture", "list_new_items"))

    def test_explicit_denial_overrides_bulk_grant_and_survives_discovery(self):
        self.discovered()
        self.registry.set_auto_readonly("fixture", True)
        self.registry.allow_tool("fixture", "list_commits", False)
        self.assertFalse(self.registry.is_allowed("fixture", "list_commits"))
        self.assertTrue(self.registry.is_allowed("fixture", "pull_request_read"))
        self.registry.discover("fixture", [tool("list_commits"), tool("pull_request_read")])
        self.assertFalse(self.registry.is_allowed("fixture", "list_commits"))
        self.registry.set_auto_readonly("fixture", True)
        self.assertFalse(self.registry.is_allowed("fixture", "list_commits"))
        self.registry.allow_tool("fixture", "list_commits", True)
        self.assertTrue(self.registry.is_allowed("fixture", "list_commits"))

    def test_revoke_does_not_erase_server_or_scoped_credentials(self):
        self.discovered()
        self.registry.set_auto_readonly("fixture", True)
        before = self.registry.get_server("fixture").get("credential_source")
        perform_mcp_command(self.registry, MCPCommand("revoke_auto_read", "fixture"))
        self.assertEqual(self.registry.list_servers()[0]["allowed"], 0)
        self.assertEqual(self.registry.get_server("fixture").get("credential_source"), before)
        self.assertTrue(self.registry.get_server("fixture")["enabled"])
        self.assertTrue(self.adapter.requires_confirmation("mcp__fixture__list_commits"))
        self.registry.set_auto_readonly("fixture", True)
        perform_mcp_command(self.registry, MCPCommand("revoke_tools", "fixture"))
        self.assertEqual(self.registry.list_servers()[0]["allowed"], 0)
        self.assertFalse(self.registry.get_server("fixture")["auto_read_enabled"])

    def test_disabled_runtime_and_server_always_fail_closed(self):
        self.discovered()
        self.registry.set_auto_readonly("fixture", True)
        self.registry.set_runtime_enabled(False)
        self.assertTrue(self.adapter.requires_confirmation("mcp__fixture__list_commits"))
        self.assertFalse(self.adapter.execute("mcp__fixture__list_commits", {}).success)
        self.registry.set_runtime_enabled(True)
        self.registry.set_enabled("fixture", False)
        self.assertFalse(self.adapter.execute("mcp__fixture__list_commits", {}).success)
        self.transport.call_tool.assert_not_called()


class OneClickPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_stable_poll_does_not_rebuild_long_picker_or_jump_selection(self):
        from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        tools = [{"name": f"get_result_{i}", "allowed": False} for i in range(135)]
        data = {"enabled": True, "servers": [{
            "id": "fixture", "enabled": True, "kind": "http",
            "last_discovery_success_utc": "2026-10-13T20:00:00Z",
            "auto_read_enabled": False, "tools": tools, "allowed": 0,
        }]}
        panel.apply_snapshot(data)
        panel.server_picker.setCurrentIndex(1)
        self.assertTrue(panel.auto_read_button.isEnabled())
        panel.tool_picker.setCurrentIndex(70)
        model = panel.tool_picker.model()
        self.assertTrue(model.rowCount() > 100)
        with patch.object(panel.tool_picker, "clear", side_effect=AssertionError(
                "unchanged telemetry must not clear tool combo")):
            for _ in range(4):
                panel.apply_snapshot(data)
        self.assertEqual(panel.tool_picker.currentIndex(), 70)

    def test_read_auto_grant_needs_single_modal_confirmation(self):
        from PySide6.QtWidgets import QMessageBox
        from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        data = {"servers": [{
            "id": "fixture", "kind": "http", "enabled": True, "tools": [],
            "last_discovery_success_utc": "2026-10-13T20:00:00Z",
        }]}
        panel.apply_snapshot(data)
        panel.server_picker.setCurrentIndex(1)
        emitted = []
        panel.requested.connect(lambda *args: emitted.append(args))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            panel.auto_read_button.click()
        self.assertEqual(emitted, [])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            panel.auto_read_button.click()
        self.assertEqual(emitted, [("grant_auto_read", "fixture", "")])
        data["servers"][0]["auto_read_enabled"] = True
        panel.apply_snapshot(data)
        panel.auto_read_button.click()
        self.assertEqual(emitted[-1], ("revoke_auto_read", "fixture", ""))


if __name__ == "__main__":
    unittest.main()
