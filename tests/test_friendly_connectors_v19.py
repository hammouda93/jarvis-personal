"""Simple connector choices with the original Gmail permission gateway."""
import json
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from jarvis_agent.connector_functions_panel import ConnectorFunctionsPanel
from jarvis_agent.gmail_connection_panel import GmailConnectionPanel
from jarvis_agent.mcp_control import MCPCommand, perform_mcp_command
from tests.test_gmail_connector_v18 import Fixture, TOOLS, schema


class FunctionChoicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_same_widget_handles_a_different_service_and_only_observed_functions(self):
        panel = ConnectorFunctionsPanel({"read": "Consulter les rendez-vous", "manage": "Modifier les rendez-vous"})
        self.addCleanup(panel.close)
        panel.load([{"name": "calendar.list", "group": "read"}, {"name": "calendar.create", "group": "manage"}], ["unknown"])
        events = []
        panel.selection_changed.connect(events.append)
        self.assertEqual(panel.selected(), [])
        self.assertEqual(events, [])
        panel.choices["read"].click()
        self.assertEqual(events, [["calendar.list"]])
        self.assertEqual(panel.selected(), ["calendar.list"])
        self.assertEqual(panel.choices["manage"].checkState(), Qt.Unchecked)

    def test_mixed_access_is_preserved_until_one_explicit_group_choice(self):
        panel = ConnectorFunctionsPanel({"read": "Lire", "send": "Envoyer"})
        self.addCleanup(panel.close)
        rows = [{"name": "read_one", "group": "read"}, {"name": "read_two", "group": "read"}]
        events = []
        panel.selection_changed.connect(events.append)
        panel.load(rows, ["read_one"])
        self.assertEqual(panel.choices["read"].checkState(), Qt.PartiallyChecked)
        self.assertTrue(panel.choices["send"].isHidden())
        panel.load(rows, panel.selected())
        self.assertEqual(events, [])
        self.assertEqual(panel.selected(), ["read_one"])
        panel.choices["read"].click()
        self.assertEqual(set(events[-1]), {"read_one", "read_two"})
        panel.choices["read"].click()
        self.assertEqual(events[-1], [])


class FriendlyGmailTests(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = GmailConnectionPanel(registry=self.registry, workspace=self.workspace)
        self.addCleanup(panel.close)
        return panel

    def test_first_screen_is_simple_and_advanced_options_remain_available(self):
        panel = self.panel()
        self.assertFalse(panel.connect_button.isHidden())
        self.assertTrue(panel.advanced_body.isHidden())
        self.assertTrue(panel.functions_section.isHidden())
        self.assertTrue(panel.grant_button.isHidden())
        self.assertTrue(panel.runtime_button.isHidden())
        self.assertTrue(panel.test_button.isHidden())
        panel.advanced_toggle.click()
        self.assertFalse(panel.advanced_body.isHidden())
        panel.provider.setCurrentIndex(1)
        self.assertFalse(panel.connect_button.isEnabled())
        panel.provider.setCurrentIndex(0)
        self.assertTrue(panel.connect_button.isEnabled())
        self.assertFalse(self.registry.path.exists())
        self.assertFalse(self.workspace.path.exists())

    def test_group_choices_activate_through_original_commands_without_mail_dispatch(self):
        self.connect()
        panel, commands, requests = self.panel(), [], []
        panel.advanced_toggle.click()
        panel.requested.connect(lambda *args: commands.append(args))
        panel.request_ready.connect(requests.append)
        panel.functions.choices["read"].click()
        panel.functions.choices["draft"].click()
        self.assertEqual(commands, [])
        self.assertEqual(self.registry.list_servers()[0]["allowed"], 0)
        self.assertFalse(panel.grant_button.isHidden())
        panel.grant_button.click()
        op, sid, value = commands.pop()
        self.assertEqual(op, "gmail_grant")
        self.assertEqual(set(json.loads(value)["selected"]), {"GMAIL-GET-PROFILE", "GMAIL-SEARCH-EMAILS", "GMAIL-CREATE-DRAFT"})
        result = perform_mcp_command(self.registry, MCPCommand(op, sid, value))
        panel.show_result(result)
        self.assertTrue(self.registry.is_auto_read_allowed("gmail", "GMAIL-SEARCH-EMAILS"))
        self.assertFalse(self.registry.is_allowed("gmail", "GMAIL-SEND-EMAIL"))
        self.assertTrue(panel.grant_button.isHidden())
        self.assertFalse(panel.runtime_button.isHidden())
        self.assertIn("autres services", panel.runtime_note.text())
        panel.runtime_button.click()
        panel.show_result(perform_mcp_command(self.registry, MCPCommand(*commands.pop())))
        self.assertFalse(panel.test_button.isHidden())
        panel.test_button.click()
        self.assertEqual(len(requests), 1)
        self.assertIn("Ne modifie rien", requests[0])
        self.assertEqual(self.transport.calls, [])

    def test_existing_granular_grants_are_not_widened_by_group_rendering(self):
        self.connect()
        self.grant(names=["GMAIL-GET-PROFILE"])
        panel = self.panel()
        panel.refresh()
        self.assertEqual(panel.functions.choices["read"].checkState(), Qt.PartiallyChecked)
        self.assertFalse(self.registry.is_allowed("gmail", "GMAIL-SEARCH-EMAILS"))
        panel.functions.choices["read"].click()
        self.assertEqual(panel.functions.choices["read"].checkState(), Qt.Checked)
        self.assertFalse(self.registry.is_allowed("gmail", "GMAIL-SEARCH-EMAILS"))
        panel.advanced_toggle.click()
        for index in range(panel.tools.topLevelItemCount()):
            item = panel.tools.topLevelItem(index)
            if item.data(0, Qt.UserRole) == "GMAIL-GET-PROFILE":
                item.setCheckState(0, Qt.Unchecked)
        self.assertEqual(panel.functions.choices["read"].checkState(), Qt.PartiallyChecked)
        self.assertEqual(panel.functions.selected(), ["GMAIL-SEARCH-EMAILS"])
        panel.reload()
        self.assertEqual(panel.functions.selected(), ["GMAIL-GET-PROFILE"])
        self.assertTrue(panel.grant_button.isHidden())

    def test_changed_inventory_preserves_choices_but_requires_visible_reload(self):
        self.connect()
        panel = self.panel()
        panel.functions.choices["read"].click()
        self.registry.discover("gmail", [*TOOLS, schema("GMAIL-FORWARD-EMAIL")])
        panel.refresh()
        self.assertEqual(set(panel.functions.selected()), {"GMAIL-GET-PROFILE", "GMAIL-SEARCH-EMAILS"})
        self.assertFalse(panel.grant_button.isEnabled())
        self.assertFalse(panel.reload_button.isHidden())
        panel.reload_button.click()
        self.assertTrue(panel.grant_button.isEnabled())
        self.assertTrue(panel.reload_button.isHidden())
        self.assertEqual(panel.functions.selected(), [])
        self.assertIn("7", panel.functions.summary.text())

    def test_expired_authorization_returns_the_connection_button_to_the_main_screen(self):
        self.connect()
        self.grant()
        self.registry.set_runtime_enabled(True)
        panel = self.panel()
        self.assertTrue(panel.connect_button.isHidden())
        self.assertFalse(panel.test_button.isHidden())
        self.registry.note_connection_state("gmail", "authorization_required")
        panel.refresh()
        self.assertFalse(panel.connect_button.isHidden())
        self.assertTrue(panel.connect_button.isEnabled())
        self.assertTrue(panel.test_button.isHidden())
        self.assertTrue(panel.grant_button.isHidden())
