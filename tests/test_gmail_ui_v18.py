"""Offscreen Gmail onboarding; no real browser login or mailbox operation."""
import json
from unittest.mock import patch

from PySide6.QtCore import QObject, QThread, Qt
from PySide6.QtWidgets import QApplication

from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.gmail_connection_panel import GmailConnectionPanel
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
from jarvis_agent.ui import JarvisWindow
from tests.test_gmail_connector_v18 import Fixture, TOOLS, schema


class GmailUITests(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = GmailConnectionPanel(registry=self.registry, workspace=self.workspace)
        self.addCleanup(panel.close)
        return panel

    def test_initial_page_is_passive_and_explains_provider_and_account_verification(self):
        with patch("webbrowser.open") as browser, patch("jarvis_agent.mcp_sdk_transport.OfficialMCPTransport.inventory") as inventory:
            panel = self.panel()
            panel.refresh()
        browser.assert_not_called()
        inventory.assert_not_called()
        self.assertFalse(self.registry.path.exists())
        self.assertFalse(self.workspace.path.exists())
        self.assertIn("Pipedream", panel.provider_note.text())
        self.assertFalse(panel.test_button.isEnabled())
        self.assertFalse(panel.grant_button.isEnabled())

    def test_parent_guided_connect_review_and_runtime_use_actual_worker_command_contract(self):
        parent = MCPConnectionsPanel()
        self.addCleanup(parent.close)
        child, commands = parent.gmail_panel, []
        parent.requested.connect(lambda *args: commands.append(args))
        child.start_connection(access="manual")
        self.assertEqual(commands[-1][0], "gmail_connect")
        self.assertTrue(child._busy)
        result = perform_mcp_command(self.registry, MCPCommand(*commands.pop()), transport=self.transport, vault=self.vault)
        parent.show_result(result)
        self.assertFalse(child._busy)
        self.assertEqual(child.tools.topLevelItemCount(), 6)
        self.assertIn("fonctions à choisir", child.status.text())
        child.select_tools(reads_only=False)
        child.grant()
        self.assertEqual(commands[-1][0], "gmail_grant")
        result = perform_mcp_command(self.registry, MCPCommand(*commands.pop()), transport=self.transport, vault=self.vault)
        parent.show_result(result)
        self.assertIn("enregistrées", child.status.text())
        self.assertEqual(len(self.transport.calls), 0)
        child.runtime_button.click()
        self.assertEqual(commands[-1], ("runtime_set", "", "true"))
        result = perform_mcp_command(self.registry, MCPCommand(*commands.pop()))
        parent.show_result(result)
        self.assertTrue(child.test_button.isEnabled())
        self.assertTrue(child.runtime_button.isHidden())
        self.assertIn("MCP activé", child.feedback.text())
        child.refresh_button.click()
        self.assertEqual(commands[-1], ("reconnect", "gmail", ""))
        result = perform_mcp_command(self.registry, MCPCommand(*commands.pop()), transport=self.transport)
        parent.show_result(result)
        self.assertIn("Inventaire actualisé", child.feedback.text())
        self.assertEqual(len(self.transport.calls), 0)

    def test_new_capabilities_preserve_dirty_selection_and_require_reload(self):
        self.connect()
        panel = self.panel()
        panel.select_tools(reads_only=False)
        selected = [panel.tools.topLevelItem(i).checkState(0) for i in range(panel.tools.topLevelItemCount())]
        self.registry.discover("gmail", [*TOOLS, schema("GMAIL-FORWARD-EMAIL")])
        panel.refresh()
        self.assertEqual(selected, [panel.tools.topLevelItem(i).checkState(0) for i in range(panel.tools.topLevelItemCount())])
        self.assertFalse(panel.grant_button.isEnabled())
        self.assertIn("sélection est conservée", panel.feedback.text())
        panel.reload()
        self.assertEqual(panel.tools.topLevelItemCount(), 7)
        self.assertTrue(panel.grant_button.isEnabled())

    def test_gmail_shortcut_opens_the_guided_page_without_a_connection(self):
        parent = MCPConnectionsPanel()
        self.addCleanup(parent.close)
        commands = []
        parent.requested.connect(lambda *args: commands.append(args))
        parent.workspace_picker.setCurrentIndex(parent.workspace_picker.findData("gmail"))
        parent.workspace_prepare_button.click()
        self.assertIs(parent.tabs.currentWidget(), parent.gmail_panel)
        self.assertEqual(commands, [])
        self.assertFalse(self.registry.path.exists())
        parent.tabs.setCurrentWidget(parent.catalog_page)
        parent.catalog_picker.setCurrentIndex(parent.catalog_picker.findData("gmail"))
        parent.catalog_prepare_button.click()
        self.assertIs(parent.tabs.currentWidget(), parent.gmail_panel)
        self.assertEqual(commands, [])
        self.assertIn("Pipedream", parent.catalog_endpoint.text())

    def test_connection_cancel_reaches_same_authorization_slot_and_error_releases_controls(self):
        parent = MCPConnectionsPanel()
        self.addCleanup(parent.close)
        inbox = MCPControlInbox()
        parent.requested.connect(inbox.submit)
        parent.gmail_panel.start_connection()
        self.assertFalse(inbox.submit("connect", "other"))
        parent.gmail_panel.cancel_button.click()
        self.assertTrue(inbox.stop_requested.is_set())
        parent.show_result({"operation": "gmail_connect", "success": False, "reason": "oauth_cancelled"})
        self.assertFalse(parent.gmail_panel._busy)
        self.assertTrue(parent.gmail_panel.connect_button.isEnabled())
        self.assertIn("annulée", parent.gmail_panel.feedback.text())

    def test_narrow_page_keeps_all_steps_accessible_by_vertical_scroll(self):
        self.connect()
        panel = self.panel()
        panel.resize(300, 500)
        panel.show()
        panel.advanced_toggle.click()
        self.app.processEvents()
        self.assertEqual(panel.scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(panel.scroll.widget().width(), panel.scroll.viewport().width())
        panel.scroll.ensureWidgetVisible(panel.grant_button)
        self.app.processEvents()
        self.assertTrue(panel.grant_button.isVisible())
        self.assertLessEqual(panel.grant_button.mapTo(panel.scroll.viewport(), panel.grant_button.rect().bottomRight()).y(), panel.scroll.viewport().height())
        self.assertGreater(panel.scroll.verticalScrollBar().maximum(), 0)
        panel.scroll.ensureWidgetVisible(panel.advanced_button)
        self.app.processEvents()
        self.assertLessEqual(panel.advanced_button.mapTo(panel.scroll.viewport(), panel.advanced_button.rect().bottomRight()).y(), panel.scroll.viewport().height())
        panel.grab()

    def test_mailbox_test_uses_chat_queue_and_preserves_user_draft(self):
        patches = [patch.object(AssistantWorker, "__init__", lambda self: QObject.__init__(self)),
            patch.object(AssistantWorker, "set_text_mode"), patch.object(QThread, "start"),
            patch("jarvis_agent.operator_console.snapshot", return_value={}),
            patch("jarvis_agent.ui.operator_snapshot", return_value={})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        window = JarvisWindow()
        window._operator_timer.stop()
        self.addCleanup(window.close)
        window.chat_input.setText("Brouillon à conserver")
        with patch.object(window._thread, "isRunning", return_value=True), \
             patch.object(window._worker, "submit_text", return_value=True) as submit:
            window.operator_console.mcp_panel.gmail_panel.test_mailbox()
        self.assertEqual(submit.call_count, 1)
        self.assertIn("Ne modifie rien", submit.call_args.args[0])
        self.assertEqual(window.chat_input.text(), "Brouillon à conserver")

    def test_worker_reports_named_gmail_errors_and_releases_authorization_lock(self):
        from PySide6.QtCore import QObject
        with patch.object(AssistantWorker, "__init__", lambda self: QObject.__init__(self)):
            worker = AssistantWorker()
        worker._mcp_registry = self.registry
        worker._mcp_control = MCPControlInbox()
        command = MCPCommand("gmail_connect", "gmail", json.dumps({"provider": "pipedream", "binding_revision": 0}))
        worker._mcp_control.submit(command.operation, command.server_id, command.value)
        results = []
        worker.mcp_control_result.connect(results.append)
        with patch("jarvis_agent.assistant_v3.perform_mcp_command", side_effect=ValueError("gmail_binding_changed")):
            worker._run_mcp_control(command)
        self.assertEqual(results[0]["reason"], "gmail_binding_changed")
        self.assertTrue(worker._mcp_control.submit("connect", "another"))

    def test_progress_keeps_authorization_pending_and_failure_shows_stage(self):
        parent = MCPConnectionsPanel()
        self.addCleanup(parent.close)
        commands = []
        parent.requested.connect(lambda *args: commands.append(args))
        child = parent.gmail_panel
        child.start_connection()
        pending = parent._authorization_pending
        parent.show_progress({"operation": "gmail_connect", "server_id": pending, "stage": "token_exchange"})
        self.assertTrue(child._busy)
        self.assertEqual(parent._authorization_pending, pending)
        self.assertTrue(child.cancel_button.isEnabled())
        self.assertIn("Échange", child.step.text())
        self.assertEqual(len(commands), 1)
        parent.show_result({"operation": "gmail_connect", "server_id": pending, "success": False,
            "reason": "oauth_token_exchange_failed", "diagnostic": {"stage": "token_exchange",
                "http_status": 400, "oauth_error": "invalid_grant"}})
        self.assertFalse(child._busy)
        self.assertEqual(parent._authorization_pending, "")
        self.assertIn("HTTP 400", child.feedback.text())
        self.assertIn("invalid_grant", child.feedback.text())

    def test_worker_reports_safe_oauth_details_through_nested_task_group_errors(self):
        from jarvis_agent.mcp_oauth import OAuthConnectionFailure
        with patch.object(AssistantWorker, "__init__", lambda self: QObject.__init__(self)):
            worker = AssistantWorker()
        worker._mcp_registry, worker._mcp_control = self.registry, MCPControlInbox()
        command = MCPCommand("gmail_connect", "gmail", json.dumps({"provider": "pipedream", "binding_revision": 0}))
        worker._mcp_control.submit(command.operation, command.server_id, command.value)
        results, logs, stages = [], [], []
        worker.mcp_control_result.connect(results.append)
        worker.log_line.connect(logs.append)
        worker.mcp_control_progress.connect(stages.append)
        def fail(*args, **kwargs):
            kwargs["progress"]({"stage": "token_exchange"})
            raise ExceptionGroup("PRIVATE_OAUTH_URL", [OAuthConnectionFailure("oauth_token_exchange_failed",
                {"stage": "token_exchange", "http_status": 400, "oauth_error": "invalid_grant"})])
        with patch("jarvis_agent.assistant_v3.perform_mcp_command", side_effect=fail):
            worker._run_mcp_control(command)
        self.assertEqual(results[0]["reason"], "oauth_token_exchange_failed")
        self.assertEqual(results[0]["diagnostic"]["http_status"], 400)
        self.assertIn("http_status=400", " ".join(logs))
        self.assertNotIn("PRIVATE", str(results) + str(logs) + str(stages))
        self.assertTrue(worker._mcp_control.submit("connect", "another"))
