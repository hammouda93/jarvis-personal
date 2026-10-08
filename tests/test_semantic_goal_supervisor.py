"""Stage 8 semantic supervisor tests: evidence does not equal tool success."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from jarvis_agent.semantic_goal_supervisor import evaluate_mission
from jarvis_agent.mission_semantics import MissionContract, MissionStep
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.mission_workbench import MissionCommand, MissionControlInbox, perform_mission_command
from jarvis_agent.operator_console import OperatorConsole
from jarvis_agent.operator_telemetry import snapshot as operator_snapshot


class StubBrain:
    def __init__(self):
        self.calls = []
        self.answer = SimpleNamespace(text="Réponse", actions=(),
                                      should_exit=False, end_session=False)

    def run(self, user_text, *, log=None, phase=None):
        self.calls.append(user_text)
        return self.answer

    def run_with_context(self, user_text, context, *, log=None, phase=None):
        return self.run(user_text, log=log, phase=phase)

    def warm_up(self, *, log=None):
        pass

    def reset(self):
        pass


class SemanticSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.brain = StubBrain()
        self.runtime = LiveMissionContinuityRuntime(
            self.brain, base_dir=Path(self.tmp.name), owner_user_id="owner-s8",
        )

    def plan(self):
        return MissionContract(
            source_text="chercher puis préparer",
            objective="document vérifié",
            steps=(
                MissionStep(step_id="find", intent="localiser source",
                            required_evidence=("source_observed",)),
                MissionStep(step_id="draft", intent="préparer document",
                            depends_on=("find",),
                            required_evidence=("draft_readback",)),
            ),
        )

    def test_new_mission_is_never_assumed_complete(self):
        mid = self.runtime.begin_mission("Créer un document")
        report = self.runtime.review_mission(mid)
        self.assertEqual(report["state"], "plan_needed")
        self.assertEqual(report["next_action"], "register_plan")
        self.assertFalse(report["goal_verified"])
        self.assertEqual(self.brain.calls, [])

    def test_plan_requires_dependencies_and_independent_goal_gate(self):
        mid = self.runtime.begin_mission("Créer un document")
        self.runtime.register_semantic_plan(self.plan())
        first = self.runtime.review_mission()
        self.assertEqual(first["state"], "evidence_missing")
        self.assertEqual(first["missing_evidence"],
                         ["source_observed", "draft_readback"])
        steps = {s["id"]: s for s in first["steps"]}
        self.assertEqual(steps["find"]["state"], "waiting_evidence")
        self.assertEqual(steps["draft"]["state"], "waiting_dependency")
        self.assertEqual(steps["draft"]["unmet_dependencies"], ["find"])

        self.runtime.register_goal_evidence(
            "draft_readback", proof_ref="trusted:readback-draft",
        )
        second = self.runtime.review_mission(mid)
        steps2 = {s["id"]: s for s in second["steps"]}
        self.assertEqual(steps2["draft"]["state"], "waiting_dependency")
        self.runtime.register_goal_evidence(
            "source_observed", proof_ref="trusted:observed-source",
        )
        complete_evidence = self.runtime.review_mission(mid)
        self.assertEqual(complete_evidence["state"], "goal_proof_pending")
        self.assertEqual(complete_evidence["next_action"], "independent_goal_review")
        self.assertEqual(complete_evidence["missing_evidence"], [])
        self.assertEqual([x["state"] for x in complete_evidence["steps"]],
                         ["verified", "verified"])
        self.assertFalse(complete_evidence["goal_verified"])
        self.assertEqual(self.brain.calls, [])

        self.runtime.complete_mission(proof_ref="trusted:final-goal-check")
        final = self.runtime.review_mission(mid)
        self.assertEqual(final["state"], "goal_verified")
        self.assertTrue(final["goal_verified"])
        self.assertEqual(self.brain.calls, [])

    def test_tool_success_is_not_mission_proof(self):
        mid = self.runtime.begin_mission("Send a message")
        self.runtime.register_semantic_plan(self.plan())
        self.brain.answer = SimpleNamespace(
            text="Envoyé", actions=(SimpleNamespace(
                name="browser_click", success=True, detail='{"verified":true}'
            ),), should_exit=False, end_session=False)
        self.runtime.run("Envoie")
        review = self.runtime.review_mission(mid)
        self.assertEqual(review["action_count"], 1)
        self.assertEqual(review["state"], "evidence_missing")
        self.assertFalse(review["goal_verified"])
        self.assertEqual(review["recorded_evidence"], [])

    def test_failed_action_manual_review_overrides_missing_evidence(self):
        mid = self.runtime.begin_mission("Changement réel")
        self.runtime.register_semantic_plan(self.plan())
        self.brain.answer = SimpleNamespace(
            text="unknown", actions=(SimpleNamespace(
                name="computer_click", success=False
            ),), should_exit=False, end_session=False)
        self.runtime.run("Essayer")
        report = self.runtime.review_mission(mid)
        self.assertEqual(report["state"], "manual_review")
        self.assertEqual(report["next_action"], "independent_review")
        self.assertGreaterEqual(report["failed_or_uncertain_turns"], 1)
        with self.assertRaises(RuntimeError):
            self.runtime.run("Recommencer")
        self.assertEqual(len(self.brain.calls), 1)

    def test_ambiguity_requires_clarification_before_final_verification(self):
        mid = self.runtime.begin_mission("Qui est le destinataire ?")
        self.runtime.register_semantic_plan(MissionContract(
            source_text="Qui est le destinataire ?",
            objective="envoyer", unresolved=("recipient_missing",),
            steps=(MissionStep(step_id="send", intent="envoyer",
                               required_evidence=("recipient_readback",)),),
        ))
        report = self.runtime.review_mission(mid)
        self.assertEqual(report["state"], "clarification_required")
        self.assertEqual(report["next_action"], "clarify_entities")
        self.assertEqual(report["unresolved"], ["recipient_missing"])

    def test_step_without_evidence_is_not_automatically_verified(self):
        snap = {
            "mission_id": "live_1", "status": "waiting_external",
            "goal_verified": False,
            "semantic_plan": {"unresolved": [], "steps": [
                {"step_id": "a", "intent": "do thing",
                 "depends_on": [], "required_evidence": []},
            ]},
        }
        review = evaluate_mission(snap)
        self.assertEqual(review["state"], "evidence_spec_needed")
        self.assertEqual(review["steps"][0]["state"], "evidence_spec_needed")
        self.assertFalse(review["goal_verified"])

    def test_invalid_persisted_graph_yields_review_not_dispatch(self):
        snapshot = {
            "mission_id": "live_x", "status": "waiting_external",
            "semantic_plan": {"steps": [
                {"step_id": "a", "intent": "A", "depends_on": ["b"],
                 "required_evidence": ["proof1"]},
                {"step_id": "b", "intent": "B", "depends_on": ["a"],
                 "required_evidence": ["proof2"]},
            ]},
        }
        result = evaluate_mission(snapshot)
        self.assertEqual(result["state"], "invalid_plan")
        self.assertTrue(all(s["state"] == "invalid_dependencies"
                            for s in result["steps"]))

    def test_private_owner_cannot_review_foreign_mission(self):
        mid = self.runtime.begin_mission("private mission")
        other = LiveMissionContinuityRuntime(
            StubBrain(), base_dir=Path(self.tmp.name), owner_user_id="other",
        )
        with self.assertRaises(PermissionError):
            other.review_mission(mid)
        self.assertEqual(self.brain.calls, [])

    def test_implicit_conversation_remains_unverified(self):
        self.runtime.run("bonjour")
        # No active mission after implicit single-turn response.
        self.assertIsNone(self.runtime.active_mission_id)
        self.assertEqual(self.brain.calls, ["bonjour"])

    def test_supervisor_review_command_does_not_invoke_brain(self):
        mid = self.runtime.begin_mission("analyser")
        result = perform_mission_command(
            self.runtime, MissionCommand("review", mid),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["review"]["state"], "plan_needed")
        self.assertEqual(self.brain.calls, [])
        q = MissionControlInbox()
        self.assertTrue(q.submit("review", mid))
        self.assertFalse(q.submit("review", "not-a-valid-id"))
        self.assertEqual(q.pop_nowait().operation, "review")

    def test_operator_telemetry_shows_real_plan_without_proof_values(self):
        with patch.dict(os.environ, {
            "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "1",
            "JARVIS_RUNTIME_CONVERGENCE_DIR": self.tmp.name,
            "JARVIS_KERNEL_SHADOW_USER_ID": "owner-s8",
            "JARVIS_HERMES_RELIABILITY_ENABLED": "0",
        }):
            mid = self.runtime.begin_mission("private business goal")
            self.runtime.register_semantic_plan(self.plan())
            self.runtime.register_goal_evidence(
                "source_observed", proof_ref="PRIVATE-PROOF-REFERENCE",
            )
            data = operator_snapshot()
        self.assertEqual(data["missions"][0]["id"], mid)
        sup = data["supervisor"]
        self.assertTrue(sup["plan_registered"])
        self.assertEqual(sup["recorded_evidence"], ["source_observed"])
        self.assertNotIn("PRIVATE-PROOF-REFERENCE", repr(data))
        self.assertFalse(sup["goal_verified"])


class SemanticOperatorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_qt_panel_renders_no_false_proof_and_review_button_is_signal_only(self):
        ui = OperatorConsole()
        try:
            mid = "live_" + "a" * 32
            emitted = []
            ui.mission_requested.connect(lambda op, value: emitted.append((op, value)))
            ui.apply_snapshot({
                "mission_enabled": True, "reliability_enabled": False,
                "missions": [{"id": mid, "status": "waiting_external",
                              "goal": "document", "verified": False}],
                "actions": [], "events": [],
                "supervisor": {
                    "mission_id": mid, "state": "evidence_missing",
                    "next_action": "observe_and_verify",
                    "required_evidence": ["source_matched"],
                    "missing_evidence": ["source_matched"],
                    "recorded_evidence": [],
                    "goal_verified": False,
                    "steps": [{"id": "locate", "intent": "localiser",
                               "state": "waiting_evidence",
                               "waiting_for": ["source_matched"],
                               "unmet_dependencies": []}],
                    "unresolved": [],
                },
            })
            text = ui._views["supervisor"].toPlainText()
            self.assertIn("evidence_missing", text)
            self.assertIn("source_matched", text)
            self.assertIn("ne prouve pas", text)
            ui.mission_id_input.setText(mid)
            ui.mission_review_button.click()
            self.assertEqual(emitted, [("review", mid)])
            ui.show_mission_result({
                "success": True, "operation": "review",
                "mission_id": mid, "review": {
                    "state": "evidence_missing",
                    "missing_evidence": ["source_matched"],
                },
            })
            self.assertIn("Aucun outil exécuté", ui.mission_feedback.text())
        finally:
            ui.close()

    def test_supervisor_html_escapes_untrusted_intent(self):
        ui = OperatorConsole()
        try:
            ui.apply_snapshot({
                "mission_enabled": True, "reliability_enabled": False,
                "missions": [{"id": "live_x", "status": "blocked",
                              "goal": "X", "verified": False}],
                "actions": [], "events": [],
                "supervisor": {
                    "state": "manual_review", "next_action": "independent_review",
                    "recorded_evidence": [], "required_evidence": [],
                    "missing_evidence": [], "steps": [{
                        "id": "a", "intent": "<script>bad</script>",
                        "state": "waiting_evidence",
                        "waiting_for": [], "unmet_dependencies": [],
                    }], "unresolved": [],
                },
            })
            markup = ui._render_cache["supervisor"]
            self.assertIn("&lt;script&gt;", markup)
            self.assertNotIn("<script>", markup)
        finally:
            ui.close()


if __name__ == "__main__":
    unittest.main()
