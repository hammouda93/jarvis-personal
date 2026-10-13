"""Offline regression for new-document Windows work and mission switching.

No real Windows controls, browser, API, or personal data are touched.
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis_agent.auto_mission import prepare
from jarvis_agent.instruction_clauses import requires_empty_target, requires_new_document
from jarvis_agent.mission_outcome_policy import failure_requires_review
from jarvis_agent import windows_perception as wp


GOAL = (
    "Ouvre Bloc-notes, crée un nouveau document vide sans modifier mes "
    "documents existants, puis écris exactement : "
    "Bonjour Jarvis ! Français : été, cœur. العربية: مرحباً."
)


class NewDocumentRegressionTests(unittest.TestCase):
    def test_goal_distinguishes_new_document_from_new_instance(self):
        self.assertTrue(requires_empty_target(GOAL))
        self.assertTrue(requires_new_document(GOAL))
        self.assertFalse(requires_new_document("Ouvre Bloc-notes et inspecte le texte existant."))
        self.assertFalse(requires_new_document("Ne crée jamais de nouveau document."))
        self.assertTrue(requires_new_document("Open a new document and write a note."))

    def test_ctrln_requires_fresh_observed_window(self):
        with (patch.object(wp, "_SNAPSHOT_ID", ""),
              patch.object(wp, "_SNAPSHOT_WINDOW_TITLE", ""),
              patch.object(wp, "_send_keys") as sender):
            result = wp.press_key("Ctrl+N")
        self.assertFalse(result.success)
        self.assertFalse(json.loads(result.detail)["dispatched"])
        sender.assert_not_called()

    def test_ctrln_uses_verified_existing_work_window(self):
        window = Mock()
        with (patch.object(wp, "_SNAPSHOT_ID", "obs5"),
              patch.object(wp, "_SNAPSHOT_WINDOW_TITLE", "Existing file - Text editor"),
              patch.object(wp, "_window_by_title", return_value=window),
              patch.object(wp, "_send_keys") as sender,
              patch.object(wp, "invalidate_ui_snapshot")):
            result = wp.press_key("Ctrl+N")
        self.assertTrue(result.success, result.message)
        window.set_focus.assert_called_once()
        sender.assert_called_once_with("^n")

    def test_ctrln_missing_observed_window_cannot_target_foreground(self):
        with (patch.object(wp, "_SNAPSHOT_ID", "obs5"),
              patch.object(wp, "_SNAPSHOT_WINDOW_TITLE", "Existing file - Text editor"),
              patch.object(wp, "_window_by_title", return_value=None),
              patch.object(wp, "_send_keys") as sender):
            result = wp.press_key("Ctrl+N")
        self.assertFalse(result.success)
        self.assertEqual(json.loads(result.detail)["guard"], "target_window_missing")
        sender.assert_not_called()

    def test_write_protects_existing_text_without_dispatch_and_is_recoverable(self):
        with (patch.object(wp, "_snapshot_element", return_value=object()),
              patch.object(wp, "_strict_control_value", return_value="Existing important document"),
              patch.object(wp, "_send_keys") as sender):
            outcome = wp.write_ui_element("", GOAL, ref="obs1:e7",
                                          mode="replace", precondition_value="")
        self.assertFalse(outcome.success)
        payload = json.loads(outcome.detail)
        self.assertEqual(payload["precondition_failed"], True)
        self.assertIs(payload["dispatched"], False)
        self.assertIs(payload["outcome_unknown"], False)
        self.assertFalse(failure_requires_review([
            SimpleNamespace(name="write_ui_element", success=False, detail=outcome.detail)
        ]))
        sender.assert_not_called()

    def test_missing_editable_target_is_proven_local_refusal(self):
        with patch.object(wp, "_snapshot_element", return_value=None):
            outcome = wp.write_ui_element("", "Hi", ref="stale:e1",
                                          precondition_value="")
        self.assertFalse(outcome.success)
        data = json.loads(outcome.detail)
        self.assertTrue(data["stale_ref"])
        self.assertFalse(data["dispatched"])
        self.assertFalse(failure_requires_review([
            SimpleNamespace(name="write_ui_element", success=False, detail=outcome.detail)
        ]))

    def test_unknown_external_effect_still_requires_review(self):
        self.assertTrue(failure_requires_review([
            SimpleNamespace(name="write_ui_element", success=False,
                            detail=json.dumps({"dispatched": True, "outcome_unknown": True}))
        ]))
        self.assertTrue(failure_requires_review([
            SimpleNamespace(name="write_ui_element", success=False, detail="")
        ]))

    def test_blocked_goal_does_not_capture_independent_web_mission(self):
        agent = Mock()
        agent.active_mission_id = "live_previous"
        agent.mission_snapshot.return_value = {
            "status": "blocked", "user_goal": GOAL,
            "active_supervisor": {}, "manual_review_required": True,
        }
        agent.detach_mission.side_effect = lambda: setattr(agent, "active_mission_id", None)
        agent.begin_mission.return_value = "live_new"
        events = []
        target = ("Ouvre https://example.com/dashboard/, inspecte le menu "
                  "puis clique sur Joueurs et vérifie l'URL.")
        with patch.dict("os.environ", {"JARVIS_AUTO_MISSION_PLANNING": "0"}):
            result = prepare(agent, target, log=events.append)
        self.assertTrue(result.track)
        agent.detach_mission.assert_called_once_with()
        agent.begin_mission.assert_called_once_with(target)
        self.assertTrue(any("PREVIOUS_DETACHED" in event for event in events))
        agent.generate_semantic_plan.assert_not_called()

    def test_follow_up_or_same_destination_cannot_silently_detach(self):
        agent = Mock()
        agent.active_mission_id = "live_previous"
        agent.mission_snapshot.return_value = {
            "status": "blocked",
            "user_goal": "Ouvre https://example.com, inspecte puis clique.",
            "active_supervisor": {},
        }
        prepare(agent, "Continue la mission.")
        prepare(agent, "Ouvre https://example.com, inspecte puis vérifie.")
        agent.detach_mission.assert_not_called()
        agent.begin_mission.assert_not_called()


if __name__ == "__main__":
    unittest.main()
