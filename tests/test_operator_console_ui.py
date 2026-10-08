"""Headless Qt UI test: actual widgets bind recorded states, no Jarvis worker."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from jarvis_agent.operator_console import OperatorConsole


class OperatorConsoleWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_widgets_project_real_data_and_do_not_start_agent(self):
        panel = OperatorConsole()
        try:
            panel.apply_snapshot({
                "mission_enabled": True,
                "reliability_enabled": True,
                "kernel_shadow_enabled": False,
                "semantic_memory_enabled": True,
                "browser_core_enabled": True,
                "computer_core_enabled": True,
                "learning_enabled": False,
                "unresolved_visible": 1,
                "knowledge_stats": {
                    "skills": 4, "lessons": 7, "app_profiles": 2,
                },
                "memory_stats": {"raw_count": 23},
                "missions": [{
                    "id": "m1", "goal": "préparer rapport joueur",
                    "status": "waiting_external", "verified": False,
                    "needs_review": False,
                }],
                "tasks": [{
                    "name": "turn_00001",
                    "capability": "interaction.live_turn",
                    "status": "completed",
                    "tools": ["browser_observe_dom"],
                }],
                "actions": [{
                    "tool": "browser_click", "id": "act_123",
                    "status": "unknown", "verified": False,
                }],
            })
            panel.update_model({
                "provider": "cerebras", "rounds_used": 3,
                "rounds_limit": 8, "failure_category": "",
            })
            panel.update_phase("acting")
            self.assertIn("ACTION", panel.phase_line.text())
            self.assertIn("Missions observées : 1", panel.mission_line.text())
            self.assertIn("Skills : 4", panel.learning_line.text())
            self.assertIn("Cerebras".lower(), panel.model_line.text().lower())
            self.assertIn("3/8", panel.model_line.text())
            self.assertIn("INCERTAIN", panel._views["actions"].toPlainText())
            self.assertIn("objectif non prouvé", panel._views["missions"].toPlainText())
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
