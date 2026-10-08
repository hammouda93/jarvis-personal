from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_active_mission_supervisor as fixtures
from jarvis_agent.active_mission_supervisor import (
    SupervisorStopped, bind_pending_tool_approval, reserve_model_request, _SCOPE,
)
from jarvis_agent.agent_factory import AgentFactory, AgentLifecycle
from jarvis_agent.controlled_delegation import responsibility_catalog, role_tools
from jarvis_agent.mission_semantics import MissionContract, MissionStep
from jarvis_agent.mission_workbench import MissionCommand, MissionControlInbox, perform_mission_command


class ControlledDelegationTests(unittest.TestCase):
    setUp = fixtures.SupervisorTests.setUp
    approve = fixtures.SupervisorTests.approve
    state = fixtures.SupervisorTests.state

    def plan(self, count=2):
        self.runtime.detach_mission()
        self.mid = self.runtime.begin_mission("work")
        self.runtime.register_semantic_plan(MissionContract(
            source_text="work", objective="work", steps=tuple(
                MissionStep(f"step_{i}", "navigate browser", depends_on=(f"step_{i-1}",) if i else (),
                            required_evidence=(f"proof_{i}",)) for i in range(count)),
        ))
        return {f"proof_{i}": self.rule for i in range(count)}

    def test_all_required_responsibilities_are_declared(self):
        roles = {r["id"] for r in responsibility_catalog()}
        self.assertTrue({"research", "browser", "windows", "documents", "communications",
                         "data", "automations", "mcp", "reports"} <= roles)
        self.assertTrue(all(r["executor"] == "legacy_runtime" for r in responsibility_catalog()))

    def test_actual_factory_wraps_one_cerebras_delegate_and_one_native_registry(self):
        from jarvis_agent import agent_runtime
        from jarvis_agent.hermes_reliability import HermesReliabilityRuntime
        from jarvis_agent.active_mission_supervisor import plan_digest
        from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
        brains = []
        def brain_builder(tools):
            brain = fixtures.Delegate(tools)
            brains.append(brain)
            return brain
        stub_settings = SimpleNamespace(agent_provider="cerebras", structured_tracing_enabled=False,
                                        kernel_shadow_user_id="a")
        with patch.object(agent_runtime, "settings", stub_settings), patch.object(agent_runtime, "NATIVE_TOOLS", self.registry), \
             patch("jarvis_agent.hermes_provider_budget.HermesBudgetedCerebrasAgent", side_effect=brain_builder), \
             patch.dict(os.environ, {
                 "JARVIS_ACTIVE_SUPERVISOR_ENABLED": "1", "JARVIS_MCP_ENABLED": "0",
                 "JARVIS_MEMORY_CORE_ENABLED": "0", "JARVIS_BROWSER_CORE_ENABLED": "0",
                 "JARVIS_COMPUTER_CORE_ENABLED": "0", "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "0",
                 "JARVIS_RUNTIME_CONVERGENCE_DIR": str(Path(self.temp.name) / "factory"),
                 "JARVIS_HERMES_RELIABILITY_DIR": str(Path(self.temp.name) / "ledger"),
             }):
            runtime = agent_runtime.build_agent_runtime()
        self.assertIsInstance(runtime, HermesReliabilityRuntime)
        self.assertIsInstance(runtime.delegate, LiveMissionContinuityRuntime)
        self.assertEqual(len(brains), 1)
        mid = runtime.begin_mission("work")
        runtime.register_semantic_plan(MissionContract(source_text="work", objective="work", steps=tuple(
            MissionStep(f"step_{i}", "navigate browser", depends_on=(f"step_{i-1}",) if i else (),
                        required_evidence=(f"proof_{i}",)) for i in range(3))))
        runtime.approve_supervised_plan(digest=plan_digest(runtime.mission_snapshot(mid)["semantic_plan"]),
            rules={f"proof_{i}": self.rule for i in range(3)},
            assignments={f"step_{i}": {"agent": "browser"} for i in range(3)})
        result = runtime.run_supervised_mission()
        self.assertTrue(result["goal_verified"])
        self.assertEqual(brains[0].calls, 3)
        self.assertEqual(len([c for c in self.registry.calls if c[0] == "open_url"]), 3)
        self.assertTrue(runtime.recent_actions())
        self.assertEqual(len(runtime.recent_actions()), len(self.registry.calls))

    def test_dependency_chain_uses_same_delegate_and_distinct_logical_processes(self):
        rules = self.plan(3)
        self.approve(rules=rules, assignments={
            "step_0": {"agent": "browser"}, "step_1": {"agent": "documents"},
            "step_2": {"agent": "research"},
        })
        progress = []
        result = self.supervisor.run_until_pause(progress=progress.append)
        self.assertTrue(result["goal_verified"])
        self.assertEqual(self.delegate.calls, 3)
        self.assertEqual([r["agent"] for r in result["reports"]], ["browser", "documents", "research"])
        self.assertEqual(len({r["process_id"] for r in result["reports"]}), 3)
        self.assertEqual(len(progress), 3)
        self.assertEqual([t["agent_id"] for t in self.runtime.mission_snapshot(self.mid)["tasks"]],
                         ["browser", "documents", "research"])
        self.assertEqual(self.state().observed_state["active_supervisor"]["usage"]["model_calls"], 3)
        for report in result["reports"]:
            process = self.supervisor.factory.get(report["process_id"])
            self.assertEqual(process.lifecycle, AgentLifecycle.COMPLETED)
            self.assertEqual(process.mission_id, self.mid)
            self.assertEqual(process.result["goal_verified"], False)

    def test_missing_proof_pauses_chain_without_dispatching_dependent_step(self):
        self.plan()
        self.approve()
        report = self.supervisor.run_until_pause()
        self.assertEqual(report["steps_advanced"], 1)
        self.assertEqual(report["state"], "RECOVERING")
        self.supervisor.run_until_pause()
        self.assertEqual(self.delegate.calls, 1)

    def test_bounded_run_can_continue_later_without_repeating_verified_step(self):
        rules = self.plan()
        self.approve(rules=rules)
        first = self.supervisor.run_until_pause(max_steps=1)
        self.assertEqual(first["state"], "READY")
        second = self.supervisor.run_until_pause(max_steps=1)
        self.assertTrue(second["goal_verified"])
        self.assertEqual(self.delegate.calls, 2)

    def test_dependency_is_reobserved_before_second_turn(self):
        self.approve(rules=self.plan())
        self.supervisor.advance()
        self.registry.location = "changed"
        with self.assertRaisesRegex(SupervisorStopped, "dependency_evidence"):
            self.supervisor.advance()
        self.assertEqual(self.delegate.calls, 1)
        self.assertNotIn("proof_0", self.state().observed_state["plan_evidence"])

    def test_stop_before_first_turn_dispatches_nothing_and_is_durable(self):
        self.approve(rules=self.plan())
        stop = threading.Event()
        stop.set()
        with self.assertRaisesRegex(SupervisorStopped, "operator_requested_stop"):
            self.supervisor.run_until_pause(stop_event=stop)
        self.assertEqual(self.delegate.calls, 0)
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(self.supervisor.snapshot()["state"], "BLOCKED")

    def test_stop_between_steps_never_dispatches_next_step(self):
        self.approve(rules=self.plan())
        stop = threading.Event()
        with self.assertRaisesRegex(SupervisorStopped, "operator_requested_stop"):
            self.supervisor.run_until_pause(stop_event=stop, progress=lambda _: stop.set())
        self.assertEqual(self.delegate.calls, 1)

    def test_stop_after_tool_prevents_next_observer_and_never_replays(self):
        self.approve(rules={"destination": self.rule})
        stop = threading.Event()
        original = self.registry.execute
        def execute(name, arguments, **kwargs):
            result = original(name, arguments, **kwargs)
            stop.set()
            return result
        self.registry.execute = execute
        with self.assertRaises(SupervisorStopped):
            self.supervisor.advance(stop_event=stop)
        self.assertEqual(len(self.registry.calls), 1)
        with self.assertRaises(SupervisorStopped):
            self.supervisor.advance()
        self.assertEqual(self.delegate.calls, 1)

    def test_tool_scope_is_filtered_in_both_model_protocols(self):
        self.approve(assignments={"navigate": {"agent": "browser", "tools": ["open_url"]}})
        observed = []
        def turn(text, context, **kwargs):
            reserve_model_request()
            observed.append([t["function"]["name"] for t in self.tools.ollama_tools()])
            observed.append([t["name"] for t in self.tools.openai_tools()])
            return SimpleNamespace(text="no action", actions=())
        self.delegate.run_with_context = turn
        self.supervisor.advance()
        self.assertEqual(observed, [["open_url"], ["open_url"]])

    def test_out_of_scope_call_is_denied_even_if_model_ignores_its_schema(self):
        self.approve(assignments={"navigate": {"agent": "browser"}})
        def turn(text, context, **kwargs):
            self.tools.execute("open_application", {"name": "notepad"}, approved=True)
        self.delegate.run_with_context = turn
        with self.assertRaisesRegex(SupervisorStopped, "scope"):
            self.supervisor.advance()
        self.assertEqual(self.registry.calls, [])

    def test_native_scope_cannot_be_widened_by_explicit_assignment(self):
        with self.assertRaisesRegex(ValueError, "outside_responsibility"):
            self.approve(assignments={"navigate": {"agent": "browser", "tools": ["open_application"]}})
        self.assertEqual(self.registry.calls, [])

    def test_unknown_role_or_step_cannot_be_approved(self):
        for assignments in ({"navigate": {"agent": "arbitrary"}}, {"absent": {"agent": "browser"}}):
            with self.assertRaises(ValueError):
                self.approve(assignments=assignments)
        self.assertEqual(self.delegate.calls, 0)

    def test_remote_tools_require_explicit_scope_not_server_description(self):
        available = frozenset({"open_url", "mcp__service__send"})
        self.assertNotIn("mcp__service__send", role_tools("interaction", available))
        self.assertEqual(role_tools("mcp", available, ["mcp__service__send"]), frozenset({"mcp__service__send"}))
        self.assertEqual(role_tools("mcp", available), frozenset())

    def test_revoked_explicit_tool_fails_closed_before_dispatch(self):
        self.approve(assignments={"navigate": {"agent": "browser", "tools": ["open_url"]}})
        self.registry.ollama_tools = lambda: []
        with self.assertRaisesRegex(ValueError, "currently_available"):
            self.supervisor.advance()
        self.assertEqual(self.delegate.calls, 0)
        self.assertEqual(self.registry.calls, [])

    def test_unavailable_responsibility_is_truthfully_blocked(self):
        self.approve(assignments={"navigate": {"agent": "automations"}})
        with self.assertRaisesRegex(SupervisorStopped, "no_available_tools"):
            self.supervisor.advance()
        self.assertEqual(self.delegate.calls, 0)

    def test_reports_are_structured_from_local_evidence_not_model_prose(self):
        self.approve(rules={"destination": {**self.rule, "equals": "not reached"}})
        report = self.supervisor.advance()["report"]
        self.assertFalse(report["verified"])
        self.assertEqual(report["schema_version"], 1)
        self.assertTrue(report["result"]["turn_returned"])
        self.assertEqual(report["evidence"], [])
        self.assertNotIn("https://example.org", json.dumps(report))
        self.assertNotIn("done", json.dumps(report))

    def test_unknown_outcome_also_persists_a_failure_delegation_report(self):
        self.approve()
        self.registry.unknown = True
        with self.assertRaises(SupervisorStopped):
            self.supervisor.advance()
        report = self.supervisor.snapshot()["reports"][-1]
        self.assertFalse(report["verified"])
        self.assertTrue(report["actions"][0]["outcome_unknown"])
        self.assertIn("action_outcome_requires_review", report["risks"])

    def test_manual_completed_recovery_reobserves_without_redelegating_effect(self):
        self.approve(rules={"destination": self.rule})
        self.registry.unknown = True
        with self.assertRaises(SupervisorStopped):
            self.supervisor.advance()
        usage_before = self.supervisor.snapshot()["usage"]["total_units"]
        self.runtime.resolve_recovery(verified_outcome="completed", proof_ref="operator:independent-screen")
        self.registry.unknown = False
        result = self.supervisor.advance()
        self.assertTrue(result["goal_verified"])
        self.assertEqual(self.delegate.calls, 1)
        self.assertEqual(len([c for c in self.registry.calls if c[0] == "open_url"]), 1)
        usage = self.state().observed_state["active_supervisor"]["usage"]
        self.assertEqual(usage["recoveries"], 1)
        self.assertGreaterEqual(usage["total_units"], usage_before + 1)

    def test_cancellation_is_allowed_when_recovery_budget_is_already_exhausted(self):
        self.approve()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(SupervisorStopped):
            self.supervisor.advance(stop_event=stop)
        state, version = self.runtime.context_store.load(self.mid)
        state.observed_state["active_supervisor"]["usage"]["recoveries"] = 2
        self.runtime.context_store.save(state, expected_version=version)
        result = self.runtime.resolve_recovery(verified_outcome="cancel", proof_ref="operator:cancel")
        self.assertEqual(result.status.value, "failed")
        self.assertEqual(self.delegate.calls, 0)

    def test_completed_mission_never_dispatches_again(self):
        self.approve(rules={"destination": self.rule})
        self.supervisor.advance()
        count = len(self.registry.calls)
        with self.assertRaisesRegex(RuntimeError, "terminal|no_active_mission"):
            self.supervisor.run_until_pause()
        self.assertEqual(len(self.registry.calls), count)

    def test_strict_coordination_bound(self):
        self.approve()
        for limit in (0, 25, True, "2"):
            with self.assertRaises(ValueError):
                self.supervisor.run_until_pause(max_steps=limit)
        self.assertEqual(self.delegate.calls, 0)

    def test_permission_is_bound_to_exact_arguments_and_consumed_once(self):
        self.approve()
        self.supervisor.step = {"step_id": "navigate"}
        self.supervisor.mission_id = self.mid
        token = _SCOPE.set(self.supervisor)
        self.addCleanup(lambda: _SCOPE.reset(token))
        bind_pending_tool_approval("browser_click", {"ref": "observed"})
        with self.assertRaisesRegex(SupervisorStopped, "approval_not_bound"):
            self.tools.execute("browser_click", {"ref": "different"}, approved=True)
        self.assertEqual(self.registry.calls, [])
        self.tools.execute("browser_click", {"ref": "observed"}, approved=True)
        with self.assertRaisesRegex(SupervisorStopped, "approval_not_bound"):
            self.tools.execute("browser_click", {"ref": "observed"}, approved=True)
        self.assertEqual(len(self.registry.calls), 1)

    def test_pending_confirmation_keeps_original_step_even_if_its_criterion_matches(self):
        self.approve(rules=self.plan())
        requests = []
        def turn(text, context, **kwargs):
            reserve_model_request()
            requests.append(self.supervisor.step["step_id"])
            if text != "oui":
                self.tools.requires_confirmation("browser_click")
                bind_pending_tool_approval("browser_click", {"ref": "observed"})
                self.registry.location = "after"
                return SimpleNamespace(text="confirm", actions=())
            result = self.tools.execute("browser_click", {"ref": "observed"}, approved=True)
            return SimpleNamespace(text="done", actions=(result,))
        self.delegate.run_with_context = turn
        self.assertEqual(self.supervisor.advance()["state"], "WAITING_APPROVAL")
        self.assertEqual(self.supervisor.snapshot()["tool"], "browser_click")
        self.assertEqual(self.supervisor.advance("oui")["state"], "READY")
        self.assertEqual(requests, ["step_0", "step_0"])

    def test_permission_for_one_step_cannot_be_used_on_another_step(self):
        self.approve(rules=self.plan())
        self.supervisor.mission_id = self.mid
        self.supervisor.step = {"step_id": "step_0"}
        self.supervisor.bind_approval("browser_click", {"ref": "observed"})
        self.supervisor.step = {"step_id": "step_1"}
        with self.assertRaisesRegex(SupervisorStopped, "approval_not_bound"):
            self.supervisor.consume_approval("browser_click", {"ref": "observed"})

    def test_restart_never_reuses_a_lost_provider_confirmation(self):
        from jarvis_agent.active_mission_supervisor import SupervisorState
        from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
        self.approve()
        self.supervisor.mission_id = self.mid
        self.supervisor.step = {"step_id": "navigate"}
        self.supervisor.bind_approval("browser_click", {"ref": "observed"})
        self.supervisor.transition(SupervisorState.WAITING_APPROVAL, tool="browser_click")
        other = LiveMissionContinuityRuntime(self.delegate, base_dir=Path(self.temp.name), owner_user_id="a")
        state = other.resume_mission(self.mid)
        self.assertEqual(state.status.value, "blocked")
        self.assertNotIn("approval_digest", state.observed_state["active_supervisor"])
        self.assertEqual(self.registry.calls, [])

    def test_factory_builder_cannot_bypass_a_supervised_owner_scope(self):
        process = self.supervisor.factory.spawn(agent_id="browser", mission_id=self.mid)
        with self.assertRaisesRegex(PermissionError, "context_mismatch"):
            self.supervisor.factory.execute(process.process_id, {"mission_id": self.mid, "step_id": "navigate"})
        self.assertEqual(self.delegate.calls, 0)

    def test_approval_is_not_reused_from_an_unsupervised_turn(self):
        self.approve()
        def turn(text, context, **kwargs):
            return self.tools.execute("browser_click", {"ref": "old"}, approved=True)
        self.delegate.run_with_context = turn
        with self.assertRaisesRegex(SupervisorStopped, "approval_not_bound"):
            self.supervisor.advance("oui")
        self.assertEqual(self.registry.calls, [])

    def test_worker_command_runs_chain_and_stop_is_out_of_band(self):
        self.approve(rules=self.plan())
        inbox = MissionControlInbox()
        self.assertTrue(inbox.submit("run_supervision", "1"))
        self.assertFalse(inbox.submit("run_supervision", "1"))
        result = perform_mission_command(self.runtime, inbox.pop_nowait(), stop_event=inbox.stop_requested)
        self.assertEqual(result["steps_advanced"], 1)
        self.assertNotIn("_turn_result", result)
        inbox.finish_coordination()
        self.assertTrue(inbox.submit("run_supervision", "2"))
        self.assertTrue(inbox.submit("cancel_supervision"))
        with self.assertRaisesRegex(SupervisorStopped, "operator_requested_stop"):
            perform_mission_command(self.runtime, inbox.pop_nowait(), stop_event=inbox.stop_requested)
        self.assertEqual(self.delegate.calls, 1)

    def test_inbox_rejects_bad_bounds_and_cancel_payload(self):
        inbox = MissionControlInbox()
        for value in ("0", "25", "-1", "true", "2.0", "9" * 30000):
            self.assertFalse(inbox.submit("run_supervision", value))
        self.assertFalse(inbox.submit("cancel_supervision", "something"))
        self.assertTrue(inbox.submit("cancel_supervision"))
        self.assertIsNone(inbox.pop_nowait())


class FactoryConcurrencyTests(unittest.TestCase):
    def test_running_instance_cannot_execute_or_suspend_again(self):
        started, finish = threading.Event(), threading.Event()
        class Agent:
            def run(self, task):
                started.set()
                if not finish.wait(5):
                    raise TimeoutError()
                return {"done": True}
        factory = AgentFactory()
        factory.register_builder("browser", Agent)
        process = factory.spawn(agent_id="browser", mission_id="mission")
        thread = threading.Thread(target=lambda: factory.execute(process.process_id, {}))
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            with self.assertRaisesRegex(RuntimeError, "already_running"):
                factory.execute(process.process_id, {})
            self.assertFalse(factory.suspend(process.process_id))
            self.assertTrue(factory.terminate(process.process_id))
        finally:
            finish.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(factory.get(process.process_id).lifecycle, AgentLifecycle.TERMINATED)


class ProviderScopeTests(unittest.TestCase):
    def test_v5_ollama_budget_reserves_before_any_request(self):
        from jarvis_agent.memory_semantic_interpreter import ModelSemanticMemoryInterpreter
        interpreter = object.__new__(ModelSemanticMemoryInterpreter)
        interpreter.model = "test"
        scope = SimpleNamespace(reserve=lambda **_: (_ for _ in ()).throw(SupervisorStopped("budget")))
        token = _SCOPE.set(scope)
        try:
            with patch("jarvis_agent.memory_semantic_interpreter.urllib.request.urlopen") as network:
                with self.assertRaisesRegex(SupervisorStopped, "budget"):
                    interpreter._chat_ollama("system", "user")
                network.assert_not_called()
        finally:
            _SCOPE.reset(token)

    def test_v5_fallback_does_not_swallow_supervision_stop(self):
        from jarvis_agent.memory_semantic_interpreter import ModelSemanticMemoryInterpreter
        interpreter = object.__new__(ModelSemanticMemoryInterpreter)
        interpreter.provider = "cerebras"
        with patch.object(interpreter, "_provider_chain", return_value=("cerebras", "groq")), \
             patch.object(interpreter, "_chat_openai_compatible", side_effect=SupervisorStopped("budget")) as chat:
            with self.assertRaises(SupervisorStopped):
                interpreter._chat("system", {})
        self.assertEqual(chat.call_count, 1)

    def test_v5_runtime_preserves_stop_instead_of_falling_back_to_conversation(self):
        from jarvis_agent.semantic_memory_runtime import SemanticMemoryRuntime
        delegate = SimpleNamespace(run=unittest.mock.Mock())
        engine = SimpleNamespace(interpret_turn=unittest.mock.Mock(side_effect=SupervisorStopped("budget")))
        runtime = SemanticMemoryRuntime(delegate, SimpleNamespace(), engine)
        with patch("jarvis_agent.tools.route", return_value=None):
            with self.assertRaises(SupervisorStopped):
                runtime.run("personal memory question")
        delegate.run.assert_not_called()

    def test_provider_hosted_tools_are_not_exposed_in_supervised_scope(self):
        from jarvis_agent.agent_runtime import OpenAIResponsesAgent, settings
        agent = object.__new__(OpenAIResponsesAgent)
        agent.provider_name, agent.web_search_tool_type = "openai", "web_search"
        agent.tools = SimpleNamespace(openai_tools=lambda: [{"type": "function", "name": "local"}])
        scoped_settings = SimpleNamespace(**{**vars(settings), "openai_web_search": True})
        with patch("jarvis_agent.agent_runtime.settings", scoped_settings), patch("jarvis_agent.agent_runtime.CONNECTORS") as connectors:
            connectors.openai_tools.return_value = [{"type": "mcp", "server_label": "hosted"}]
            token = _SCOPE.set(object())
            try:
                self.assertEqual(agent._tool_definitions(), [{"type": "function", "name": "local"}])
            finally:
                _SCOPE.reset(token)
            self.assertEqual(len(agent._tool_definitions()), 3)

    def test_pending_hosted_approval_never_reaches_provider_in_supervised_scope(self):
        from jarvis_agent.agent_runtime import OpenAIResponsesAgent
        agent = object.__new__(OpenAIResponsesAgent)
        agent._previous_response_id = "previous"
        agent._pending_function_approval = None
        agent._pending_mcp_approval = {"id": "remote"}
        with patch.object(agent, "_post") as post:
            token = _SCOPE.set(object())
            try:
                with self.assertRaisesRegex(SupervisorStopped, "separate_review"):
                    agent.run("oui")
            finally:
                _SCOPE.reset(token)
            post.assert_not_called()


class DelegationPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_assignments_and_bounded_run_use_only_worker_signals(self):
        from jarvis_agent.mission_supervisor_panel import MissionSupervisorPanel
        panel = MissionSupervisorPanel()
        self.addCleanup(panel.close)
        requests = []
        panel.requested.connect(lambda op, value: requests.append((op, value)))
        data = {"active_supervisor_enabled": True, "supervised_plan": {
            "mission_id": "a", "digest": "digest", "steps": [{"id": "first", "intent": "read"}], "criteria": ["proof"]}}
        panel.apply_snapshot(data)
        panel.agent_choice.setCurrentIndex(panel.agent_choice.findData("research"))
        panel.tool_scope.setText('["browser_observe_dom"]')
        panel.assign_button.click()
        panel.approve_button.click()
        packet = json.loads(requests[-1][1])
        self.assertEqual(packet["assignments"], {"first": {"agent": "research", "tools": ["browser_observe_dom"]}})
        self.assertEqual(set(packet["limits"]), {"actions", "model_calls", "network_calls", "recoveries", "total_units", "seconds"})
        panel.apply_snapshot({**data, "active_supervisor": {"state": "READY", "reports": [
            {"step_id": "first", "agent": "research", "verified": False, "action_count": 1}]}})
        panel.step_limit.setValue(2)
        panel.run_button.click()
        self.assertEqual(requests[-1], ("run_supervision", "2"))
        panel.stop_button.click()
        self.assertEqual(requests[-1], ("cancel_supervision", ""))
        self.assertEqual(panel.reports.topLevelItem(0).text(2), "Preuve attendue")

    def test_same_plan_in_new_mission_does_not_reuse_prior_operator_assignments(self):
        from jarvis_agent.mission_supervisor_panel import MissionSupervisorPanel
        panel = MissionSupervisorPanel()
        self.addCleanup(panel.close)
        plan = {"mission_id": "a", "digest": "same", "steps": [{"id": "first"}]}
        panel.apply_snapshot({"active_supervisor_enabled": True, "supervised_plan": plan})
        panel.assign_button.click()
        self.assertTrue(panel._assignments)
        panel.apply_snapshot({"active_supervisor_enabled": True, "supervised_plan": {**plan, "mission_id": "b"}})
        self.assertFalse(panel._assignments)
