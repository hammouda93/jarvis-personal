"""Work-in-progress live mission control: serialized entry and review gates."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from jarvis_agent.assistant_v3 import AssistantWorker, TextTurnInbox
from jarvis_agent.mission_workbench import (
    MissionCommand, MissionControlInbox, perform_mission_command,
)
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.project_roadmap import STAGES, snapshot as roadmap_snapshot
from jarvis_agent.operator_console import OperatorConsole


class FakeModel:
    def __init__(self):
        self.calls = []

    def run(self, user_text, *, log=None, phase=None):
        self.calls.append(("run", user_text))
        return SimpleNamespace(text="acknowledged", actions=(),
                               should_exit=False, end_session=False)

    def run_with_context(self, user_text, context, *, log=None, phase=None):
        self.calls.append(("context", user_text, context))
        return self.run(user_text, log=log, phase=phase)

    def reset(self):
        return None

    def warm_up(self, *, log=None):
        return None


class MissionWorkbenchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.model = FakeModel()
        self.runtime = LiveMissionContinuityRuntime(
            self.model, base_dir=self.base, owner_user_id="test-user",
        )

    def test_queue_rejects_invalid_and_keeps_commands_fifo(self):
        inbox = MissionControlInbox()
        self.assertFalse(inbox.submit("execute", "run powershell"))
        self.assertFalse(inbox.submit("begin", ""))
        self.assertFalse(inbox.submit("resume", "../danger"))
        self.assertFalse(inbox.submit("detach", "ignored"))
        self.assertTrue(inbox.submit("begin", "Analyser le rapport du joueur"))
        self.assertTrue(inbox.submit("detach"))
        self.assertEqual(inbox.pop_nowait(), MissionCommand(
            "begin", "Analyser le rapport du joueur",
        ))
        self.assertEqual(inbox.pop_nowait(), MissionCommand("detach"))
        self.assertIsNone(inbox.pop_nowait())
        self.assertFalse(inbox.pending())

    def test_begin_then_two_turns_same_agent_and_goal_checkpoints(self):
        outcome = perform_mission_command(
            self.runtime, MissionCommand("begin", "Préparer mon rapport"),
        )
        self.assertTrue(outcome["success"])
        mid = outcome["mission_id"]
        self.assertTrue(mid.startswith("live_"))
        self.runtime.run("Préparer mon rapport")
        self.runtime.run("Continue avec le même objectif")
        state = self.runtime.mission_snapshot(mid)
        self.assertEqual(len(self.model.calls), 4)  # 2 context + 2 run, same delegate
        self.assertEqual([a[0] for a in self.model.calls], [
            "context", "run", "context", "run",
        ])
        self.assertIn(mid, self.model.calls[2][2])
        self.assertFalse(state["goal_verified"])
        self.assertEqual(len(state["tasks"]), 2)
        paused = perform_mission_command(
            self.runtime, MissionCommand("detach"),
        )
        self.assertTrue(paused["success"])
        self.assertEqual(paused["mission_id"], mid)
        self.assertIsNone(self.runtime.active_mission_id)
        self.assertFalse(self.runtime.mission_snapshot(mid)["goal_verified"])

        self.runtime = LiveMissionContinuityRuntime(
            self.model, base_dir=self.base, owner_user_id="test-user",
        )
        resumed = perform_mission_command(
            self.runtime, MissionCommand("resume", mid),
        )
        self.assertTrue(resumed["success"])
        self.assertFalse(resumed["manual_review_required"])
        self.runtime.run("Dernière précision")
        final = self.runtime.mission_snapshot(mid)
        self.assertEqual(len(final["tasks"]), 3)
        self.assertFalse(final["goal_verified"])

    def test_never_auto_replays_interrupted_or_uncertain_mission(self):
        start = perform_mission_command(
            self.runtime, MissionCommand("begin", "Envoyer un message"),
        )
        mid = start["mission_id"]
        def fail(text, context, **kwargs):
            self.model.calls.append(("failed", text))
            raise TimeoutError("tool may have sent a message")
        self.model.run_with_context = fail
        with self.assertRaises(TimeoutError):
            self.runtime.run("Envoyer un message")
        original = len(self.model.calls)
        self.runtime.detach_mission()
        reopened = LiveMissionContinuityRuntime(
            self.model, base_dir=self.base, owner_user_id="test-user",
        )
        result = perform_mission_command(
            reopened, MissionCommand("resume", mid),
        )
        self.assertTrue(result["manual_review_required"])
        with self.assertRaises(RuntimeError):
            reopened.run("Réessaie l'envoi")
        self.assertEqual(len(self.model.calls), original)

    def test_convergence_off_cannot_fabricate_mission(self):
        outcome = perform_mission_command(
            object(), MissionCommand("begin", "Bonjour"),
        )
        self.assertFalse(outcome["success"])
        self.assertEqual(outcome["reason"], "convergence_not_enabled")

    def test_worker_queues_without_touching_runtime_on_gui_thread(self):
        with patch.object(AssistantWorker, "__init__",
                          lambda self: QObject.__init__(self)):
            worker = AssistantWorker()
        worker._text_inbox = TextTurnInbox()
        worker._mission_control = MissionControlInbox()
        worker._input_mode = SimpleNamespace(changed=SimpleNamespace(set=lambda: None))
        self.assertTrue(worker.submit_mission_control("begin", "Tester notre mission"))
        self.assertEqual(self.model.calls, [])
        events = []
        worker.mission_control_result.connect(events.append)
        worker.log_line.connect(lambda _: None)
        turns = []
        worker._agent = self.runtime
        worker._process_user_text = lambda text, *, source: turns.append((text, source))
        packet = worker._mission_control.pop_nowait()
        worker._run_mission_control(packet)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["success"])
        self.assertEqual(turns, [("Tester notre mission", "text")])
        self.assertEqual(self.model.calls, [])

    def test_explicit_mode_does_not_consume_unrelated_prior_input(self):
        with patch.object(AssistantWorker, "__init__",
                          lambda self: QObject.__init__(self)):
            worker = AssistantWorker()
        worker._text_inbox = TextTurnInbox()
        worker._mission_control = MissionControlInbox()
        worker._input_mode = SimpleNamespace(changed=SimpleNamespace(set=lambda: None))
        worker._text_inbox.submit("Other pending turn")
        self.assertFalse(worker.submit_mission_control("begin", "New mission"))
        self.assertFalse(worker._mission_control.pending())

    def test_roadmap_is_ordered_and_never_claims_desktop_acceptance(self):
        data = roadmap_snapshot()
        self.assertEqual(data["current"], 7)
        self.assertEqual(data["total"], len(STAGES))
        self.assertEqual([x["number"] for x in data["stages"]],
                         list(range(1, len(STAGES) + 1)))
        self.assertTrue(all(x["windows_real"] == "not_validated"
                            for x in data["stages"]))
        self.assertFalse(data["release_ready"])
        self.assertEqual(data["stages"][7]["implementation"], "planned")


class MissionOperatorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_qt_buttons_only_emit_command_and_roadmap_renders(self):
        ui = OperatorConsole()
        try:
            calls = []
            ui.mission_requested.connect(lambda op, value: calls.append((op, value)))
            ui.apply_snapshot({"mission_enabled": True,
                               "reliability_enabled": False,
                               "actions": [], "missions": [], "events": []})
            self.assertTrue(ui.mission_begin_button.isEnabled())
            ui.mission_goal_input.setText("Créer le rapport")
            ui.mission_begin_button.click()
            self.assertEqual(calls, [("begin", "Créer le rapport")])
            ui.show_mission_result({
                "operation": "begin", "success": True,
                "mission_id": "live_" + "a" * 32,
            })
            self.assertEqual(ui.mission_goal_input.text(), "")
            self.assertIn("7/11", ui.progress_line.text())
            self.assertIn("Windows réel : non validé",
                          ui._views["roadmap"].toPlainText())
            ui.mission_resume_button.click()
            ui.mission_detach_button.click()
            self.assertEqual(calls[-2:], [
                ("resume", "live_" + "a" * 32), ("detach", ""),
            ])
            ui.show_mission_result({
                "operation": "resume", "success": True,
                "mission_id": "live_" + "a" * 32,
                "manual_review_required": True,
            })
            self.assertIn("BLOQUÉE", ui.mission_feedback.text())
        finally:
            ui.close()

    def test_disabled_convergence_keeps_controls_unavailable(self):
        ui = OperatorConsole()
        try:
            ui.apply_snapshot({"mission_enabled": False,
                               "reliability_enabled": False,
                               "actions": [], "missions": [], "events": []})
            self.assertFalse(ui.mission_begin_button.isEnabled())
            self.assertFalse(ui.mission_resume_button.isEnabled())
            self.assertFalse(ui.mission_detach_button.isEnabled())
            self.assertEqual(ui._views["roadmap"].toPlainText().count("Windows réel"), 11)
        finally:
            ui.close()


if __name__ == "__main__":
    unittest.main()
