"""Local Qt account surface; no OAuth, account reads or token decryption."""
import json
import os
import subprocess
import sys
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.google_workspace_catalog import CLIENT_SLOT
from jarvis_agent.google_workspace_panel import GoogleWorkspacePanel
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox
from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
from tests.google_workspace_fixtures import GoogleFixture, bundle


class GoogleUITests(GoogleFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = GoogleWorkspacePanel(registry=self.registry, workspace=self.workspace)
        self.addCleanup(panel.close)
        return panel

    def test_passive_initial_panel_has_guided_setup_and_five_services(self):
        with patch("webbrowser.open") as browser, patch.object(self.vault, "read", side_effect=AssertionError("no credentials on Qt")):
            panel = self.panel()
            panel.refresh()
        browser.assert_not_called()
        self.assertFalse(self.registry.path.exists())
        self.assertFalse(self.workspace.path.exists())
        self.assertEqual(len(panel.service_labels), 5)
        self.assertFalse(panel.connect_button.isEnabled())
        self.assertFalse(panel.test_button.isEnabled())
        self.assertTrue(panel.setup_toggle.isChecked())

    def test_ui_boot_does_not_require_optional_mcp_google_or_vault_dependencies(self):
        program = '''
import importlib.abc, sys, tempfile
from pathlib import Path
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'mcp', 'httpx2', 'keyring', 'jsonschema'}:
            raise ImportError('optional dependency deliberately absent')
sys.meta_path.insert(0, BlockOptional())
from PySide6.QtWidgets import QApplication
from jarvis_agent.google_workspace_panel import GoogleWorkspacePanel
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.personal_workspace import PersonalWorkspaceStore
app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    panel = GoogleWorkspacePanel(registry=MCPRegistry(root/'mcp.json'), workspace=PersonalWorkspaceStore(root/'workspace.sqlite3'))
    assert not list(root.iterdir())
    panel.close()
print('passive UI OK')
'''
        result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("passive UI OK", result.stdout)

    def test_import_uses_file_picker_and_only_queues_path(self):
        panel = self.panel()
        commands = []
        panel.requested.connect(lambda *args: commands.append(args))
        with patch("jarvis_agent.google_workspace_panel.QFileDialog.getOpenFileName", return_value=("C:/Downloads/client.json", "")):
            panel.choose_client()
        self.assertEqual(commands[0][:2], ("google_import_client", CLIENT_SLOT))
        self.assertEqual(json.loads(commands[0][2]), {"path": "C:/Downloads/client.json"})
        self.assertFalse(self.registry.path.exists())
        panel.show_result({"operation": "google_import_client", "success": False, "reason": "google_desktop_client_required"})
        self.assertIn("Application de bureau", panel.feedback.text())
        self.assertTrue(panel.import_button.isEnabled())

    def test_connect_carries_binding_client_lease_and_cancel_keeps_progress_pending(self):
        self.seed_client()
        panel, commands = self.panel(), []
        panel.requested.connect(lambda *args: commands.append(args))
        panel.start_connection()
        inbox = MCPControlInbox()
        self.assertTrue(inbox.submit(*commands[0]))
        self.assertFalse(panel.import_button.isEnabled())
        panel.show_progress({"operation": "google_connect", "stage": "callback_received"})
        self.assertIn("Retour du navigateur", panel.feedback.text())
        self.assertEqual(panel._pending, "google_connect")
        panel.cancel_connection()
        self.assertEqual(commands[-1], ("cancel_oauth", CLIENT_SLOT, ""))
        panel.show_result({"operation": "google_connect", "success": False, "reason": "oauth_cancelled"})
        self.assertEqual(panel._pending, "")
        self.assertTrue(panel.connect_button.isEnabled())
        self.assertIn("annulée", panel.feedback.text())

    def test_direct_test_never_uses_chat_and_preserves_detailed_result_across_refresh(self):
        self.seed_client()
        entry = self.seed_account()
        panel, commands = self.panel(), []
        panel.requested.connect(lambda *args: commands.append(args))
        panel.test_button.click()
        self.assertEqual(commands, [("google_test", entry["id"], "")])
        panel.show_result({"operation": "google_test", "success": True, "message": "Compte fixture · 22 non lus"})
        panel.refresh()
        self.assertIn("22 non lus", panel.feedback.text())
        self.assertIn("API à vérifier", panel.status.text())

    def test_readonly_google_consent_does_not_advertise_send_or_other_services(self):
        self.seed_client()
        self.seed_account(bundle(scopes=["https://www.googleapis.com/auth/gmail.readonly"]))
        panel = self.panel()
        self.assertIn("Lire un message", panel.service_labels["gmail"].text())
        self.assertNotIn("Envoyer", panel.service_labels["gmail"].text())
        self.assertNotIn("Créer", panel.service_labels["google_docs"].text())

    def test_compact_panel_retains_vertical_access_without_horizontal_scroll(self):
        panel = self.panel()
        panel.resize(300, 530)
        panel.show()
        self.app.processEvents()
        self.assertGreater(panel.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(panel.scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(panel.minimumSizeHint().width(), 300)

    def test_parent_primary_google_button_routes_to_grouped_page_without_connecting(self):
        parent, commands = MCPConnectionsPanel(), []
        self.addCleanup(parent.close)
        parent.requested.connect(lambda *args: commands.append(args))
        parent.google_button.click()
        self.assertIs(parent.tabs.currentWidget(), parent.google_panel)
        self.assertEqual(commands, [])
        for service in ("google_calendar", "google_drive", "google_sheets", "google_docs"):
            parent.workspace_picker.setCurrentIndex(parent.workspace_picker.findData(service))
            parent.workspace_prepare_button.click()
            self.assertIs(parent.tabs.currentWidget(), parent.google_panel)
        self.assertEqual(commands, [])

    def test_worker_error_is_safe_and_authorization_slot_released(self):
        self.seed_client()
        worker = AssistantWorker()
        worker._mcp_registry = self.registry
        self.assertTrue(worker._mcp_control.submit("google_connect", CLIENT_SLOT, self.packet()))
        command = worker._mcp_control.pop_nowait()
        results, logs = [], []
        worker.mcp_control_result.connect(results.append)
        worker.log_line.connect(logs.append)
        with patch("jarvis_agent.assistant_v3.perform_mcp_command", side_effect=RuntimeError("PRIVATE_ACCESS_FIXTURE")):
            worker._run_mcp_control(command)
        self.assertFalse(results[0]["success"])
        self.assertEqual(results[0]["reason"], "google_connection_failed")
        self.assertNotIn("PRIVATE_ACCESS_FIXTURE", json.dumps(results) + str(logs))
        self.assertTrue(worker._mcp_control.submit("google_test", "google_" + "a" * 16))
