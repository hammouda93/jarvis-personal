"""Offscreen geometry contracts, not real Windows desktop acceptance."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import QApplication
from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.ui import JarvisWindow


class WindowGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patches = [patch.object(AssistantWorker, "__init__", lambda self: QObject.__init__(self)),
                        patch.object(AssistantWorker, "set_text_mode"), patch.object(QThread, "start"),
                        patch("jarvis_agent.operator_console.snapshot", return_value={}),
                        patch("jarvis_agent.ui.operator_snapshot", return_value={})]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.window = JarvisWindow()
        self.window._operator_timer.stop()
        self.addCleanup(self.window.close)

    def resize(self, width, height):
        self.window.resize(width, height)
        self.window.show()
        self.app.processEvents()
        self.assertEqual((self.window.width(), self.window.height()), (width, height))

    def test_entire_control_surface_scrolls_and_bottom_connections_are_reachable(self):
        self.resize(1024, 640)
        panel = self.window.operator_console
        scroll = panel.scroll_area
        self.assertTrue(scroll.isAncestorOf(panel.mission_begin_button))
        self.assertTrue(scroll.isAncestorOf(panel.mode_line))
        scroll.ensureWidgetVisible(panel.skills_panel)
        self.app.processEvents()
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

    def test_mcp_has_direct_navigation_separate_from_control_scroll(self):
        self.resize(660, 500)
        self.window.side_tabs.setCurrentWidget(self.window.mcp_scroll)
        self.app.processEvents()
        panel = self.window.operator_console.mcp_panel
        self.assertTrue(panel.isVisible())
        self.assertFalse(self.window.operator_console.scroll_area.isAncestorOf(panel))
        for page in (panel.workspace_page, panel.services_page,
                     panel.catalog_page, panel.add_page, panel.gmail_panel):
            panel.tabs.setCurrentWidget(page)
            self.app.processEvents()
            self.assertEqual(self.window.mcp_scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(panel.tabs.count(), 5)
        self.assertEqual(panel.tabs.tabText(0), "Mon espace")

    def test_small_logical_screen_with_text_entry_keeps_window_controls_accessible(self):
        self.window.conversation_button.setChecked(True)
        self.resize(660, 500)
        for control in (self.window.chat_input, self.window.chat_send_button,
                        self.window.close_button, self.window.compact_button, self.window.resize_grip):
            point = control.mapTo(self.window, control.rect().topLeft())
            self.assertGreaterEqual(point.x(), 0)
            self.assertGreaterEqual(point.y(), 0)
            self.assertLessEqual(point.x() + control.width(), self.window.width())
            self.assertLessEqual(point.y() + control.height(), self.window.height())
        self.assertGreater(self.window.workspace_splitter.handleWidth(), 0)

    def test_resizable_panels_do_not_have_a_fixed_maximum_width(self):
        self.resize(1280, 760)
        splitter = self.window.workspace_splitter
        splitter.setSizes([400, 800])
        self.app.processEvents()
        self.assertGreater(self.window.side_tabs.width(), 410)
        self.assertGreater(self.window.operator_console.scroll_area.viewport().height(), 100)

    def test_architecture_is_directly_accessible_and_routes_to_existing_panels(self):
        self.resize(660, 500)
        panel = self.window.architecture_panel
        self.assertEqual(self.window.side_tabs.indexOf(panel), 1)
        self.window.side_tabs.setCurrentWidget(panel)
        self.app.processEvents()
        self.assertTrue(panel.isVisible())
        overview = panel.tabs.widget(0)
        self.assertEqual(overview.horizontalScrollBar().maximum(), 0)
        self.window._show_architecture_section("brains")
        self.assertIs(self.window.side_tabs.currentWidget(), self.window.brain_scroll)
        self.window._show_architecture_section("mcp")
        self.assertIs(self.window.side_tabs.currentWidget(), self.window.mcp_scroll)
        self.window._show_architecture_section("skills")
        self.app.processEvents()
        self.assertIs(self.window.side_tabs.currentWidget(), self.window.operator_console)
        self.assertTrue(self.window.operator_console.operational_preferences.isVisible())

    def test_profiles_are_reachable_in_full_window_without_horizontal_overflow(self):
        from PySide6.QtWidgets import QScrollArea
        self.resize(660, 500)
        panel = self.window.architecture_panel
        self.window.side_tabs.setCurrentWidget(panel)
        panel.tabs.setCurrentWidget(panel.profile_editor)
        self.app.processEvents()
        scroll = panel.profile_editor.findChild(QScrollArea)
        self.assertTrue(panel.profile_editor.isVisible())
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        scroll.ensureWidgetVisible(panel.profile_editor.save_button)
        self.app.processEvents()
        self.assertTrue(panel.profile_editor.save_button.isVisible())

    def test_compact_and_normal_modes_restore_visible_controls(self):
        self.resize(1024, 640)
        self.window.compact_button.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.window.chat_panel.isVisible())
        self.window.compact_button.setChecked(False)
        self.app.processEvents()
        self.assertTrue(self.window.side_tabs.isVisible())
        self.assertTrue(self.window.resize_grip.isVisible())
        self.assertEqual(self.window.compact_button.text(), "")

    def test_daily_workspace_is_reachable_at_narrow_window_and_routes_to_mcp(self):
        self.resize(660, 500)
        panel = self.window.personal_workspace
        self.window._show_architecture_section("workspace")
        self.app.processEvents()
        self.assertIs(self.window.side_tabs.currentWidget(), panel)
        for index in range(panel.tabs.count()):
            panel.tabs.setCurrentIndex(index)
            self.app.processEvents()
            self.assertEqual(panel.tabs.currentWidget().horizontalScrollBar().maximum(), 0)
        panel.connections_requested.emit()
        self.assertIs(self.window.side_tabs.currentWidget(), self.window.mcp_scroll)

    def test_workspace_handoff_uses_worker_queue_without_overwriting_chat_draft(self):
        self.window.chat_input.setText("Brouillon personnel à conserver")
        with patch.object(self.window._thread, "isRunning", return_value=True), \
                patch.object(self.window._worker, "submit_text", return_value=True) as submit:
            self.window.personal_workspace.request_ready.emit("Organise ma journée")
        submit.assert_called_once_with("Organise ma journée")
        self.assertEqual(self.window.chat_input.text(), "Brouillon personnel à conserver")
        self.assertIn("confiée", self.window.personal_workspace.status_line.text())
        with patch.object(self.window._thread, "isRunning", return_value=True), \
                patch.object(self.window._worker, "submit_text", return_value=False):
            self.window._on_workspace_request("Nouvelle demande")
        self.assertIn("n’a pas été ajoutée", self.window.personal_workspace.status_line.text())


if __name__ == "__main__":
    unittest.main()
