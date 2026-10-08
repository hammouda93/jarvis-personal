"""Stage 9C same-LLM plan authoring, zero tool exposure or automatic proof."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from jarvis_agent.llm_mission_planner import (
    MissionPlanError, generate_draft, parse_model_plan,
)
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.mission_workbench import MissionCommand, MissionControlInbox, perform_mission_command
from jarvis_agent.operator_console import OperatorConsole


def valid_plan():
    return json.dumps({
        "steps": [
            {
                "id": "find", "intent": "Identifier la source fiable",
                "depends_on": [], "required_evidence": ["source_observed"],
            },
            {
                "id": "check", "intent": "Relire les données enregistrées",
                "depends_on": ["find"], "required_evidence": ["data_readback"],
            },
        ],
        "unresolved": ["Quel destinataire ?"],
    }, ensure_ascii=False)


class FakeProvider:
    provider_name = "cerebras"
    model = "gpt-oss-120b"

    def __init__(self, answer=None):
        self.answer = answer or valid_plan()
        self.requests = []
        self.normal_turns = []

    def _get_client(self):
        owner = self

        class FakeCreate:
            def create(self, **kwargs):
                owner.requests.append(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content=owner.answer
                    ))]
                )

        return SimpleNamespace(chat=SimpleNamespace(
            completions=FakeCreate(),
        ))

    def run(self, text, **kwargs):
        self.normal_turns.append(text)
        raise AssertionError("Planning must not call normal model tool turn")

    def run_with_context(self, text, context, **kwargs):
        self.normal_turns.append(text)
        raise AssertionError("Planning must not call normal model tool turn")

    def warm_up(self, **kwargs):
        pass

    def reset(self):
        pass


class DelegateWrapper:
    def __init__(self, delegate):
        self.delegate = delegate


class StrictPlanParserTests(unittest.TestCase):
    def test_accepted_plan_contains_only_future_proof_requirements(self):
        doc = parse_model_plan(valid_plan(), goal="Étudier les documents")
        self.assertEqual(len(doc.steps), 2)
        self.assertEqual(doc.steps[1].depends_on, ("find",))
        self.assertEqual(doc.completion_requirements(),
                         ("source_observed", "data_readback"))
        self.assertIn("Quel destinataire ?", doc.unresolved)
        self.assertFalse(doc.metadata["authoritative"])
        self.assertFalse(doc.metadata["evidence_registered"])
        self.assertFalse(doc.metadata["automatic_execution"])

    def test_json_fenced_response_is_accepted(self):
        doc = parse_model_plan("```json\n" + valid_plan() + "\n```",
                               goal="Faire un rapport")
        self.assertEqual(len(doc.steps), 2)

    def test_invalid_plans_never_become_executable(self):
        original = json.loads(valid_plan())
        broken = [
            "not json", "", "{}", "[]" ,
            json.dumps({**original, "goal_verified": True}),
            json.dumps({**original, "steps": []}),
            json.dumps({**original, "steps": original["steps"] * 4}),
            json.dumps({**original, "unresolved": ["x"] * 9}),
        ]
        for data in broken:
            with self.subTest(case=str(data)[:50]):
                with self.assertRaises(MissionPlanError):
                    parse_model_plan(data, goal="Objectif")

    def test_unknown_dependency_cycle_and_wrong_proofs_rejected(self):
        base = json.loads(valid_plan())
        modifications = [
            {"id": "find", "intent": "La source", "depends_on": ["check"],
             "required_evidence": ["source_observed"]},
            {"id": "find", "intent": "La source", "depends_on": ["find"],
             "required_evidence": ["source_observed"]},
            {"id": "find", "intent": "La source", "depends_on": [],
             "required_evidence": ["PWN;DELETE"]},
            {"id": "find", "intent": "La source", "depends_on": [],
             "required_evidence": []},
            {"id": "find", "intent": "La source", "depends_on": [],
             "required_evidence": ["source_observed"], "executed": True},
        ]
        for bad in modifications:
            copy = {**base, "steps": [bad, base["steps"][1]]}
            with self.subTest(case=str(bad)[:60]):
                with self.assertRaises(MissionPlanError):
                    parse_model_plan(json.dumps(copy), goal="Contrôler")

    def test_no_plan_if_goal_missing(self):
        with self.assertRaises(ValueError):
            parse_model_plan(valid_plan(), goal="")


class ProviderPlanningTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeProvider()

    def test_same_cerebras_provider_called_once_without_tools(self):
        plan = generate_draft(DelegateWrapper(self.provider), "Créer le rapport")
        self.assertEqual(plan.objective, "Créer le rapport")
        self.assertEqual(len(self.provider.requests), 1)
        data = self.provider.requests[0]
        self.assertEqual(data["model"], self.provider.model)
        self.assertNotIn("tools", data)
        self.assertNotIn("tool_choice", data)
        self.assertNotIn("parallel_tool_calls", data)
        self.assertEqual(self.provider.normal_turns, [])

    def test_invalid_model_text_is_fail_closed_no_retry(self):
        self.provider.answer = "OK je l'ai fait"
        with self.assertRaises(MissionPlanError):
            generate_draft(self.provider, "Mission")
        self.assertEqual(len(self.provider.requests), 1)

    def test_openai_response_no_tool_history_state(self):
        class FakeResponsesProvider:
            provider_name = "openai"
            model = "gpt-test"

            def __init__(self):
                self.params = []
                self._previous_response_id = "existing-conversation"
                self._local_input_history = [{"role": "user", "content": "private"}]

            def _post(self, params):
                self.params.append(params)
                return {"output": [{"content": [
                    {"type": "output_text", "text": valid_plan()}
                ]}]}

        provider = FakeResponsesProvider()
        draft = generate_draft(provider, "Projet indépendant")
        self.assertEqual(len(draft.steps), 2)
        self.assertEqual(provider.params[0]["store"], False)
        self.assertNotIn("tools", provider.params[0])
        self.assertEqual(provider._previous_response_id, "existing-conversation")
        self.assertEqual(len(provider._local_input_history), 1)

    def test_ollama_uses_local_non_tool_json_request(self):
        class OllamaToolAgent:
            model = "local-test"

            def __init__(self):
                self.calls = []

            def _post(self, payload):
                self.calls.append(payload)
                return {"message": {"content": valid_plan()}}

        local = OllamaToolAgent()
        contract = generate_draft(local, "Travailler hors connexion")
        self.assertEqual(len(contract.steps), 2)
        self.assertEqual(local.calls[0]["format"], "json")
        self.assertNotIn("tools", local.calls[0])

    def test_provider_request_exception_does_not_leak_private_reason(self):
        class Broken(FakeProvider):
            def _get_client(self):
                raise RuntimeError("SECRET API KEY and private user content")
        with self.assertRaises(MissionPlanError) as caught:
            generate_draft(Broken(), "PRIVATE mission")
        self.assertEqual(str(caught.exception), "planning_provider_request_failed")
        self.assertNotIn("PRIVATE", str(caught.exception))


class LivePlanAdmissionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.provider = FakeProvider()
        self.runtime = LiveMissionContinuityRuntime(
            self.provider, base_dir=self.base, owner_user_id="plan-owner",
        )

    def test_plan_first_flow_creates_mission_without_model_or_tools(self):
        before = perform_mission_command(
            self.runtime, MissionCommand("begin_only", "Analyser les sources")
        )
        self.assertTrue(before["success"])
        self.assertEqual(before["status"], "mission_created_no_execution")
        mid = before["mission_id"]
        self.assertTrue(mid.startswith("live_"))
        self.assertEqual(self.provider.requests, [])
        self.assertEqual(self.provider.normal_turns, [])
        snap = self.runtime.mission_snapshot(mid)
        self.assertEqual(snap["tasks"], [])
        self.assertFalse(snap["goal_verified"])
        planned = perform_mission_command(
            self.runtime, MissionCommand("auto_plan"),
        )
        self.assertTrue(planned["success"])
        self.assertEqual(len(self.provider.requests), 1)
        self.assertFalse(planned["tool_execution"])
        self.assertEqual(self.runtime.mission_snapshot(mid)["tasks"], [])

    def test_generate_and_register_without_external_execution_or_goal_proof(self):
        mid = self.runtime.begin_mission("Analyser les sources")
        before = self.runtime.mission_snapshot(mid)
        command = perform_mission_command(
            self.runtime, MissionCommand("auto_plan")
        )
        self.assertTrue(command["success"])
        self.assertFalse(command["tool_execution"])
        self.assertFalse(command["goal_verified"])
        self.assertEqual(command["step_count"], 2)
        self.assertEqual(command["model_request_count"], 1)
        after = self.runtime.mission_snapshot(mid)
        self.assertGreater(after["version"], before["version"])
        self.assertEqual(before["tasks"], after["tasks"])
        self.assertEqual(self.provider.normal_turns, [])
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(after["supervisor"]["state"],
                         "clarification_required")
        self.assertFalse(after["goal_verified"])

    def test_existing_plan_does_not_trigger_another_api_request(self):
        self.runtime.begin_mission("Analyser les sources")
        perform_mission_command(self.runtime, MissionCommand("auto_plan"))
        with self.assertRaises(RuntimeError):
            perform_mission_command(self.runtime, MissionCommand("auto_plan"))
        self.assertEqual(len(self.provider.requests), 1)

    def test_no_active_mission_fails_without_call(self):
        with self.assertRaises(RuntimeError):
            perform_mission_command(self.runtime, MissionCommand("auto_plan"))
        self.assertEqual(len(self.provider.requests), 0)

    def test_failed_generated_text_does_not_change_mission_checkpoint(self):
        mid = self.runtime.begin_mission("Contrôler")
        self.provider.answer = "{malformed"
        before = self.runtime.mission_snapshot(mid)
        with self.assertRaises(MissionPlanError):
            perform_mission_command(self.runtime, MissionCommand("auto_plan"))
        after = self.runtime.mission_snapshot(mid)
        self.assertEqual(before["version"], after["version"])
        self.assertIsNone(after["semantic_plan"])
        self.assertFalse(after["goal_verified"])

    def test_queue_rejects_payload_for_explicit_generate(self):
        box = MissionControlInbox()
        self.assertTrue(box.submit("auto_plan"))
        self.assertFalse(box.submit("auto_plan", "auto yes execute"))
        self.assertEqual(box.pop_nowait(), MissionCommand("auto_plan"))


class PlanUIAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_create_without_execution_button_is_only_mission_command(self):
        widget = OperatorConsole()
        try:
            events = []
            widget.mission_requested.connect(lambda op, v: events.append((op, v)))
            widget.apply_snapshot({
                "mission_enabled": True, "reliability_enabled": False,
                "missions": [], "actions": [], "events": [],
            })
            widget.mission_goal_input.setText("Préparer mission de recherche")
            widget.mission_begin_only_button.click()
            self.assertEqual(events, [
                ("begin_only", "Préparer mission de recherche")
            ])
            widget.show_mission_result({
                "success": True, "operation": "begin_only",
                "mission_id": "live_" + "b"*32,
            })
            self.assertIn("SANS action", widget.mission_feedback.text())
        finally:
            widget.close()

    def test_button_requests_only_one_opt_in_plan_and_displays_truth(self):
        widget = OperatorConsole()
        try:
            messages = []
            widget.mission_requested.connect(
                lambda operation, value: messages.append((operation, value))
            )
            widget.apply_snapshot({
                "mission_enabled": True, "reliability_enabled": False,
                "missions": [], "actions": [], "events": [],
            })
            widget.mission_auto_plan_button.click()
            self.assertEqual(messages, [("auto_plan", "")])
            widget.show_mission_result({
                "success": True, "operation": "auto_plan",
                "mission_id": "live_" + "a"*32,
                "step_count": 2, "evidence_count": 2,
                "unresolved_count": 1,
            })
            self.assertIn("2 étape(s)", widget.mission_feedback.text())
            self.assertIn("Aucune action", widget.mission_feedback.text())
            self.assertIn("aucune preuve", widget.mission_feedback.text())
        finally:
            widget.close()

    def test_auto_plan_disabled_without_mission_feature(self):
        widget = OperatorConsole()
        try:
            widget.apply_snapshot({
                "mission_enabled": False, "reliability_enabled": False,
                "missions": [], "actions": [], "events": [],
            })
            self.assertFalse(widget.mission_auto_plan_button.isEnabled())
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
