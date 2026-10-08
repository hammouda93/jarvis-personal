"""Stage 9A: MCP registry, consent gates, SDK adapter and operator UI.

All tests are offline: mock transport. No credentials, network, Hermes install,
real Windows interactions or background asynchronous tasks.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from jarvis_agent.mcp_server_registry import MCPRegistry, _valid_remote
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
from jarvis_agent.mcp_sdk_transport import OfficialMCPTransport
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent.operator_telemetry import snapshot as operator_snapshot


class FakeTransport:
    def __init__(self):
        self.discovery_count = 0
        self.call_count = 0
        self.fail = False
        self.last_tool = None

    def discover(self, entry):
        self.discovery_count += 1
        return [
            {"name": "read-items", "description": "Read items",
             "input_schema": {"type": "object", "properties": {
                 "query": {"type": "string"},
             }, "required": ["query"]}},
            {"name": "send_message", "description": "Send a real message",
             "input_schema": {"type": "object", "properties": {
                 "text": {"type": "string"},
             }}},
        ]

    def call_tool(self, entry, tool, arguments):
        self.call_count += 1
        self.last_tool = (entry["id"], tool, arguments)
        if self.fail:
            raise TimeoutError("effect may have happened")
        return {"success": True, "message": "Tool finished", "data": {"count": 1}}


class FakeDelegate:
    def __init__(self):
        self.calls = []

    def ollama_tools(self):
        return [{"type": "function", "function": {
            "name": "open_application", "description": "existing native tool",
            "parameters": {"type": "object", "properties": {}},
        }}]

    def requires_confirmation(self, name):
        return False

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, arguments, approved))
        return AgentActionResult(name, True, "native success", detail="{}")


class MCPHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config" / "servers.json"
        self.registry = MCPRegistry(self.path)
        self.transport = FakeTransport()
        self.delegate = FakeDelegate()
        self.adapter = MCPToolRegistry(
            self.delegate, registry=self.registry, transport=self.transport
        )

    def configured(self):
        self.registry.add_http("github", "https://mcp.example.org/mcp")
        self.registry.set_enabled("github", True)

    def test_default_is_empty_and_does_not_create_config(self):
        self.assertEqual(self.registry.list_servers(), [])
        self.assertEqual(len(self.adapter.ollama_tools()), 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.transport.discovery_count, 0)

    def test_remote_mcp_config_is_disabled_until_user_switches_on(self):
        self.registry.add_http("research", "https://mcp.example.org/mcp")
        saved = self.registry.list_servers()
        self.assertEqual(len(saved), 1)
        self.assertFalse(saved[0]["enabled"])
        self.assertEqual(saved[0]["allowed"], 0)
        self.assertEqual(len(self.adapter.openai_tools()), 1)
        self.assertEqual(self.transport.call_count, 0)
        self.assertTrue(self.path.is_file())

    def test_remote_url_security_and_name_validation(self):
        allowed = ["https://example.org/mcp", "http://localhost:8765/mcp",
                   "http://127.0.0.1:9000/mcp"]
        for url in allowed:
            self.assertTrue(_valid_remote(url), url)
        rejected = ["http://example.org/mcp", "file:///etc/passwd",
                    "https://user:token@example.org/mcp",
                    "https://example.org/mcp?access_token=secret",
                    "https://example.org/mcp#token", "javascript:alert(1)",
                    "https://", "http://192.168.1.1/mcp"]
        for url in rejected:
            self.assertFalse(_valid_remote(url), url)
        with self.assertRaises(ValueError):
            self.registry.add_http("../unsafe", allowed[0])

    def test_hermes_profile_is_config_only_and_does_not_install_or_launch(self):
        self.registry.add_hermes_local()
        row = self.registry.list_servers()[0]
        self.assertEqual(row["id"], "hermes")
        self.assertEqual(row["kind"], "stdio")
        self.assertFalse(row["enabled"])
        self.assertEqual(self.registry.get_server("hermes")["args"], ["mcp", "serve"])
        self.assertEqual(self.transport.discovery_count, 0)
        self.assertEqual(self.transport.call_count, 0)

    def test_deny_discovery_until_server_explicitly_enabled(self):
        self.registry.add_http("github", "https://mcp.example.org/mcp")
        with self.assertRaises(RuntimeError):
            perform_mcp_command(
                self.registry, MCPCommand("discover", "github"),
                transport=self.transport
            )
        self.assertEqual(self.transport.discovery_count, 0)

    def test_discovery_health_never_claims_live_connection(self):
        self.registry.add_http("research", "https://mcp.example.org/mcp")
        before = self.registry.list_servers()[0]
        self.assertFalse(before["connected_now"])
        self.assertEqual(before["last_discovery_success_utc"], "")
        with self.assertRaises(RuntimeError):
            self.registry.note_successful_discovery("research")
        self.registry.set_enabled("research", True)
        perform_mcp_command(
            self.registry, MCPCommand("discover", "research"),
            transport=self.transport,
        )
        after = self.registry.list_servers()[0]
        self.assertTrue(after["last_discovery_success_utc"])
        self.assertFalse(after["connected_now"])
        self.assertEqual(after["discovered"], 2)
        self.assertEqual(after["allowed"], 0)

    def test_tool_discovery_is_not_tool_authorization(self):
        self.configured()
        out = perform_mcp_command(
            self.registry, MCPCommand("discover", "github"),
            transport=self.transport
        )
        self.assertEqual(out["tool_count"], 2)
        self.assertEqual(len(self.adapter.ollama_tools()), 1)
        self.assertEqual(self.registry.list_servers()[0]["discovered"], 2)
        self.assertEqual(self.registry.list_servers()[0]["allowed"], 0)
        self.assertEqual(self.transport.call_count, 0)

    def test_explicit_per_tool_consent_exposes_only_selected_tool(self):
        self.configured()
        self.registry.discover("github", self.transport.discover({}))
        self.registry.allow_tool("github", "read-items", True)
        tools = self.adapter.ollama_tools()
        self.assertEqual(
            {t["function"]["name"] for t in tools},
            {"open_application", "mcp__github__read_items"},
        )
        self.assertTrue(self.adapter.requires_confirmation("mcp__github__read_items"))
        self.assertFalse(self.adapter.requires_confirmation("open_application"))
        self.assertEqual(tools[-1]["function"]["parameters"]["required"], ["query"])
        self.assertEqual(self.transport.call_count, 0)

    def test_tool_call_requires_fresh_user_confirmation_every_time(self):
        self.configured()
        self.registry.discover("github", self.transport.discover({}))
        self.registry.allow_tool("github", "read-items", True)
        name = "mcp__github__read_items"
        rejected = self.adapter.execute(name, {"query": "what is new"})
        self.assertFalse(rejected.success)
        self.assertEqual(self.transport.call_count, 0)
        yes = self.adapter.execute(name, {"query": "what is new"}, approved=True)
        self.assertTrue(yes.success)
        self.assertEqual(self.transport.call_count, 1)
        self.assertFalse(json.loads(yes.detail)["verified"])
        self.assertEqual(self.transport.last_tool,
                         ("github", "read-items", {"query": "what is new"}))
        # An earlier approved invocation doesn't create blanket permission.
        again = self.adapter.execute(name, {"query": "new"})
        self.assertFalse(again.success)
        self.assertEqual(self.transport.call_count, 1)

    def test_remote_unknown_error_never_promoted_to_success(self):
        self.configured()
        self.registry.discover("github", self.transport.discover({}))
        self.registry.allow_tool("github", "send_message", True)
        self.transport.fail = True
        action = self.adapter.execute("mcp__github__send_message",
                                      {"text": "hello"}, approved=True)
        self.assertFalse(action.success)
        self.assertTrue(json.loads(action.detail)["outcome_unknown"])
        self.assertFalse(json.loads(action.detail)["verified"])
        self.assertEqual(self.transport.call_count, 1)

    def test_policy_changes_live_and_old_alias_is_blocked(self):
        self.configured()
        self.registry.discover("github", self.transport.discover({}))
        self.registry.allow_tool("github", "read-items", True)
        name = "mcp__github__read_items"
        self.assertIn(name, [t["function"]["name"] for t in self.adapter.ollama_tools()])
        self.registry.set_enabled("github", False)
        self.assertNotIn(name, [t["function"]["name"] for t in self.adapter.ollama_tools()])
        blocked = self.adapter.execute(name, {}, approved=True)
        self.assertFalse(blocked.success)
        self.assertEqual(self.transport.call_count, 0)

    def test_discovery_persists_existing_policy_not_new_tool_consent(self):
        self.configured()
        self.registry.discover("github", self.transport.discover({}))
        self.registry.allow_tool("github", "read-items", True)
        self.registry.discover("github", self.transport.discover({}))
        self.assertTrue(self.registry.is_allowed("github", "read-items"))
        self.assertFalse(self.registry.is_allowed("github", "send_message"))

    def test_changed_tool_definition_revokes_previous_user_consent(self):
        self.configured()
        tools = self.transport.discover({})
        self.registry.discover("github", tools)
        self.registry.allow_tool("github", "read-items", True)
        self.assertTrue(self.registry.is_allowed("github", "read-items"))
        # Same name, but now another schema. Prior consent is NOT reusable.
        changed = [
            {
                **item,
                "description": "Potentially writes external data",
                "input_schema": {"type": "object", "properties": {
                    "delete_everything": {"type": "boolean"}
                }},
            } if item["name"] == "read-items" else item
            for item in tools
        ]
        self.registry.discover("github", changed)
        self.assertFalse(self.registry.is_allowed("github", "read-items"))
        self.assertNotIn(
            "mcp__github__read_items",
            [x["function"]["name"] for x in self.adapter.ollama_tools()],
        )
        self.assertFalse(self.adapter.execute(
            "mcp__github__read_items", {}, approved=True
        ).success)
        self.assertEqual(self.transport.call_count, 0)

    def test_changed_tool_description_also_revokes_old_consent(self):
        self.configured()
        tools = self.transport.discover({})
        self.registry.discover("github", tools)
        self.registry.allow_tool("github", "read-items", True)
        mutated = [
            {**item, "description": "Changed semantics"}
            if item["name"] == "read-items" else item
            for item in tools
        ]
        self.registry.discover("github", mutated)
        self.assertFalse(self.registry.is_allowed("github", "read-items"))

    def test_tool_alias_collision_disables_both_ambiguous_candidates(self):
        self.configured()
        tools = [{"name": "a-b", "input_schema": {"type": "object"}},
                 {"name": "a.b", "input_schema": {"type": "object"}}]
        self.registry.discover("github", tools)
        self.registry.allow_tool("github", "a-b", True)
        self.registry.allow_tool("github", "a.b", True)
        self.assertEqual(len(self.adapter.ollama_tools()), 1)
        self.assertFalse(self.adapter.execute("mcp__github__a_b", {},
                                              approved=True).success)

    def test_native_delegate_still_executes_unmodified(self):
        result = self.adapter.execute("open_application", {"name": "notepad"},
                                      approved=False)
        self.assertTrue(result.success)
        self.assertEqual(self.delegate.calls[0][0], "open_application")

    def test_mcp_independent_server_isolation(self):
        self.configured()
        self.registry.discover("github", self.transport.discover({}))
        self.registry.allow_tool("github", "read-items", True)
        self.registry.add_http("calendar", "https://calendar.example.org/mcp")
        self.registry.set_enabled("calendar", True)
        self.registry.discover("calendar", self.transport.discover({}))
        self.assertEqual(len(self.adapter.ollama_tools()), 2)
        self.assertFalse(self.registry.is_allowed("calendar", "read-items"))
        self.assertTrue(self.registry.is_allowed("github", "read-items"))

    def test_control_inbox_validation_and_fake_connection_only(self):
        inbox = MCPControlInbox()
        self.assertFalse(inbox.submit("remove_all", "github"))
        self.assertFalse(inbox.submit("add_http", "../unsafe", "https://example.org"))
        self.assertTrue(inbox.submit("add_http", "github", "https://example.org/mcp"))
        self.assertTrue(inbox.submit("enable", "github"))
        self.assertEqual(inbox.pop_nowait().operation, "add_http")
        self.assertEqual(inbox.pop_nowait().operation, "enable")
        self.assertIsNone(inbox.pop_nowait())

    def test_operator_telemetry_exposes_no_credentials(self):
        self.configured()
        self.registry.discover("github", self.transport.discover({}))
        self.registry.allow_tool("github", "read-items", True)
        with patch.dict(os.environ, {
            "JARVIS_MCP_CONFIG_PATH": str(self.path),
            "JARVIS_MCP_ENABLED": "1",
            "JARVIS_MCP_BEARER_GITHUB": "NEVER_EXPOSE_SECRET",
        }):
            snap = operator_snapshot()
        self.assertTrue(snap["mcp"]["enabled"])
        self.assertEqual(len(snap["mcp"]["servers"]), 1)
        self.assertNotIn("NEVER_EXPOSE_SECRET", repr(snap))


class MCPOperatorWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_widget_issues_only_explicit_requests(self):
        panel = MCPConnectionsPanel()
        try:
            messages = []
            panel.requested.connect(lambda op, sid, val: messages.append((op, sid, val)))
            panel.server_name.setText("github")
            panel.server_url.setText("https://example.org/mcp")
            panel.add_button.click()
            self.assertEqual(messages[-1],
                             ("add_http", "github", "https://example.org/mcp"))
            panel.apply_snapshot({
                "enabled": True,
                "servers": [{"id": "github", "kind": "http", "enabled": False,
                             "discovered": 1, "allowed": 0,
                             "tools": [{"name": "read-items", "allowed": False}]}],
            })
            panel.server_picker.setCurrentIndex(1)
            panel.enable_button.click()
            self.assertEqual(messages[-1], ("enable", "github", ""))
            panel.tool_picker.setCurrentIndex(1)
            panel.allow_button.click()
            self.assertEqual(messages[-1], ("allow_tool", "github", "read-items"))
            panel.hermes_button.click()
            self.assertEqual(messages[-1], ("add_hermes", "hermes", ""))
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
