"""MCP hub / Hermes interoperability contracts, all offline with fake sessions."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jarvis_agent.hermes_reliability import ActionLedger
from jarvis_agent.mcp_client_bridge import (
    HERMES_CHANNEL_TOOLS, MCPClientBridge, safe_hermes_environment,
)
from jarvis_agent.mcp_hub import MCPHub, check_https_endpoint
from jarvis_agent.mcp_connections_ui import MCPConnectionsPanel

from PySide6.QtWidgets import QApplication


class DummyMcpSession:
    def __init__(self):
        self.list_calls = 0
        self.tool_calls = []
        self.fail_call = False
        self.tools = [
            SimpleNamespace(name="read_table", description="Read sheet"),
            SimpleNamespace(name="delete_everything", description="Danger"),
            SimpleNamespace(name="messages_read", description="Read channel"),
            SimpleNamespace(name="messages_send", description="Send message"),
        ]

    async def list_tools(self):
        self.list_calls += 1
        return SimpleNamespace(tools=list(self.tools))

    async def call_tool(self, tool, args):
        self.tool_calls.append((tool, dict(args)))
        if self.fail_call:
            raise TimeoutError("may have sent")
        return SimpleNamespace(isError=False, content=[{"text": "PRIVATE"}])


class HubContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "mcp.json"
        self.hub = MCPHub(self.path)

    def _add(self, **kw):
        self.hub.add_remote(
            server_id=kw.get("server_id", "sheets"),
            label=kw.get("label", "sheets"),
            endpoint=kw.get("endpoint", "https://mcp.example.com/mcp"),
            allowed_tools=kw.get("allowed_tools", ("read_table",)),
            authorization_env=kw.get("authorization_env", ""),
        )

    def test_presets_are_not_connected_and_new_servers_default_disabled(self):
        self.assertGreaterEqual(len(self.hub.catalog()), 6)
        self.assertEqual(self.hub.list_servers(), [])
        self._add()
        row = self.hub.list_servers()[0]
        self.assertFalse(row["enabled"])
        self.assertFalse(row["connected"])
        self.assertTrue(row["eligible"])
        self.assertEqual(row["tool_allowlist"], ["read_table"])
        self.assertEqual(row["status"], "désactivé")
        self.assertEqual(json.loads(self.path.read_text())["connectors"][0]["require_approval"], "always")

    def test_auth_token_never_written_and_missing_auth_refuses_enable(self):
        self._add(authorization_env="TEST_MCP_PRIVATE_TOKEN")
        with patch.dict(os.environ, {"TEST_MCP_PRIVATE_TOKEN": ""}):
            with self.assertRaises(PermissionError):
                self.hub.set_enabled("sheets", enabled=True)
        with patch.dict(os.environ, {"TEST_MCP_PRIVATE_TOKEN": "secret_token"}):
            self.hub.set_enabled("sheets", enabled=True)
            row = self.hub.list_servers()[0]
            self.assertTrue(row["enabled"])
            self.assertTrue(row["auth_ready"])
            self.assertEqual(row["status"], "configuré_non_testé")
        self.assertNotIn("secret_token", self.path.read_text())

    def test_remote_policy_explicit_allowlist_and_approval(self):
        self._add()
        self.hub.set_enabled("sheets", enabled=True)
        with self.assertRaises(PermissionError):
            self.hub.authorize_call("sheets", "read_table", approved=False)
        with self.assertRaises(PermissionError):
            self.hub.authorize_call("sheets", "delete_everything", approved=True)
        allowed = self.hub.authorize_call("sheets", "read_table", approved=True)
        self.assertEqual(allowed.server_label, "sheets")
        self.hub.set_enabled("sheets", enabled=False)
        with self.assertRaises(PermissionError):
            self.hub.authorize_call("sheets", "read_table", approved=True)

    def test_https_and_unique_id_validation(self):
        for url in (
            "http://example.com/mcp", "https://localhost/mcp",
            "https://127.0.0.1/mcp", "https://10.0.0.10/mcp",
            "https://user:pass@somewhere.com/mcp",
            "https://example.com/mcp?token=secret",
        ):
            with self.assertRaises(ValueError, msg=url):
                check_https_endpoint(url)
        self._add()
        with self.assertRaises(ValueError):
            self._add()
        with self.assertRaises(ValueError):
            self._add(server_id="broken", allowed_tools=())
        with self.assertRaises(ValueError):
            self._add(server_id="broken", allowed_tools=("bad tool()",))

    def test_mcp_unsupported_unsafe_legacy_config_remains_disabled(self):
        self.path.write_text(json.dumps({"connectors": [{
            "id":"unsafe","server_label":"unsafe","server_url":"https://example.com/mcp",
            "enabled":True,"require_approval":"never","allowed_tools":["execute_anything"],
        }]}))
        row = self.hub.list_servers()[0]
        self.assertFalse(row["eligible"])
        self.assertEqual(row["status"], "configuration_invalide")
        with self.assertRaises(ValueError):
            self.hub.set_enabled("unsafe", enabled=True)

    def test_interleaved_per_server_configuration_is_isolated(self):
        self._add()
        self._add(server_id="maps",label="maps",
                  endpoint="https://maps.example.com/mcp",
                  allowed_tools=("lookup",))
        self.hub.set_enabled("sheets", enabled=True)
        rows = {x["id"]: x for x in self.hub.list_servers()}
        self.assertTrue(rows["sheets"]["enabled"])
        self.assertFalse(rows["maps"]["enabled"])


class BridgeContracts(HubContracts):
    def setUp(self):
        super().setUp()
        self.session = DummyMcpSession()
        self.created = 0
        session = self.session
        self.inputs = []

        @asynccontextmanager
        async def fake_remote(connector):
            self.created += 1
            self.inputs.append(connector.connector_id)
            yield session

        @asynccontextmanager
        async def fake_hermes(executable):
            self.created += 1
            self.inputs.append(executable)
            yield session

        self.bridge = MCPClientBridge(
            hub=self.hub, remote_factory=fake_remote,
            hermes_factory=fake_hermes,
            ledger=ActionLedger(Path(self.tmp.name) / "journal"),
        )

    def test_discover_filters_disallowed_tools_without_execution(self):
        self._add()
        self.hub.set_enabled("sheets", enabled=True)
        got = asyncio.run(self.bridge.discover_remote("sheets"))
        self.assertEqual(got["source"], "direct_mcp")
        self.assertEqual([x["name"] for x in got["tools"]], ["read_table"])
        self.assertEqual(self.session.tool_calls, [])
        self.assertEqual(self.created, 1)
        self.assertFalse(got["verified_goal"])

    def test_hermes_stdio_only_exposes_messaging_not_google_mcp(self):
        got = asyncio.run(self.bridge.discover_hermes(executable="hermes"))
        self.assertEqual(got["source"], "hermes_messaging_stdio")
        self.assertEqual(set(x["name"] for x in got["tools"]),
                         {"messages_read", "messages_send"})
        self.assertEqual(len(HERMES_CHANNEL_TOOLS), 10)
        self.assertEqual(self.session.tool_calls, [])

    def test_hermes_send_requires_approval_and_allowlist(self):
        async def refuse():
            await self.bridge.call_hermes(
                tool="messages_send", arguments={"text": "hello"}, approved=False
            )
        with self.assertRaises(PermissionError):
            asyncio.run(refuse())
        self.assertEqual(self.created, 0)

        with self.assertRaises(PermissionError):
            asyncio.run(self.bridge.call_hermes(
                tool="remote_filesystem_tool", approved=True
            ))
        self.assertEqual(self.created, 0)

        result = asyncio.run(self.bridge.call_hermes(
            tool="messages_send", arguments={"target": "fake", "text": "hello"},
            approved=True
        ))
        self.assertTrue(result["success"])
        self.assertFalse(result["verified"])
        self.assertNotIn("PRIVATE", repr(result))

    def test_uncertain_external_send_must_not_be_replayed(self):
        self.session.fail_call = True
        call = lambda: self.bridge.call_hermes(
            tool="messages_send", arguments={"target": "fake", "text": "hello"},
            approved=True,
        )
        first = asyncio.run(call())
        self.assertEqual(first["error"], "mcp_outcome_unknown_requires_review")
        self.assertEqual(len(self.session.tool_calls), 1)
        second = asyncio.run(call())
        self.assertEqual(second["error"], "prior_outcome_unknown")
        self.assertEqual(len(self.session.tool_calls), 1)

    def test_safe_hermes_env_never_exposes_model_or_api_secrets(self):
        with patch.dict(os.environ, {
            "PATH": "/bin", "OPENAI_API_KEY": "private",
            "CEREBRAS_API_KEY": "sensitive",
            "HOME": "/tmp/home",
        }):
            got = safe_hermes_environment()
        self.assertEqual(got["PATH"], "/bin")
        self.assertNotIn("OPENAI_API_KEY", got)
        self.assertNotIn("CEREBRAS_API_KEY", got)


class MCPPanelContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hub = MCPHub(Path(self.tmp.name) / "catalog.json")

    def test_ui_never_probes_or_installs_on_open(self):
        panel = MCPConnectionsPanel(hub=self.hub)
        try:
            emitted = []
            panel.request_probe.connect(
                lambda k, v: emitted.append((k, v))
            )
            self.assertEqual(emitted, [])
            self.assertEqual(self.hub.list_servers(), [])
            self.assertFalse(panel.probe_button.isEnabled())
            panel.hermes_probe.click()
            self.assertEqual(emitted, [("hermes", "hermes")])
        finally:
            panel.close()

    def test_add_activate_disable_and_probe_are_per_server(self):
        panel = MCPConnectionsPanel(hub=self.hub)
        try:
            emitted = []
            panel.request_probe.connect(
                lambda k, v: emitted.append((k, v))
            )
            panel.server_id.setText("my_test")
            panel.server_label.setText("my_test")
            panel.server_url.setText("https://example.com/mcp")
            panel.allowed_tools.setText("read_data")
            panel.add_button.click()
            self.assertEqual(len(self.hub.list_servers()), 1)
            self.assertFalse(self.hub.list_servers()[0]["enabled"])
            panel.servers.setCurrentIndex(1)
            panel.enable_button.click()
            self.assertTrue(self.hub.list_servers()[0]["enabled"])
            panel.probe_button.click()
            self.assertEqual(emitted, [("remote", "my_test")])
            panel.disable_button.click()
            self.assertFalse(self.hub.list_servers()[0]["enabled"])
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
