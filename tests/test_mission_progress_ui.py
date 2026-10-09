"""Runtime state drives UI progress without TTS or fabricated completion."""
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject
from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.mission_workbench import MissionControlInbox
from jarvis_agent.states import AssistantState


class MissionPresentationTests(unittest.TestCase):
    def setUp(self):
        with patch.object(AssistantWorker, "__init__", lambda self: QObject.__init__(self)):
            self.worker = AssistantWorker()
        self.states, self.replies = [], []
        self.worker._state = lambda *args: self.states.append(args)
        self.worker._deliver_reply = self.replies.append

    def test_progress_signal_never_speaks_intermediate_text(self):
        events = []
        self.worker.mission_control_result.connect(events.append)
        self.worker._agent_phase("mission_progress:" + json.dumps({"status": "READY", "goal_verified": False}))
        self.assertEqual(self.replies, [])
        self.assertEqual(self.states[-1][0], AssistantState.ACTING)
        self.assertEqual(events[-1]["operation"], "delegation_progress")

    def test_observation_and_verification_are_actual_runtime_states(self):
        self.worker._agent_phase("observing")
        self.worker._agent_phase("verifying")
        self.assertEqual([s[0] for s in self.states], [AssistantState.OBSERVING, AssistantState.VERIFYING])

    def test_waiting_mission_is_not_presented_as_completed_after_response(self):
        self.worker._agent = SimpleNamespace(active_mission_id="fixture", mission_snapshot=lambda _: {
            "status": "waiting_external", "goal_verified": False})
        self.worker._restore_mission_phase()
        self.assertEqual(self.states[-1][0], AssistantState.PAUSED)

    def test_unknown_outcome_uses_recovery_not_success(self):
        self.worker._agent = SimpleNamespace(active_mission_id="fixture", mission_snapshot=lambda _: {
            "status": "blocked", "manual_review_required": True})
        self.worker._restore_mission_phase()
        self.assertEqual(self.states[-1][0], AssistantState.RECOVERING)

    def test_approval_remains_visible_after_final_voice_reply(self):
        self.worker._agent = SimpleNamespace(active_mission_id="fixture", mission_snapshot=lambda _: {
            "active_supervisor": {"state": "WAITING_APPROVAL"}})
        self.worker._restore_mission_phase()
        self.assertEqual(self.states[-1][0], AssistantState.WAITING_APPROVAL)

    def test_close_stops_coordination_before_another_tool_can_start(self):
        self.worker._stop = threading.Event()
        self.worker._mission_control = MissionControlInbox()
        self.worker._input_mode = SimpleNamespace(changed=threading.Event())
        self.worker.stop()
        self.assertTrue(self.worker._stop.is_set())
        self.assertTrue(self.worker._mission_control.stop_requested.is_set())


if __name__ == "__main__":
    unittest.main()
