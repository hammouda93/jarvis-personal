"""MCP metadata, runtime gates, and explicit controls; no personal accounts."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from mcp.types import (ListPromptsResult, ListResourcesResult, ListResourceTemplatesResult,
    ListToolsResult, PaginatedRequestParams, Prompt, PromptsCapability, Resource,
    ResourcesCapability, ResourceTemplate, ServerCapabilities, Tool, ToolsCapability)
from PySide6.QtWidgets import QApplication

from jarvis_agent.mcp_control import (MCPCommand, MCPControlInbox, connection_failure_state,
    perform_mcp_command)
from jarvis_agent.mcp_credentials import CredentialVault
from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
from jarvis_agent.mcp_sdk_transport import MCPUnavailable, OfficialMCPTransport, session_inventory
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.native_tools import AgentActionResult


class InventorySession:
    def __init__(self):
        self.calls = []

    async def list_tools(self, *, params=None):
        self.calls.append(("tools", params))
        return ListToolsResult(tools=[Tool(name="second" if params else "first", inputSchema={"type": "object"})],
                               nextCursor=None if params else "page2")

    async def list_resources(self, *, params=None):
        self.calls.append(("resources", params))
        return ListResourcesResult(resources=[Resource(name="record", uri="fixture://record")])

    async def list_resource_templates(self, *, params=None):
        self.calls.append(("resource_templates", params))
        return ListResourceTemplatesResult(resourceTemplates=[ResourceTemplate(name="records", uriTemplate="fixture://records/{id}")])

    async def list_prompts(self, *, params=None):
        self.calls.append(("prompts", params))
        return ListPromptsResult(prompts=[Prompt(name="draft", arguments=[{"name": "topic", "required": True}])])


class InventoryTransport:
    def __init__(self):
        self.connections = 0
        self.executions = 0

    def inventory(self, entry):
        self.connections += 1
        return {"tools": [{"name": "read", "input_schema": {"type": "object"}}],
            "capabilities": {"tools": True, "resources": True, "prompts": True},
            "resources": [{"name": "record", "uri": "fixture://record"}],
            "prompts": [{"name": "draft", "arguments": [{"name": "topic", "required": True}]}],
            "resource_templates": [{"name": "records", "uri_template": "fixture://records/{id}"}]}

    def call_tool(self, *args):
        self.executions += 1
        return {"success": True}


class NativeDelegate:
    def ollama_tools(self):
        return [{"function": {"name": "native", "description": "Native", "parameters": {"type": "object"}}}]

    def execute(self, name, arguments, *, approved=False):
        return AgentActionResult(name, True, "native forwarded")


class InventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_sdk_pagination_and_all_capability_types(self):
        session = InventorySession()
        initialized = SimpleNamespace(capabilities=ServerCapabilities(tools=ToolsCapability(),
            resources=ResourcesCapability(), prompts=PromptsCapability()))
        result = await session_inventory(session, initialized)
        self.assertEqual([t["name"] for t in result["tools"]], ["first", "second"])
        self.assertIsInstance(session.calls[1][1], PaginatedRequestParams)
        self.assertEqual(session.calls[1][1].cursor, "page2")
        self.assertEqual(result["resource_templates"][0]["uri_template"], "fixture://records/{id}")
        self.assertEqual(result["prompts"][0]["arguments"], [{"name": "topic", "required": True}])
        self.assertEqual(result["truncated"], [])

    async def test_resource_only_server_does_not_receive_tools_list(self):
        session = InventorySession()
        result = await session_inventory(session, SimpleNamespace(capabilities=ServerCapabilities(resources=ResourcesCapability())))
        self.assertEqual([x[0] for x in session.calls], ["resources", "resource_templates"])
        self.assertEqual(result["tools"], [])
        self.assertFalse(result["capabilities"]["tools"])

    async def test_repeated_cursor_and_oversize_inventory_are_bounded(self):
        class Repeating:
            count = 0
            async def list_tools(self, *, params=None):
                self.count += 1
                return ListToolsResult(tools=[Tool(name="read", inputSchema={})], nextCursor="same")
        session = Repeating()
        out = await session_inventory(session, None)
        self.assertEqual(session.count, 2)
        self.assertIn("tools", out["truncated"])
        class Large:
            async def list_tools(self, *, params=None):
                return ListToolsResult(tools=[Tool(name=f"read{i}", inputSchema={}) for i in range(170)])
        out = await session_inventory(Large(), None)
        self.assertEqual(len(out["tools"]), 150)
        self.assertIn("tools", out["truncated"])

    async def test_tools_only_oauth_discovery_does_not_fetch_other_capabilities(self):
        session = InventorySession()
        initialized = SimpleNamespace(capabilities=ServerCapabilities(tools=ToolsCapability(), resources=ResourcesCapability()))
        await session_inventory(session, initialized, tools_only=True)
        self.assertEqual([x[0] for x in session.calls], ["tools", "tools"])


class ControlCenterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.registry = MCPRegistry(Path(tmp.name) / "servers.json")
        self.registry.add_http("fixture", "https://fixture.example/mcp")
        self.transport = InventoryTransport()
        backend = SimpleNamespace(items={})
        backend.put = lambda k, v: backend.items.__setitem__(k, v)
        backend.get = lambda k: backend.items.get(k)
        backend.delete = lambda k: backend.items.pop(k, None)
        self.vault = CredentialVault(Path(tmp.name) / "vault", backend=backend)

    def command(self, op, value=""):
        return perform_mcp_command(self.registry, MCPCommand(op, "fixture", value), transport=self.transport, vault=self.vault)

    def test_connect_discovers_persisted_metadata_without_calls_or_tool_consent(self):
        result = self.command("connect")
        self.assertEqual(result["tool_count"], 1)
        row = MCPRegistry(self.registry.path).list_servers()[0]
        self.assertEqual(row["connection_state"], "tested_session_closed")
        self.assertFalse(row["connected_now"])
        self.assertEqual(row["allowed"], 0)
        self.assertEqual(len(row["resources"]), 1)
        self.assertEqual(len(row["prompts"]), 1)
        self.assertTrue(row["last_discovery_success_utc"])
        self.assertEqual(self.transport.executions, 0)

    def test_reconnect_is_one_attempt_and_disconnect_revokes_without_erasing_credentials(self):
        self.command("store_bearer", "FIXTURE_SECRET")
        self.command("connect")
        self.registry.allow_tool("fixture", "read", True)
        self.command("reconnect")
        self.assertEqual(self.transport.connections, 2)
        self.assertTrue(self.registry.is_allowed("fixture", "read"))
        self.command("disconnect")
        self.assertFalse(self.registry.get_server("fixture")["enabled"])
        self.assertFalse(self.registry.is_allowed("fixture", "read"))
        self.assertEqual(self.vault.read("fixture", "https://fixture.example/mcp"), "FIXTURE_SECRET")

    def test_failed_connection_deactivates_stale_tools_and_stores_only_safe_state(self):
        self.command("connect")
        self.registry.allow_tool("fixture", "read", True)
        import httpx2
        failure = httpx2.HTTPStatusError("PRIVATE_ERROR", request=httpx2.Request("GET", "https://fixture.example"), response=httpx2.Response(401))
        with patch.object(self.transport, "inventory", side_effect=ExceptionGroup("PRIVATE", [failure])):
            with self.assertRaises(ExceptionGroup):
                self.command("reconnect")
        row = self.registry.list_servers()[0]
        self.assertEqual(row["connection_state"], "authorization_required")
        self.assertFalse(row["enabled"])
        self.assertEqual(self.registry.exposed_tools(), [])
        self.assertNotIn("PRIVATE", self.registry.path.read_text())

    def test_failed_quota_preflight_does_not_enable_stale_tools(self):
        self.command("connect")
        self.registry.set_quotas("fixture", {"sessions_per_hour": 1, "calls_per_hour": 1})
        with self.assertRaises(RuntimeError):
            self.command("reconnect")
        self.assertFalse(self.registry.get_server("fixture")["enabled"])
        self.assertEqual(self.transport.connections, 1)

    def test_runtime_switch_is_durable_live_and_native_tools_stay_available(self):
        self.registry.set_runtime_enabled(False)
        self.command("connect")
        self.registry.allow_tool("fixture", "read", True)
        adapter = MCPToolRegistry(NativeDelegate(), registry=self.registry, transport=self.transport,
            runtime_gate=self.registry.runtime_enabled)
        self.assertEqual(len(adapter.ollama_tools()), 1)
        self.assertFalse(adapter.execute("mcp__fixture__read", {}, approved=True).success)
        perform_mcp_command(self.registry, MCPCommand("runtime_set", "", "true"))
        self.assertTrue(MCPRegistry(self.registry.path).runtime_enabled())
        self.assertEqual(len(adapter.ollama_tools()), 2)
        self.assertTrue(adapter.execute("native", {}).success)
        self.assertEqual(self.transport.executions, 0)
        self.registry.set_runtime_enabled(False)
        self.assertFalse(adapter.execute("mcp__fixture__read", {}, approved=True).success)

    def test_bad_registry_is_fail_closed_for_mcp_but_not_native(self):
        adapter = MCPToolRegistry(NativeDelegate(), registry=self.registry, runtime_gate=self.registry.runtime_enabled)
        with patch.object(self.registry, "runtime_enabled", side_effect=ValueError("invalid")):
            self.assertEqual(len(adapter.ollama_tools()), 1)
            self.assertTrue(adapter.execute("native", {}).success)

    def test_metadata_does_not_persist_secret_uri_queries_fragments_or_userinfo(self):
        self.registry.note_inventory("fixture", {"resources": [
            {"name": "safe", "uri": "fixture://record"},
            {"name": "query", "uri": "https://fixture.example?token=PRIVATE"},
            {"name": "userinfo", "uri": "https://u:PRIVATE@fixture.example"},
            {"name": "fragment", "uri": "fixture://record#PRIVATE"}]})
        self.assertEqual(len(self.registry.list_servers()[0]["resources"]), 1)
        self.assertNotIn("PRIVATE", self.registry.path.read_text())

    def test_api_key_is_bound_to_endpoint_and_never_in_registry_or_result(self):
        self.command("connect")
        self.registry.allow_tool("fixture", "read", True)
        value = json.dumps({"header": "X-Goog-Api-Key", "key": "PRIVATE_KEY"})
        result = self.command("store_api_key", value)
        self.assertNotIn("PRIVATE_KEY", repr(result))
        self.assertNotIn("PRIVATE_KEY", self.registry.path.read_text())
        entry = self.registry.get_server("fixture")
        self.assertEqual(entry["credential_source"], "api_key")
        self.assertFalse(entry["enabled"])
        self.assertFalse(self.registry.is_allowed("fixture", "read"))
        self.assertEqual(self.vault.read("fixture", entry["url"], "api_key"), "PRIVATE_KEY")
        self.command("forget_credentials")
        self.assertIsNone(self.vault.read("fixture", entry["url"], "api_key"))

    def test_inbox_api_keys_and_runtime_flag_validation_hide_secrets(self):
        inbox = MCPControlInbox()
        self.assertFalse(inbox.submit("runtime_set", "fixture", "true"))
        self.assertFalse(inbox.submit("runtime_set", "", "1"))
        self.assertTrue(inbox.submit("runtime_set", "", "false"))
        for packet in ({"header": "Cookie", "key": "secret"}, {"header": "X-API-Key", "key": "s\nX: s"},
                       {"header": "X-API-Key", "key": "secret", "extra": True}):
            self.assertFalse(inbox.submit("store_api_key", "fixture", json.dumps(packet)))
        self.assertTrue(inbox.submit("store_api_key", "fixture", '{"header":"X-API-Key","key":"PRIVATE"}'))
        inbox.pop_nowait()
        self.assertNotIn("PRIVATE", repr(inbox.pop_nowait()))

    def test_error_classification_never_returns_remote_error_body(self):
        self.assertEqual(connection_failure_state(MCPUnavailable("PRIVATE")), "transport_error")
        self.assertEqual(connection_failure_state(MCPUnavailable("mcp_secure_credential_missing")), "authorization_required")

    def test_http_api_key_header_uses_vault_and_no_authorization_or_proxy_environment(self):
        captured = {}
        @asynccontextmanager
        async def stream(url, *, http_client):
            captured.update(headers=dict(http_client.headers), url=url)
            yield (None, None, None)
        class Session:
            def __init__(self, *args): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def initialize(self): return SimpleNamespace(capabilities=ServerCapabilities())
        entry = {"id": "fixture", "kind": "http", "url": "https://fixture.example/mcp",
                 "credential_source": "api_key", "api_key_header": "X-API-Key"}
        with patch("jarvis_agent.mcp_credentials.CredentialVault.read", return_value="PRIVATE_KEY") as read, \
                patch("mcp.client.streamable_http.streamable_http_client", stream), patch("mcp.ClientSession", Session):
            result = asyncio.run(OfficialMCPTransport._with_session(entry, "inventory"))
        self.assertEqual(captured["headers"]["x-api-key"], "PRIVATE_KEY")
        self.assertNotIn("authorization", captured["headers"])
        self.assertEqual(read.call_args.args, ("fixture", entry["url"], "api_key"))
        self.assertEqual(result["tools"], [])
        with patch("jarvis_agent.mcp_credentials.CredentialVault.read", return_value=None):
            with self.assertRaises(MCPUnavailable):
                asyncio.run(OfficialMCPTransport._with_session(entry, "call", tool="write"))


class ControlCenterWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = MCPConnectionsPanel()
        self.addCleanup(self.panel.close)
        self.events = []
        self.panel.requested.connect(lambda *args: self.events.append(args))

    def test_runtime_toggle_is_pending_until_worker_ack_and_rolls_back_on_failure(self):
        self.panel.apply_snapshot({"enabled": False})
        self.panel.runtime_switch.click()
        self.assertEqual(self.events[-1], ("runtime_set", "", "true"))
        self.assertFalse(self.panel.runtime_switch.isEnabled())
        self.panel.apply_snapshot({"enabled": False})
        self.assertTrue(self.panel.runtime_switch.isChecked())
        self.panel.show_result({"success": False, "operation": "runtime_set", "reason": "OSError"})
        self.assertFalse(self.panel.runtime_switch.isChecked())
        self.assertTrue(self.panel.runtime_switch.isEnabled())

    def test_inventory_is_plain_text_and_api_key_cleared_after_explicit_store(self):
        self.panel.apply_snapshot({"servers": [{"id": "fixture", "kind": "http", "connection_state": "authorization_required",
            "resources": [{"name": "<script>untrusted</script>", "uri": "fixture://record"}]}]})
        self.panel.server_picker.setCurrentIndex(1)
        self.assertIn("Autorisation necessaire", self.panel.connection_status.text())
        self.assertIn("<script>", self.panel.inventory.toPlainText())
        self.panel.credential_kind.setCurrentIndex(2)
        self.panel.bearer.setText("PRIVATE_KEY")
        self.panel.store_credential_button.click()
        self.assertEqual(self.events[-1][0], "store_api_key")
        self.assertEqual(self.panel.bearer.text(), "")
        self.assertNotIn("PRIVATE_KEY", self.panel.feedback.text())
