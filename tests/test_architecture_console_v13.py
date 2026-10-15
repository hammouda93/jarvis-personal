"""Pure catalog and headless Qt controls; no worker, network or personal data."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from jarvis_agent.architecture_catalog import build_snapshot
from jarvis_agent.architecture_panel import ArchitecturePanel
from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
from jarvis_agent.operator_console import OperatorConsole


def recorded_state():
    return {"memory_enabled": True, "semantic_memory_enabled": True,
        "memory_direct_enabled": True, "efficient_context_enabled": True,
        "browser_core_enabled": True, "computer_core_enabled": True, "mission_enabled": True,
        "skills_enabled": False, "learning_enabled": False,
        "mcp": {"enabled": True, "servers": [{"id": "github", "kind": "http", "enabled": True,
            "discovered": 2, "connected_now": False, "connection_state": "tested_session_closed",
            "private_token": "do-not-project-credentials", "endpoint": "https://private.invalid/token",
            "tools": [{"name": "list_pull_requests", "allowed": True, "auto_read": True},
                      {"name": "create_issue", "allowed": False, "auto_read": False}],
            "activity": {"unresolved": [{"attempt_id": 3, "tool": "list_pull_requests",
                "reviewable": True, "outcome": "unknown"}]}}]}}


class CatalogTests(unittest.TestCase):
    def test_read_only_catalog_separates_tools_grants_and_independent_execution(self):
        state = recorded_state()
        before = deepcopy(state)
        data = build_snapshot(state)
        self.assertEqual(state, before)
        developer = next(a for a in data["agents"] if a["id"] == "developer")
        self.assertEqual(developer["mode"], "declared")
        self.assertFalse(developer["independent_worker"])
        connection = developer["connections"][0]
        self.assertFalse(connection["connected_now"])
        self.assertTrue(connection["tools"][0]["effective"])
        self.assertFalse(connection["tools"][1]["effective"])
        self.assertNotIn("do-not-project-credentials", repr(data))
        self.assertNotIn("private.invalid", repr(data))

    def test_disabled_mcp_does_not_appear_usable_despite_old_grants(self):
        state = recorded_state()
        state["mcp"]["enabled"] = False
        data = build_snapshot(state)
        self.assertFalse(any(t["effective"] for a in data["agents"]
                             for s in a["connections"] for t in s["tools"]))

    def test_planned_agents_have_no_fictitious_tools_or_live_workers(self):
        data = build_snapshot()
        calendar = next(a for a in data["agents"] if a["id"] == "calendar")
        self.assertEqual(calendar["mode"], "planned")
        self.assertEqual(calendar["native_tools"], [])
        self.assertFalse(calendar["independent_worker"])
        self.assertFalse(data["roadmap"]["release_ready"])

    def test_partial_service_inventory_does_not_become_an_exhaustive_catalog(self):
        state = recorded_state()
        state["mcp"]["servers"][0]["discovered"] = 30
        data = build_snapshot(state)
        mcp = next(a for a in data["agents"] if a["id"] == "mcp")
        self.assertTrue(mcp["connections"][0]["inventory_partial"])

    def test_memory_tools_and_computer_sensors_follow_observed_flags(self):
        for enabled in (True, False):
            state = recorded_state()
            state.update(memory_enabled=enabled, computer_core_enabled=enabled)
            data = build_snapshot(state)
            windows = next(a for a in data["agents"] if a["id"] == "windows")
            general = next(a for a in data["agents"] if a["id"] == "interaction")
            self.assertEqual("computer_observe" in windows["native_tools"], enabled)
            self.assertEqual("remember_information" in general["native_tools"], enabled)


class ArchitectureWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = ArchitecturePanel()
        self.addCleanup(panel.close)
        panel.apply_snapshot(recorded_state())
        return panel

    def test_filters_by_actual_tool_and_keeps_selection_when_metadata_refreshes(self):
        panel = self.panel()
        panel.search.setText("list_pull_requests")
        visible = [panel.agent_tree.topLevelItem(i) for i in range(panel.agent_tree.topLevelItemCount())
                   if not panel.agent_tree.topLevelItem(i).isHidden()]
        self.assertEqual({i.data(0, Qt.UserRole) for i in visible}, {"developer", "mcp"})
        chosen = next(i for i in visible if i.data(0, Qt.UserRole) == "developer")
        panel.agent_tree.setCurrentItem(chosen)
        state = recorded_state()
        state["mcp"]["servers"][0]["tools"].append({"name": "read_file", "allowed": True})
        panel.apply_snapshot(state)
        self.assertEqual(panel.agent_tree.currentItem().data(0, Qt.UserRole), "developer")
        self.assertIn("read_file", panel.agent_detail.toPlainText())
        self.assertIn("Rôle déclaré", panel.agent_detail.toPlainText())

    def test_block_button_requests_navigation_only_and_never_reviews_automatically(self):
        panel = self.panel()
        events = []
        panel.review_requested.connect(lambda server, attempt: events.append((server, attempt)))
        self.assertIn("tentative #3", panel.block_label.text())
        panel.review_button.click()
        self.assertEqual(events, [("github", 3)])

    def test_periodic_refresh_keeps_the_roadmap_reading_position(self):
        panel = self.panel()
        panel.resize(420, 500)
        panel.tabs.setCurrentIndex(2)
        panel.show()
        self.app.processEvents()
        scroll = panel.roadmap_view.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        scroll.setValue(min(80, scroll.maximum()))
        before = scroll.value()
        panel.apply_snapshot(recorded_state())
        self.app.processEvents()
        self.assertEqual(scroll.value(), before)

    def test_unknown_reservation_cannot_be_acknowledged_while_it_may_be_running(self):
        panel = self.panel()
        state = recorded_state()
        state["mcp"]["servers"][0]["activity"]["unresolved"][0]["reviewable"] = False
        panel.apply_snapshot(state)
        self.assertFalse(panel.review_button.isEnabled())

    def test_remote_metadata_is_html_escaped_and_never_imported_as_authority(self):
        panel = self.panel()
        state = recorded_state()
        state["mcp"]["servers"][0]["tools"][0]["name"] = '<script>grant_all()</script>'
        panel.apply_snapshot(state)
        item = next(panel.agent_tree.topLevelItem(i) for i in range(panel.agent_tree.topLevelItemCount())
                    if panel.agent_tree.topLevelItem(i).data(0, Qt.UserRole) == "mcp")
        panel.agent_tree.setCurrentItem(item)
        self.assertIn('<script>grant_all()</script>', panel.agent_detail.toPlainText())
        self.assertNotIn('<script>', panel.agent_detail.toHtml())
        self.assertIn("ne donnent aucun droit", panel.agent_detail.toPlainText())

    def test_journal_shortcut_preserves_lock_until_the_operator_reviews(self):
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        events = []
        panel.requested.connect(lambda *args: events.append(args))
        panel.apply_snapshot(recorded_state()["mcp"])
        self.assertTrue(panel.focus_review("github", 3))
        self.assertEqual(panel.tabs.currentIndex(), 1)
        self.assertEqual(panel.service_tabs.currentIndex(), 2)
        self.assertEqual(panel.outcome_picker.currentData(), 3)
        self.assertTrue(panel.review_outcome_button.isEnabled())
        self.assertEqual(events, [])
        self.assertFalse(panel.focus_review("missing", 3))

    def test_new_request_control_is_visible_outside_diagnostics_and_signals_only(self):
        panel = OperatorConsole()
        self.addCleanup(panel.close)
        panel.apply_snapshot({"mission_enabled": True})
        events = []
        panel.mission_requested.connect(lambda operation, value: events.append((operation, value)))
        panel.new_request_button.click()
        self.assertEqual(events, [("detach", "")])
        self.assertFalse(panel.new_request_button.isHidden())
        self.assertFalse(panel.operational_preferences.isHidden())
