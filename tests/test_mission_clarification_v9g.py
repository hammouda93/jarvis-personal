"""Live mission clarification: no execution, explicit revision, persistent provenance."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.mission_workbench import MissionCommand, MissionControlInbox, perform_mission_command
from jarvis_agent.operator_console import OperatorConsole
from test_llm_mission_planner import FakeProvider


def resolved_plan():
    return json.dumps({
        "steps": [
            {"id": "find", "intent": "Identifier la source fiable",
             "depends_on": [], "required_evidence": ["source_observed"]},
            {"id": "check", "intent": "Relire les données enregistrées",
             "depends_on": ["find"], "required_evidence": ["data_readback"]},
        ],
        "unresolved": [],
    }, ensure_ascii=False)


class ClarificationRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.provider = FakeProvider()
        self.runtime = LiveMissionContinuityRuntime(
            self.provider, base_dir=self.path, owner_user_id="clarification-owner")
        self.mission_id = self.runtime.begin_mission("Analyser les sources et faire un rapport")
        perform_mission_command(self.runtime, MissionCommand("auto_plan"))

    def test_reply_revises_plan_once_without_tools_or_proof(self):
        self.provider.answer = resolved_plan()
        response = perform_mission_command(
            self.runtime, MissionCommand("clarify_plan", "Utilise les sources publiques"))
        self.assertTrue(response["success"])
        self.assertEqual(response["operation"], "clarify_plan")
        self.assertEqual(response["revision"], 1)
        self.assertEqual(response["unresolved_count"], 0)
        self.assertFalse(response["goal_verified"])
        self.assertFalse(response["tool_execution"])
        self.assertEqual(len(self.provider.requests), 2)
        payload = self.provider.requests[1]
        self.assertNotIn("tools", payload)
        self.assertIn("Utilise les sources publiques", payload["messages"][1]["content"])
        self.assertIn("Quel destinataire", payload["messages"][1]["content"])
        snap = self.runtime.mission_snapshot(self.mission_id)
        self.assertEqual(snap["supervisor"]["state"], "evidence_missing")
        self.assertFalse(snap["goal_verified"])
        self.assertEqual(snap["tasks"], [])
        checkpoint = self.runtime.context_store.load(self.mission_id)[0]
        self.assertEqual(len(checkpoint.expected_state["semantic_plan_history"]), 1)
        self.assertEqual(checkpoint.expected_state["semantic_contract"]["metadata"]["revision"], 1)

    def test_revision_survives_full_runtime_restart(self):
        self.provider.answer = resolved_plan()
        self.runtime.clarify_semantic_plan("Sources publiques")
        self.runtime.detach_mission()
        reopened = LiveMissionContinuityRuntime(
            self.provider, base_dir=self.path, owner_user_id="clarification-owner")
        reopened.resume_mission(self.mission_id)
        state = reopened.mission_snapshot(self.mission_id)
        self.assertEqual(state["supervisor"]["unresolved"], [])
        checkpoint = reopened.context_store.load(self.mission_id)[0]
        self.assertEqual(len(checkpoint.expected_state["semantic_plan_history"]), 1)
        self.assertEqual(checkpoint.expected_state["mission_clarification_answers"][0]["answer"],
                         "Sources publiques")

    def test_no_duplicate_or_invalid_question_is_forwarded(self):
        self.assertFalse(MissionControlInbox().submit("clarify_plan", ""))
        self.assertFalse(MissionControlInbox().submit("clarify_plan", "x" * 2001))
        with self.assertRaises(ValueError):
            self.runtime.clarify_semantic_plan("")
        self.assertEqual(len(self.provider.requests), 1)

    def test_provider_failure_leaves_previous_plan_untouched(self):
        self.provider.answer = "bad JSON"
        before = self.runtime.mission_snapshot(self.mission_id)
        with self.assertRaises(Exception):
            self.runtime.clarify_semantic_plan("Sources publiques")
        after = self.runtime.mission_snapshot(self.mission_id)
        self.assertEqual(after["semantic_plan"], before["semantic_plan"])
        self.assertEqual(after["version"], before["version"])

    def test_refuses_revision_after_any_live_turn(self):
        from types import SimpleNamespace
        self.provider.run_with_context = lambda *args, **kwargs: SimpleNamespace(
            text="Not done", actions=(), should_exit=False, end_session=False)
        self.runtime.run("Première opération")
        with self.assertRaisesRegex(RuntimeError, "execution_or_evidence"):
            self.runtime.clarify_semantic_plan("Sources publiques")
        self.assertEqual(len(self.provider.requests), 1)


class ClarificationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_full_questions_are_visible_and_clarification_only_queues_command(self):
        ui = OperatorConsole()
        try:
            question = "Précisez le périmètre de recherche : " + "très détaillé " * 24
            ui.apply_snapshot({
                "mission_enabled": True, "reliability_enabled": False,
                "missions": [], "actions": [], "events": [],
                "supervisor": {"plan_registered": True, "steps": [
                    {"id": "step_1", "intent": "Ouvrir le navigateur",
                     "state": "waiting_evidence"}],
                    "unresolved": [question], "missing_evidence": []},
            })
            rendered = ui.plan_summary_view.toPlainText()
            self.assertIn("Ouvrir le navigateur", rendered)
            self.assertIn(question.strip(), rendered)
            self.assertFalse(ui.clarification_button.isHidden())
            sent = []
            ui.mission_requested.connect(lambda op, value: sent.append((op, value)))
            ui.clarification_input.setPlainText("Utilise toutes les sources publiques")
            ui.clarification_button.click()
            self.assertEqual(sent, [("clarify_plan", "Utilise toutes les sources publiques")])
            ui.apply_snapshot({
                "mission_enabled": True, "reliability_enabled": False,
                "missions": [], "actions": [], "events": [],
                "supervisor": {"plan_registered": True, "steps": [], "unresolved": []},
            })
            self.assertTrue(ui.clarification_button.isHidden())
        finally:
            ui.close()


if __name__ == "__main__":
    unittest.main()
