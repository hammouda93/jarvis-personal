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

    def test_live_turns_never_claim_goal_proven_and_escape_details(self):
        panel = OperatorConsole()
        try:
            panel.update_live_event({
                "source": "direct_fast_path",
                "tools": ["open_application", "<script>secret</script>"],
                "count": 1,
                "success": True,
                "verified": False,
            })
            lines = panel._views["live"].toPlainText()
            self.assertIn("direct_fast_path", lines)
            self.assertIn("open_application", lines)
            self.assertIn("objectif : non vérifié", lines)
            self.assertIn("<script>secret</script>", lines)
            html = panel._render_cache["live"]
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)
            panel.update_live_event({
                "source": "agent_runtime",
                "tools": ["browser_click"],
                "success": False,
            })
            self.assertIn("ÉCHEC / INCOMPLET",
                          panel._views["live"].toPlainText())
        finally:
            panel.close()

    def test_projection_job_counts_are_actual_states_not_indexing_success(self):
        panel = OperatorConsole()
        try:
            panel.apply_snapshot({"memory_stats": {"raw_count": 5,
                "projection_jobs": {"queued": 2, "running": 1, "failed": 1, "done": 1}}})
            self.assertIn("2 en attente", panel.learning_line.text())
            self.assertIn("1 en cours", panel.learning_line.text())
            self.assertIn("1 en erreur", panel.learning_line.text())
            self.assertTrue(panel.learning_line.wordWrap())
        finally:
            panel.close()

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
