"""Offscreen planet interactions, preserved drafts and narrow Qt layouts."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QThread, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.ui import JarvisWindow
from jarvis_agent.orchestra_store import OrchestraStore, WATCH_DEFAULTS, VOICE_DEFAULTS


class OrchestraUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        patches = [patch.dict(os.environ, {"JARVIS_ORCHESTRA_PATH": str(root / "orchestra.sqlite3"),
            "JARVIS_PERSONAL_WORKSPACE_PATH": str(root / "workspace.sqlite3"),
            "JARVIS_AGENT_PROFILES_PATH": str(root / "profiles.json"), "JARVIS_MCP_CONFIG_PATH": str(root / "mcp.json")}),
            patch.object(AssistantWorker, "__init__", lambda self: QObject.__init__(self)),
            patch.object(AssistantWorker, "set_text_mode"), patch.object(QThread, "start"),
            patch("jarvis_agent.operator_console.snapshot", return_value={}),
            patch("jarvis_agent.ui.operator_snapshot", return_value={})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.window = JarvisWindow()
        self.window._operator_timer.stop()
        self.window.resize(1500, 960)
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)
        self.panel = self.window.orchestra_panel

    def test_universe_planets_navigate_to_real_controls_and_classic_view_still_works(self):
        canvas = self.window.canvas
        self.assertTrue(canvas._universe_mode)
        for key, target in (("research", self.panel), ("voice_channel", self.panel),
                            ("workspace", self.window.personal_workspace), ("mcp", self.window.mcp_scroll)):
            point = canvas._universe.positions()[key]
            QTest.mouseClick(canvas, Qt.LeftButton, pos=point.toPoint())
            self.app.processEvents()
            self.assertIs(self.window.side_tabs.currentWidget(), target)
        self.panel.universe_toggle.setChecked(False)
        self.assertFalse(canvas._universe_mode)
        canvas.grab()  # Both painters must render without Qt exceptions.
        self.panel.universe_toggle.setChecked(True)
        canvas.grab()

    def test_arrow_keys_and_enter_can_activate_a_planet(self):
        canvas = self.window.canvas
        canvas._universe.selected = "interaction"
        QTest.keyClick(canvas, Qt.Key_Right)
        self.assertNotEqual(canvas._universe.selected, "interaction")
        QTest.keyClick(canvas, Qt.Key_Return)
        self.assertIs(self.window.side_tabs.currentWidget(), self.panel)

    def test_no_invented_meteors_and_recorded_events_animate_only_once(self):
        canvas = self.window.canvas
        self.assertEqual(canvas._universe.meteors, [])
        data = {"orchestra": {"events": [{"sequence": 1, "source": "interaction", "target": "research",
            "kind": "dispatch", "created": time.time()}], "runs": [{"role": "research", "state": "running"}]}}
        canvas.apply_orchestra_snapshot(data)
        canvas.apply_orchestra_snapshot(data)
        self.assertEqual(len(canvas._universe.meteors), 1)
        self.assertEqual(canvas._universe.status("research"), "Analyse en cours")
        data["orchestra"]["runs"][0]["state"] = "report_ready"
        canvas.apply_orchestra_snapshot(data)
        self.assertEqual(canvas._universe.status("research"), "Rapport disponible")
        self.assertNotIn("certifi", canvas._universe.status("research"))

    def test_small_window_pages_scroll_without_horizontal_overflow(self):
        self.window.resize(660, 500)
        self.window.side_tabs.setCurrentWidget(self.panel)
        self.app.processEvents()
        for index in range(self.panel.tabs.count()):
            self.panel.tabs.setCurrentIndex(index)
            self.app.processEvents()
            scroll = self.panel.tabs.currentWidget()
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertLessEqual(scroll.width(), self.window.width())
        self.window.canvas.grab()

    def test_refresh_preserves_job_watch_voice_and_chat_drafts(self):
        self.panel.job_title.setText("MON BROUILLON")
        self.panel.watch_enabled.setChecked(True)
        self.panel.voice_fields["contact_id"].setText("CONTACT NON ENREGISTRÉ")
        self.window.chat_input.setText("CHAT NON ENVOYÉ")
        store = self.panel.store
        store.save_config("watch", {**WATCH_DEFAULTS, "model_calls": 3}, expected_revision=0)
        store.save_config("voice", {**VOICE_DEFAULTS, "contact_id": "autre"}, expected_revision=0)
        self.panel.refresh()
        self.assertEqual(self.panel.job_title.text(), "MON BROUILLON")
        self.assertTrue(self.panel.watch_enabled.isChecked())
        self.assertEqual(self.panel.voice_fields["contact_id"].text(), "CONTACT NON ENREGISTRÉ")
        self.assertEqual(self.window.chat_input.text(), "CHAT NON ENVOYÉ")
        self.assertFalse(store.config("watch", WATCH_DEFAULTS)["data"]["enabled"])

    def test_specialist_and_call_requests_use_normal_inbox_without_touching_chat_draft(self):
        self.window.chat_input.setText("Garder ce brouillon")
        self.panel.team_request.setPlainText("Analyse ces priorités sans envoyer quoi que ce soit.")
        with patch.object(self.window._thread, "isRunning", return_value=True), \
             patch.object(self.window._worker, "submit_text", return_value=True) as submit:
            self.panel.delegate_selected()
            self.panel.request_ready.emit("Appelle mon contact propriétaire configuré pour cet essai.")
        self.assertEqual(submit.call_count, 2)
        self.assertIn("session spécialisée", submit.call_args_list[0].args[0])
        self.assertEqual(self.window.chat_input.text(), "Garder ce brouillon")

    def test_architecture_reports_isolated_analysis_only_when_runtime_advertises_it(self):
        from jarvis_agent.architecture_catalog import build_snapshot
        passive = build_snapshot({})
        self.assertFalse(any(a["independent_worker"] for a in passive["agents"]))
        active = build_snapshot({"orchestra": {"independent_contexts": True}})
        report = next(a for a in active["agents"] if a["id"] == "reports")
        self.assertTrue(report["independent_worker"])
        self.assertEqual(report["mode"], "isolated_analysis")
        self.window.architecture_panel.apply_snapshot({"orchestra": {"independent_contexts": True}})
        self.window.architecture_panel.grab()
