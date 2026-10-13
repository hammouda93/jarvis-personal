"""Owner's MCP workspace: no implicit auth, network, process launch or grants."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jarvis_agent.mcp_personal_workspace import (
    CONNECTIONS, connection_state, get_connection, workspace_summary,
)
from jarvis_agent.mcp_service_catalog import find_card


class WorkspaceProfileTests(unittest.TestCase):
    def test_core_profile_covers_real_projects_without_implicit_server(self):
        expected = {
            "github", "gmail", "google_calendar", "google_drive",
            "google_docs", "google_sheets", "google_slides", "google_contacts",
            "ms_football", "sportsbase", "youtube", "dailymotion", "whatsapp",
            "cloudflare", "heroku", "postgresql", "coaching_app",
            "coach_workspace", "chrome", "windows", "google_maps", "islamic",
        }
        self.assertTrue(expected <= {x.identifier for x in CONNECTIONS})
        self.assertEqual(len({x.identifier for x in CONNECTIONS}), len(CONNECTIONS))
        for entry in CONNECTIONS:
            with self.subTest(service=entry.identifier):
                self.assertIn(entry.integration, {
                    "native", "remote_mcp", "api_bridge", "optional"
                })
                if entry.integration == "remote_mcp":
                    self.assertIsNotNone(find_card(entry.catalog_id))

    def test_explicit_state_never_claims_authentication_or_active_session(self):
        github = get_connection("github")
        self.assertEqual(connection_state(github, []), "Non configuré")
        self.assertIn("désactivé", connection_state(github, [
            {"id": "github", "enabled": False}
        ]))
        active = connection_state(github, [{
            "id": "github", "enabled": True, "allowed": 7,
            "last_discovery_success_utc": "2026-10-13T20:00:00Z"
        }])
        self.assertIn("session non garantie", active)
        self.assertNotIn("Connecté", active)
        self.assertIn("Natif", connection_state(get_connection("chrome"), []))
        self.assertIn("Pont/API", connection_state(get_connection("ms_football"), []))
        self.assertNotIn("PRIVATE", workspace_summary([{
            "id": "github", "enabled": True, "token": "PRIVATE"
        }]))


class WorkspacePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_profile_is_read_only_and_prepares_only_verified_remote_endpoint(self):
        from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        emitted = []
        panel.requested.connect(lambda *args: emitted.append(args))
        self.assertTrue(panel.tabs.indexOf(panel.workspace_page) >= 0)
        panel.apply_snapshot({"enabled": False, "servers": []})

        panel.workspace_picker.setCurrentIndex(panel.workspace_picker.findData("github"))
        self.assertTrue(panel.workspace_prepare_button.isEnabled())
        self.assertIn("Non configuré", panel.workspace_status.text())
        panel.workspace_prepare_button.click()
        self.assertEqual(panel.tabs.currentWidget(), panel.add_page)
        self.assertEqual(panel.server_name.text(), "github")
        self.assertEqual(panel.server_url.text(), find_card("github").endpoint)
        self.assertEqual(emitted, [])

        panel.workspace_picker.setCurrentIndex(panel.workspace_picker.findData("whatsapp"))
        self.assertFalse(panel.workspace_prepare_button.isEnabled())
        panel._prepare_workspace_connection()
        self.assertEqual(emitted, [])
        self.assertEqual(panel.server_name.text(), "github")

        panel.apply_snapshot({"enabled": False, "servers": [{
            "id": "github", "kind": "http", "enabled": True,
            "allowed": 1, "last_discovery_success_utc": "2026-10-13T20:00:00Z",
            "tools": [], "quotas": {}
        }]})
        panel.workspace_picker.setCurrentIndex(panel.workspace_picker.findData("github"))
        self.assertIn("session non garantie", panel.workspace_status.text())
        self.assertEqual(emitted, [])


if __name__ == "__main__":
    unittest.main()
