"""Plugin-style Gmail login: connection makes discovered functions usable."""
import json
from unittest.mock import Mock, patch
import time

from PySide6.QtWidgets import QApplication, QCheckBox

from jarvis_agent.gmail_connection import snapshot
from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
from jarvis_agent.mcp_control import MCPCommand, perform_mcp_command
from jarvis_agent.mcp_sdk_transport import MCPUnavailable
from tests.test_gmail_connector_v18 import Fixture, schema


class PluginConnectionTests(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def ready(self, provider="pipedream", sid="gmail"):
        revision=(self.workspace.bindings().get("gmail") or {}).get("revision", 0)
        return self.command("gmail_connect", {"provider":provider, "binding_revision":revision, "access":"plugin"}, sid)

    def test_primary_button_connects_and_exposes_functions_without_another_setup_command(self):
        parent=MCPConnectionsPanel()
        self.addCleanup(parent.close)
        panel=parent.gmail_panel
        commands=[]
        parent.requested.connect(lambda *args: commands.append(args))
        panel.connect_button.click()
        self.assertEqual(len(commands), 1)
        self.assertEqual(json.loads(commands[0][2])["access"], "plugin")
        result=perform_mcp_command(self.registry, MCPCommand(*commands[0]), transport=self.transport, vault=self.vault)
        parent.show_result(result)
        self.assertEqual(result["access_mode"], "plugin")
        self.assertTrue(self.registry.runtime_enabled())
        self.assertTrue(parent.runtime_switch.isChecked())
        self.assertTrue(all(t["allowed"] for t in snapshot(self.registry,self.workspace)["tools"]))
        self.assertFalse(self.registry.is_allowed("gmail","list_calendar_events"))
        self.assertFalse(self.registry.is_allowed("gmail","COMPOSIO_MULTI_EXECUTE_TOOL"))
        self.assertTrue(panel.connect_button.isHidden())
        self.assertTrue(panel.runtime_button.isHidden())
        self.assertTrue(panel.grant_button.isHidden())
        self.assertTrue(panel.advanced_body.isHidden())
        self.assertEqual(panel.functions_section.findChildren(QCheckBox), [])
        self.assertIn("Envoyer", panel.available_functions.text())
        self.assertIn("prêt", panel.step.text())
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(len(commands), 1)
        gateway=self.gateway()
        self.assertFalse(gateway.requires_confirmation("mcp__gmail__GMAIL_SEARCH_EMAILS"))
        self.assertTrue(gateway.requires_confirmation("mcp__gmail__GMAIL_SEND_EMAIL"))
        self.assertTrue(gateway.execute("mcp__gmail__GMAIL_GET_PROFILE",{}).success)
        self.assertEqual(len(self.transport.calls),1)

    def test_existing_provider_keeps_its_authentication_and_only_grants_gmail(self):
        self.registry.add_http("shared", "https://example.test/mcp")
        result=self.ready("existing", "shared")
        self.assertEqual(self.transport.authorizations, [])
        self.assertTrue(self.registry.runtime_enabled())
        self.assertEqual(self.registry.get_server("shared")["url"],"https://example.test/mcp")
        self.assertTrue(all(t["allowed"] for t in result["gmail"]["tools"]))
        self.assertFalse(self.registry.is_allowed("shared","list_calendar_events"))
        self.assertEqual(self.transport.calls,[])

    def test_no_gmail_functions_does_not_activate_or_claim_ready(self):
        self.transport.tools=[schema("list_calendar_events")]
        result=self.ready()
        self.assertFalse(self.registry.runtime_enabled())
        self.assertEqual(result["gmail"]["tools"],[])
        self.assertFalse(result["gmail"]["mailbox_identity_verified"])
        self.assertEqual(self.registry.list_servers()[0]["allowed"],0)
        self.assertNotIn("Gmail est connecté", result["message"])

    def test_failed_oauth_does_not_activate_or_grant_functions(self):
        self.transport.authorize=Mock(side_effect=MCPUnavailable("fixture login failure"))
        with self.assertRaises(MCPUnavailable):
            self.ready()
        self.assertFalse(self.registry.runtime_enabled())
        self.assertEqual(self.registry.list_servers()[0]["allowed"],0)
        self.assertFalse(self.registry.get_server("gmail")["enabled"])
        self.assertEqual(self.transport.calls,[])

    def test_inventory_retry_reuses_oauth_but_requires_fresh_discovery_before_grant(self):
        original = self.transport.inventory
        def broken_inventory(entry):
            original(entry)
            raise MCPUnavailable("mcp_inventory_rpc_failed", rpc_method="resources/list", rpc_code=-32000)
        with patch.object(self.transport, "inventory", side_effect=broken_inventory):
            with self.assertRaises(MCPUnavailable):
                self.ready()
        failed = self.registry.get_server("gmail")
        self.assertEqual(failed["credential_source"], "oauth")
        self.assertEqual(failed["connection_state"], "transport_error")
        self.assertFalse(failed["enabled"])
        self.assertFalse(self.registry.runtime_enabled())
        self.assertFalse(any(t["allowed"] for t in snapshot(self.registry, self.workspace)["tools"]))
        # Failed attempts still observe the existing cooldown/quota policy.
        activity = self.registry.activity()
        later = time.time() + 20
        activity.clock = lambda: later
        self.transport.tools = [schema("GMAIL-SEARCH-EMAILS"), schema("list_calendar_events")]
        with patch.object(self.registry, "activity", return_value=activity):
            result = self.ready()
        self.assertTrue(result["success"])
        self.assertEqual(len(self.transport.authorizations), 1)
        self.assertEqual(self.transport.inventories, ["gmail", "gmail"])
        self.assertEqual([t["name"] for t in result["gmail"]["tools"]], ["GMAIL-SEARCH-EMAILS"])
        self.assertTrue(all(t["allowed"] for t in result["gmail"]["tools"]))
        self.assertFalse(self.registry.is_allowed("gmail", "list_calendar_events"))
        self.assertEqual(self.transport.calls, [])

    def test_revoked_authorization_and_explicit_disconnect_still_request_consent(self):
        for reason in ("authorization_required", "disconnected"):
            with self.subTest(reason=reason):
                sid = "gmail_" + reason
                self.ready(sid=sid)
                self.command("disconnect", {}, sid)
                self.registry.note_connection_state(sid, reason)
                before = len(self.transport.authorizations)
                self.ready(sid=sid)
                self.assertEqual(len(self.transport.authorizations), before + 1)
        self.assertEqual(self.transport.calls, [])

    def test_connection_change_before_atomic_grant_does_not_activate_runtime(self):
        original=self.registry.apply_tool_review
        def conflict(server, **review):
            self.registry.set_credential_source(server,"oauth")
            return original(server,**review)
        with patch.object(self.registry,"apply_tool_review",side_effect=conflict):
            with self.assertRaisesRegex(ValueError,"connection_changed"):
                self.ready()
        self.assertFalse(self.registry.runtime_enabled())
        self.assertEqual(self.registry.list_servers()[0]["allowed"],0)

    def test_plugin_reconnection_cannot_replay_a_send_with_unknown_result(self):
        self.ready()
        gateway=self.gateway()
        self.transport.call_tool=Mock(side_effect=MCPUnavailable("fixture timeout",outcome_unknown=True))
        result=gateway.execute("mcp__gmail__GMAIL_SEND_EMAIL",{"text":"Fixture"},approved=True)
        self.assertTrue(json.loads(result.detail)["outcome_unknown"])
        activity=self.registry.activity()
        later=time.time()+20
        activity.clock=lambda:later
        with patch.object(self.registry,"activity",return_value=activity):
            self.ready()
            again=gateway.execute("mcp__gmail__GMAIL_SEND_EMAIL",{"text":"Fixture"},approved=True)
        self.assertFalse(again.success)
        self.assertEqual(self.transport.call_tool.call_count,1)
