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
        scroll.ensureWidgetVisible(panel.mcp_panel)
        self.app.processEvents()
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

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


if __name__ == "__main__":
    unittest.main()
