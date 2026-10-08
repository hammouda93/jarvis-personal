from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jarvis_agent.active_mission_supervisor import (
    ActiveMissionSupervisor, MissionLimits, SupervisedToolRegistry,
    SupervisorState, SupervisorStopped, matches_rule, plan_digest, reserve_model_request, validate_rule,
)
from jarvis_agent.kernel_contracts import MissionStatus
from jarvis_agent.mission_semantics import MissionContract, MissionStep
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime


def action(name, **detail):
    return SimpleNamespace(name=name, success=True, detail=json.dumps(detail))


class Registry:
    def __init__(self):
        self.calls = []
        self.location = "before"
        self.unknown = False
        self.confirm = False

    def ollama_tools(self):
        return [{"type": "function", "function": {"name": name, "description": name,
                 "parameters": {"type": "object", "properties": {}}}}
                for name in ("open_url", "browser_observe_dom", "browser_click", "open_application")]

    def openai_tools(self):
        return []

    def requires_confirmation(self, name):
        return self.confirm

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, arguments, approved))
        if name == "browser_observe_dom":
            return action(name, snapshot={"url": self.location})
        self.location = "after"
        return action(name, outcome_unknown=self.unknown, verified=True)


class Delegate:
    def __init__(self, tools):
        self.tools = tools
        self.calls = 0

    def run_with_context(self, text, context, **kwargs):
        self.calls += 1
        reserve_model_request()
        if self.tools.requires_confirmation("open_url"):
            return SimpleNamespace(text="approval", actions=())
        result = self.tools.execute("open_url", {"url": "https://example.org"})
        return SimpleNamespace(text="done", actions=(result,))


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = Registry()
        self.tools = SupervisedToolRegistry(self.registry)
        self.delegate = Delegate(self.tools)
        self.runtime = LiveMissionContinuityRuntime(self.delegate, base_dir=Path(self.temp.name), owner_user_id="a")
        self.supervisor = ActiveMissionSupervisor(self.runtime, self.tools)
        self.runtime.active_supervisor = self.supervisor
        self.mid = self.runtime.begin_mission("reach the observed destination")
        self.runtime.register_semantic_plan(MissionContract(
            source_text="goal", objective="goal", steps=(MissionStep("navigate", "navigate", required_evidence=("destination",)),),
        ))
        self.rule = {"tool": "browser_observe_dom", "arguments": {"tab_id": 17},
                     "path": ["snapshot", "url"], "equals": "after"}

    def approve(self, **kwargs):
        plan = self.runtime.mission_snapshot(self.mid)["semantic_plan"]
        self.supervisor.approve(digest=plan_digest(plan), **kwargs)

    def state(self):
        return self.runtime.context_store.load(self.mid)[0]

    def test_unapproved_plan_never_dispatches(self):
        with self.assertRaisesRegex(RuntimeError, "reviewed_plan"):
            self.supervisor.advance()
        self.assertEqual(self.delegate.calls, 0)

    def test_changed_plan_digest_is_not_approved(self):
        with self.assertRaisesRegex(ValueError, "changed"):
            self.supervisor.approve(digest="stale")
        self.assertEqual(self.registry.calls, [])

    def test_independent_observation_completes_goal_and_persists_budget(self):
        self.approve(rules={"destination": self.rule})
        report = self.supervisor.advance()
        self.assertEqual(report["state"], "COMPLETED")
        self.assertTrue(self.state().observed_state["goal_verified"])
        self.assertEqual(self.delegate.calls, 1)
        self.assertEqual([c[0] for c in self.registry.calls], ["open_url", "browser_observe_dom", "browser_observe_dom", "browser_observe_dom"])
        self.assertEqual(self.state().observed_state["active_supervisor"]["usage"]["actions"], 4)
        self.assertEqual(self.state().observed_state["active_supervisor"]["usage"]["model_calls"], 1)

    def test_tool_success_without_rule_waits_for_proof_and_never_replays(self):
        self.approve()
        report = self.supervisor.advance()
        self.assertEqual(report["state"], "RECOVERING")
        for _ in range(3):
            self.assertFalse(self.supervisor.advance()["goal_verified"])
        self.assertEqual(self.delegate.calls, 1)
        self.assertEqual(len(self.registry.calls), 1)

    def test_model_budget_stops_before_second_request_and_survives_restart(self):
        self.approve(limits=MissionLimits(model_calls=1))
        self.supervisor.advance()
        self.runtime.register_goal_evidence("destination", proof_ref="trusted")
        # A second provider request in the same scope is never issued.
        with patch("jarvis_agent.active_mission_supervisor._SCOPE") as scope:
            scope.get.return_value = self.supervisor
            with self.assertRaisesRegex(SupervisorStopped, "model_calls"):
                reserve_model_request()
        self.assertEqual(self.state().observed_state["active_supervisor"]["usage"]["model_calls"], 1)

    def test_unknown_outcome_blocks_without_new_attempt(self):
        self.registry.unknown = True
        self.approve(rules={"destination": self.rule})
        with self.assertRaises(SupervisorStopped):
            self.supervisor.advance()
        with self.assertRaises(SupervisorStopped):
            self.supervisor.advance()
        self.assertEqual(self.delegate.calls, 1)
        self.assertEqual(len(self.registry.calls), 1)
        self.assertEqual(self.state().status, MissionStatus.BLOCKED)
        self.assertEqual(self.state().observed_state["plan_evidence"], {})

    def test_action_budget_stops_before_observation_and_does_not_replay(self):
        self.approve(rules={"destination": self.rule}, limits=MissionLimits(actions=1))
        with self.assertRaisesRegex(SupervisorStopped, "actions"):
            self.supervisor.advance()
        self.assertEqual(len(self.registry.calls), 1)
        self.assertEqual(self.delegate.calls, 1)

    def test_expired_time_budget_dispatches_nothing(self):
        self.approve(limits=MissionLimits(seconds=1))
        with patch("jarvis_agent.active_mission_supervisor.time.time", return_value=10**12):
            with self.assertRaisesRegex(SupervisorStopped, "time_budget"):
                self.supervisor.advance()
        self.assertEqual(self.delegate.calls, 0)

    def test_permissions_are_not_granted_by_plan_approval(self):
        self.registry.confirm = True
        self.approve()
        self.assertEqual(self.supervisor.advance()["state"], "WAITING_APPROVAL")
        self.assertEqual(self.registry.calls, [])

    def test_owner_mismatch_cannot_approve(self):
        self.runtime.owner_user_id = "b"
        with self.assertRaises(PermissionError):
            self.supervisor.snapshot()

    def test_mutating_tool_cannot_be_observer(self):
        with self.assertRaisesRegex(ValueError, "read_only"):
            validate_rule({**self.rule, "tool": "browser_click"})

    def test_tool_verified_flag_is_not_goal_predicate(self):
        with self.assertRaisesRegex(ValueError, "goal_evidence"):
            validate_rule({**self.rule, "path": ["verified"], "equals": True})

    def test_json_boolean_does_not_equal_integer(self):
        self.assertFalse(matches_rule(action("observe", snapshot={"url": True}), {**self.rule, "equals": 1}))

    def test_failed_or_missing_observation_does_not_prove_criterion(self):
        missing = action("observe", verified=True)
        self.assertFalse(matches_rule(missing, self.rule))
        failed = SimpleNamespace(success=False, detail=json.dumps({"snapshot": {"url": "after"}}))
        self.assertFalse(matches_rule(failed, self.rule))

    def test_interrupted_supervisor_claim_needs_manual_review(self):
        self.approve()
        self.supervisor.transition(SupervisorState.ACTING)
        other = LiveMissionContinuityRuntime(self.delegate, base_dir=Path(self.temp.name), owner_user_id="a")
        recovered = other.resume_mission(self.mid)
        self.assertEqual(recovered.status, MissionStatus.BLOCKED)
        self.assertEqual(recovered.observed_state["active_supervisor"]["state"], "BLOCKED")

    def test_unsupervised_registry_preserves_result_and_permissions(self):
        self.assertFalse(self.tools.requires_confirmation("anything"))
        result = self.tools.execute("browser_observe_dom", {}, approved=True)
        self.assertTrue(result.success)
        self.assertTrue(self.registry.calls[0][2])

    def test_final_changed_observation_revokes_interim_evidence_without_replay(self):
        original = self.registry.execute
        observations = 0
        def execute(name, arguments, *, approved=False):
            nonlocal observations
            if name == "browser_observe_dom":
                observations += 1
                if observations >= 3:
                    self.registry.location = "changed"
            return original(name, arguments, approved=approved)
        self.registry.execute = execute
        self.approve(rules={"destination": self.rule})
        result = self.supervisor.advance()
        self.assertFalse(result["goal_verified"])
        self.assertNotIn("destination", self.state().observed_state["plan_evidence"])
        self.supervisor.advance()
        self.assertEqual(self.delegate.calls, 1)

    def test_sensitive_action_is_denied_before_the_delegate_when_unapproved(self):
        self.approve()
        with patch("jarvis_agent.active_mission_supervisor._SCOPE") as scope:
            scope.get.return_value = self.supervisor
            result = self.tools.execute("browser_click", {"ref": "observed"})
        self.assertFalse(result.success)
        self.assertTrue(json.loads(result.detail)["approval_required"])
        self.assertEqual(self.registry.calls, [])


class SupervisorPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_plan_approval_and_advance_are_only_worker_commands(self):
        from jarvis_agent.mission_supervisor_panel import MissionSupervisorPanel
        panel = MissionSupervisorPanel()
        self.addCleanup(panel.close)
        requests = []
        panel.requested.connect(lambda op, data: requests.append((op, data)))
        panel.apply_snapshot({"active_supervisor_enabled": True,
            "supervised_plan": {"mission_id": "live_a", "digest": "digest", "criteria": ["destination"]}})
        panel.expected.setText('"https://example.org/"')
        panel.add_rule.click()
        panel.approve_button.click()
        self.assertEqual(requests[0][0], "approve_supervision")
        packet = json.loads(requests[0][1])
        self.assertEqual(packet["mission_id"], "live_a")
        self.assertEqual(packet["rules"]["destination"]["equals"], "https://example.org/")
        panel.apply_snapshot({"active_supervisor_enabled": True,
            "active_supervisor": {"state": "WAITING_APPROVAL", "step_id": "navigate", "tool": "browser_click", "usage": {"actions": 3}}})
        panel.continuation.setText("oui")
        panel.advance_button.click()
        self.assertEqual(requests[-1], ("advance_supervision", "oui"))
        self.assertIn("browser_click", panel.state_label.text())

    def test_disabled_or_blocked_supervision_cannot_emit_actions(self):
        from jarvis_agent.mission_supervisor_panel import MissionSupervisorPanel
        panel = MissionSupervisorPanel()
        self.addCleanup(panel.close)
        requests = []
        panel.requested.connect(lambda *args: requests.append(args))
        for state in ("BLOCKED", "FAILED", "COMPLETED"):
            panel.apply_snapshot({"active_supervisor_enabled": True, "active_supervisor": {"state": state}})
            panel.advance_button.click()
        self.assertEqual(requests, [])
