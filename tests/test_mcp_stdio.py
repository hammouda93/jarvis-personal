from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.mcp_security import stdio_command, stdio_environment, validate_stdio
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_sdk_transport import MCPUnavailable, OfficialMCPTransport


def entry(**kwargs):
    return {"command": "server.exe", "args": ["--stdio"], "trusted_stdio": True, **kwargs}


class MCPStdioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = MCPRegistry(Path(self.temp.name) / "mcp.json")

    def test_configuration_is_disabled_and_does_not_spawn_or_discover(self):
        with patch("jarvis_agent.mcp_sdk_transport.OfficialMCPTransport.invoke") as invoke:
            self.registry.add_stdio("local", "server.exe", ["--stdio"], trusted=True)
        invoke.assert_not_called()
        row = self.registry.list_servers()[0]
        self.assertFalse(row["enabled"])
        self.assertFalse(row["connected_now"])
        self.assertEqual(row["allowed"], 0)
        self.assertEqual(self.registry.exposed_tools(), [])

    def test_local_program_requires_explicit_trust(self):
        for trust in (False, "true", 1):
            with self.assertRaisesRegex(ValueError, "explicit_program_trust"):
                self.registry.add_stdio("local", "server.exe", [], trusted=trust)

    def test_shell_launchers_and_inline_eval_are_refused(self):
        for program in ("cmd.exe", "C:/Windows/System32/cmd.exe", "powershell", "pwsh.exe", "bash", "npx.cmd", "script.bat", "script.ps1"):
            with self.assertRaisesRegex(ValueError, "shell_launcher"):
                validate_stdio(entry(command=program))
        for args in (["-c", "code"], ["--eval", "code"], ["/c", "program"], ["-EncodedCommand", "payload"]):
            with self.assertRaisesRegex(ValueError, "inline_code"):
                validate_stdio(entry(command="python.exe", args=args))

    def test_argument_arrays_and_control_characters_are_strict(self):
        for args in ("--stdio", [True], ["line\nnext"], ["\x00"], ["x"] * 65):
            with self.assertRaises(ValueError):
                validate_stdio(entry(args=args))

    def test_only_selected_environment_references_are_forwarded(self):
        with patch.dict(os.environ, {"PATH": "safe", "CEREBRAS_API_KEY": "private",
                "OPENAI_API_KEY": "private", "JARVIS_MCP_TEST_TOKEN": "selected"}, clear=True):
            env = stdio_environment(entry(env_refs={"SERVICE_TOKEN": "JARVIS_MCP_TEST_TOKEN"}))
        self.assertEqual(env, {"PATH": "safe", "SERVICE_TOKEN": "selected"})

    def test_raw_secret_values_and_startup_injection_variables_are_refused(self):
        for refs in ({"SERVICE_TOKEN": "raw-token"}, {"PYTHONPATH": "JARVIS_MCP_TEST"},
                     {"NODE_OPTIONS": "JARVIS_MCP_TEST"}, {"PATH": "JARVIS_MCP_TEST"}, []):
            with self.assertRaises(ValueError):
                validate_stdio(entry(env_refs=refs))

    def test_missing_scoped_credential_fails_before_spawn(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "credential_missing"):
                stdio_environment(entry(env_refs={"TOKEN": "JARVIS_MCP_TEST_TOKEN"}))

    def test_environment_values_never_enter_config_or_telemetry(self):
        with patch.dict(os.environ, {"JARVIS_MCP_TEST_TOKEN": "PRIVATE_VALUE"}):
            self.registry.add_stdio("local", "server.exe", [], trusted=True,
                                    env_refs={"TOKEN": "JARVIS_MCP_TEST_TOKEN"})
        self.assertNotIn("PRIVATE_VALUE", self.registry.path.read_text())
        self.assertNotIn("JARVIS_MCP_TEST_TOKEN", repr(self.registry.list_servers()))

    def test_model_permissions_can_be_revoked_all_at_once_or_removed(self):
        self.registry.add_stdio("local", "server.exe", [], trusted=True)
        self.registry.set_enabled("local", True)
        self.registry.discover("local", [{"name": "read", "input_schema": {"type": "object"}}])
        self.registry.allow_tool("local", "read", True)
        self.assertTrue(self.registry.is_allowed("local", "read"))
        self.registry.revoke_tools("local")
        self.assertFalse(self.registry.is_allowed("local", "read"))
        self.registry.remove_server("local")
        self.assertEqual(self.registry.list_servers(), [])

    def test_inbox_and_worker_configure_without_a_connection(self):
        inbox = MCPControlInbox()
        payload = json.dumps({"command": "server.exe", "args": [], "trusted": True})
        self.assertTrue(inbox.submit("add_stdio", "local", payload))
        result = perform_mcp_command(self.registry, inbox.pop_nowait())
        self.assertTrue(result["success"])
        self.assertFalse(self.registry.get_server("local")["enabled"])
        self.assertFalse(inbox.submit("add_stdio", "local", "not json"))
        self.assertFalse(inbox.submit("add_stdio", "local", '{"unknown":true}'))
        self.assertTrue(inbox.submit("remove_server", "local"))
        perform_mcp_command(self.registry, inbox.pop_nowait())
        self.assertIsNone(self.registry.get_server("local"))

    def test_resolved_shell_wrapper_cannot_bypass_validation(self):
        with patch("jarvis_agent.mcp_security.shutil.which", return_value="C:/tools/npx.cmd"):
            with self.assertRaises(ValueError):
                stdio_command(entry(command="npx"))

    def test_hermes_legacy_profile_keeps_its_explicit_config_contract(self):
        self.registry.add_hermes_local()
        profile = self.registry.get_server("hermes")
        validate_stdio(profile)
        with patch("jarvis_agent.mcp_security.shutil.which", return_value="C:/tools/hermes.exe"):
            self.assertEqual(stdio_command(profile), "C:/tools/hermes.exe")

    def test_official_sdk_receives_env_isolated_cwd_and_suppressed_stderr(self):
        captured = []
        @asynccontextmanager
        async def client(params, *, errlog):
            captured.append(params)
            self.assertEqual(errlog.name, os.devnull)
            self.assertTrue(Path(params.cwd).is_dir())
            yield (object(), object())
        class Session:
            def __init__(self, *args):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def initialize(self):
                pass
            async def list_tools(self):
                return SimpleNamespace(tools=[], next_cursor=None)
        with patch("mcp.client.stdio.stdio_client", client), patch("mcp.ClientSession", Session), \
             patch("jarvis_agent.mcp_security.shutil.which", return_value="C:/tools/server.exe"), \
             patch.dict(os.environ, {"CEREBRAS_API_KEY": "NEVER_FORWARD"}):
            result = asyncio.run(OfficialMCPTransport._with_session({"kind": "stdio", **entry()}, "discover"))
        self.assertEqual(result, [])
        self.assertNotIn("CEREBRAS_API_KEY", captured[0].env)
        self.assertFalse(Path(captured[0].cwd).exists())

    def test_persisted_command_is_revalidated_before_sdk_spawn(self):
        with patch("mcp.client.stdio.stdio_client") as spawn:
            with self.assertRaisesRegex(MCPUnavailable, "shell_launcher"):
                asyncio.run(OfficialMCPTransport._with_session({"kind": "stdio", **entry(command="cmd.exe")}, "discover"))
        spawn.assert_not_called()


class MCPStdioWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_gui_requires_trust_and_emits_only_worker_configuration(self):
        from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        requests = []
        panel.requested.connect(lambda *args: requests.append(args))
        panel.server_name.setText("local")
        panel.transport_picker.setCurrentIndex(1)
        panel.stdio_command.setText("server.exe")
        panel.stdio_arguments.setText('["--stdio"]')
        panel.add_button.click()
        self.assertEqual(requests, [])
        panel.stdio_trust.setChecked(True)
        panel.add_button.click()
        self.assertEqual(requests[0][:2], ("add_stdio", "local"))
        self.assertTrue(json.loads(requests[0][2])["trusted"])
