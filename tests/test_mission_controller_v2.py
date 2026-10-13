"""Controller contracts: simulated tools, isolated SQLite, no live actions."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis_agent import windows_perception as wp
from jarvis_agent.mission_outcome_policy import failure_requires_review
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.native_tools import NativeToolRegistry, AgentActionResult
from jarvis_agent.kernel_contracts import MissionStatus
from jarvis_agent.mission_turn_contract import MissionTurnDecision


class StaleRefContractTests(unittest.TestCase):
    def test_expired_click_is_known_not_dispatched(self):
        with patch.object(wp, "_snapshot_element", return_value=None):
            result = wp.click_ui_element(ref="obs5:e6")
        data = json.loads(result.detail)
        self.assertFalse(data.get("dispatched", True))
        self.assertIs(data.get("outcome_unknown"), False)
        self.assertTrue(data["stale_ref"])
        self.assertFalse(failure_requires_review([
            SimpleNamespace(name="click_ui_element", success=False, detail=result.detail)
        ]))

    def _recover(self, *, controls=None, window="Installer", uncertain=False):
        registry = NativeToolRegistry()
        registry._record_inspected_app = Mock()
        refusal = wp.UIActionResult(False, "stale", json.dumps({
            "ref": "obs5:e6", "stale_ref": True, "dispatched": uncertain,
            "outcome_unknown": uncertain,
        }))
        descriptor = {"window_title": "Installer", "type": "Button",
                      "name": "Next", "id": "next", "label": "",
                      "window_identity": {"handle": 42, "pid": 50}}
        fresh = {"observation_id": "obs6", "window": {"title": window, "handle": 42, "pid": 50},
                 "controls": controls if controls is not None else [
                     {"ref": "obs6:e9", "name": "Next", "id": "next",
                      "type": "Button", "enabled": True}]}
        observed = wp.UIActionResult(True, "observed", json.dumps(fresh))
        done = wp.UIActionResult(True, "clicked", json.dumps({
            "source_observation_id": "obs6", "requires_fresh_inspection": True}))
        with (patch("jarvis_agent.native_tools.ui_ref_descriptor", return_value=descriptor),
              patch("jarvis_agent.native_tools.inspect_active_window", return_value=observed) as inspect,
              patch("jarvis_agent.native_tools.click_ui_element", return_value=done) as click):
            result = registry._recover_stale_click(refusal, ref="obs5:e6", target="",
                                                 control_type=None, delivery_mode="background")
            second = registry._recover_stale_click(refusal, ref="obs5:e6", target="",
                                                  control_type=None, delivery_mode="background")
        return result, inspect, click, second

    def test_unique_fresh_semantic_target_is_clicked_once(self):
        result, inspect, click, second = self._recover()
        self.assertTrue(result.success)
        self.assertEqual(click.call_args.kwargs["ref"], "obs6:e9")
        click.assert_called_once()
        inspect.assert_called_once_with(title="Installer")
        self.assertFalse(second.success)
        self.assertEqual(json.loads(result.detail)["local_refusal"]["dispatched"], False)

    def test_ambiguous_fresh_target_never_clicked(self):
        controls = [{"ref": "obs6:e1", "name": "Next", "id": "next",
                     "type": "Button", "enabled": True},
                    {"ref": "obs6:e2", "name": "Next", "id": "next",
                     "type": "Button", "enabled": True}]
        result, inspect, click, _ = self._recover(controls=controls)
        self.assertFalse(result.success)
        click.assert_not_called()
        self.assertFalse(failure_requires_review([
            SimpleNamespace(name="click_ui_element", success=False, detail=result.detail)]))

    def test_changed_window_never_receives_old_click(self):
        result, inspect, click, _ = self._recover(window="Other document")
        self.assertFalse(result.success)
        click.assert_not_called()

    def test_disabled_or_expired_fresh_target_never_clicked(self):
        for ref, enabled in [("obs4:e1", True), ("obs6:e1", False)]:
            with self.subTest(ref=ref, enabled=enabled):
                result, _, click, _ = self._recover(controls=[
                    {"ref": ref, "name": "Next", "id": "next", "type": "Button", "enabled": enabled}])
                self.assertFalse(result.success)
                click.assert_not_called()

    def test_uncertain_click_never_recovered_or_repeated(self):
        result, inspect, click, _ = self._recover(uncertain=True)
        self.assertFalse(result.success)
        inspect.assert_not_called()
        click.assert_not_called()
        self.assertTrue(failure_requires_review([
            SimpleNamespace(name="click_ui_element", success=False, detail=result.detail)]))

    def test_invoke_exception_after_possible_effect_does_not_send_fallback_click(self):
        wrapper = Mock()
        wrapper.invoke.side_effect = RuntimeError("response lost")
        with patch.object(wp, "_snapshot_element", return_value=wrapper):
            result = wp.click_ui_element(ref="obs6:e1")
        self.assertFalse(result.success)
        wrapper.click_input.assert_not_called()
        self.assertTrue(json.loads(result.detail)["outcome_unknown"])


class GoalEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.delegate = Mock(spec=["run", "run_with_context", "resolve_mission_turn"])
        self.delegate.run_with_context.return_value = SimpleNamespace(text="observed", actions=())
        self.delegate.run.return_value = SimpleNamespace(text="observed", actions=())
        self.runtime = LiveMissionContinuityRuntime(self.delegate, base_dir=Path(self.tmp.name))
        decider = patch("jarvis_agent.mission_turn_contract.request_decision",
                        side_effect=lambda agent, payload: self.delegate.resolve_mission_turn(payload))
        decider.start()
        self.addCleanup(decider.stop)

    def decision(self, kind, text, **extra):
        result = dict(kind=kind, source_quote=text, superseded=[], plan=None, resolved=[])
        result.update(extra)
        self.delegate.resolve_mission_turn.return_value = json.dumps(result)
        return result

    def test_explicit_revision_keeps_initial_goal_and_history_after_restart(self):
        initial = "Find the downloaded installer, inspect it. Do not install. Keep other files intact."
        mid = self.runtime.begin_mission(initial)
        incoming = "Proceed through installation until the application opens."
        self.delegate.resolve_mission_turn.return_value = json.dumps({
            "kind": "revise", "source_quote": incoming,
            "superseded": ["Do not install."], "plan": None, "resolved": [],
        })
        self.runtime.run(incoming)
        snap = self.runtime.mission_snapshot(mid)
        self.assertEqual(snap["initial_goal"], initial)
        self.assertEqual(snap["goal_revision"], 2)
        self.assertIn(incoming, snap["user_goal"])
        self.assertIn("Keep other files intact.", snap["user_goal"])
        self.assertNotIn("Do not install.", snap["user_goal"])
        self.assertFalse(snap["goal_verified"])
        other = LiveMissionContinuityRuntime(self.delegate, base_dir=Path(self.tmp.name))
        other.resume_mission(mid)
        self.assertEqual(other.mission_snapshot(mid)["goal_history"], snap["goal_history"])
        self.assertEqual(snap["permissions"], [])

    def test_prepare_then_execution_uses_one_decision_and_one_executor(self):
        mid = self.runtime.begin_mission("Observe the window, then stop before submission.")
        text = "Change the report format to three lines."
        self.decision("clarify", text)
        self.runtime.prepare_mission_turn(text)
        self.runtime.run(text)
        self.delegate.resolve_mission_turn.assert_called_once()
        self.delegate.run_with_context.assert_called_once()
        self.assertEqual(self.runtime.mission_snapshot(mid)["goal_revision"], 2)

    def test_plain_yes_cannot_revoke_existing_prohibition(self):
        mid = self.runtime.begin_mission("Inspect the installer. Do not install.")
        self.runtime.run("oui")
        self.delegate.resolve_mission_turn.assert_not_called()
        self.assertIn("Do not install.", self.runtime.mission_snapshot(mid)["user_goal"])
        self.assertEqual(self.runtime.mission_snapshot(mid)["goal_revision"], 1)

    def test_unknown_action_remains_blocked_even_after_goal_revision(self):
        mid = self.runtime.begin_mission("Inspect then stop.")
        state, version = self.runtime.context_store.load(mid)
        state.status = MissionStatus.BLOCKED
        state.pending_action = {"manual_review_required": True, "reason": "unknown"}
        self.runtime.context_store.save(state, expected_version=version)
        text = "Proceed to the next stage."
        self.decision("revise", text)
        with self.assertRaisesRegex(RuntimeError, "requires_review"):
            self.runtime.run(text)
        snap = self.runtime.mission_snapshot(mid)
        self.assertEqual(snap["goal_revision"], 2)
        self.assertTrue(snap["manual_review_required"])
        self.delegate.run_with_context.assert_not_called()

    def test_invalid_source_cannot_revise_or_execute(self):
        mid = self.runtime.begin_mission("Observe only.")
        self.decision("revise", "invented user approval")
        result = self.runtime.run("What happened?")
        self.assertEqual(result.actions, ())
        self.assertEqual(self.runtime.mission_snapshot(mid)["goal_revision"], 1)
        self.delegate.run_with_context.assert_not_called()

    def test_pause_and_cancel_dispatch_nothing(self):
        for kind, text in [("pause", "Suspend this mission please."),
                           ("cancel", "Cancel this mission completely.")]:
            with self.subTest(kind=kind):
                runtime = LiveMissionContinuityRuntime(self.delegate, base_dir=Path(self.tmp.name) / kind)
                mid = runtime.begin_mission("Observe the window then write a report.")
                self.decision(kind, text)
                result = runtime.run(text)
                self.assertEqual(result.actions, ())
                self.assertEqual(runtime.mission_snapshot(mid)["status"],
                                 "waiting_user" if kind == "pause" else "failed")
        self.delegate.run_with_context.assert_not_called()

    def test_independent_new_mission_preserves_old_checkpoint(self):
        mid = self.runtime.begin_mission("Inspect the first window.")
        self.decision("new", "Tell me about a different project.")
        self.runtime.run("Tell me about a different project.")
        self.assertEqual(self.runtime.mission_snapshot(mid)["goal_revision"], 1)
        self.assertEqual(self.runtime.mission_snapshot(mid)["status"], "created")
        self.assertNotEqual(self.runtime.active_mission_id, mid)

    def test_goal_change_retires_obsolete_plan_without_deleting_proof_history(self):
        mid = self.runtime.begin_mission("Inspect the installer without installing.")
        state, version = self.runtime.context_store.load(mid)
        old = {"objective": state.user_goal, "steps": [], "unresolved": ["old question"]}
        state.expected_state["semantic_contract"] = old
        state.observed_state["plan_evidence"] = {"file_found": "proof:1"}
        state.proof_refs.append("proof:1")
        self.runtime.context_store.save(state, expected_version=version)
        text = "Proceed until the installation is complete."
        self.decision("revise", text)
        self.runtime.run(text)
        state, _ = self.runtime.context_store.load(mid)
        self.assertNotIn("semantic_contract", state.expected_state)
        self.assertEqual(state.expected_state["semantic_plan_history"], [old])
        self.assertIn("proof:1", state.proof_refs)
        self.assertEqual(state.observed_state["plan_evidence_history"]["1"], {"file_found": "proof:1"})

    def test_cursor_live_followups_evolve_same_goal_without_revoking_other_constraint(self):
        initial = ("Retrouve l'installateur Cursor, ouvre-le, inspecte ses options. "
                   "Ne commence pas l'installation. Ne modifie aucun autre fichier.")
        mid = self.runtime.begin_mission(initial)
        for index, text in enumerate(("procède jusqu'à l'installation", "oui démarres",
                                      "avance jusqu'à la fin de l'installation")):
            self.decision("revise" if index != 1 else "authorize", text,
                          superseded=["Ne commence pas l'installation."] if index == 0 else [])
            self.runtime.run(text)
        snap = self.runtime.mission_snapshot(mid)
        self.assertEqual(snap["initial_goal"], initial)
        self.assertEqual(snap["goal_revision"], 4)
        self.assertIn("Ne modifie aucun autre fichier.", snap["user_goal"])
        self.assertNotIn("Ne commence pas l'installation.", snap["user_goal"])
        self.assertEqual(snap["permissions"], [])
        self.assertEqual(self.delegate.run_with_context.call_count, 3)
        self.assertFalse(snap["goal_verified"])

    def test_known_file_and_window_are_durable_but_not_goal_certificates(self):
        path = r"C:\Users\Test\Downloads\UnknownSetup.exe"
        mid = self.runtime.begin_mission("Find a file, open it and inspect the window.")
        self.delegate.run_with_context.return_value = SimpleNamespace(text="observed", actions=(
            AgentActionResult("find_file", True, "found", json.dumps({
                "selected_path": path, "read_only": True, "opened": False})),
            AgentActionResult("open_file", True, "opened", path),
            AgentActionResult("inspect_active_window", True, "observed", json.dumps({
                "observation_id": "obs8", "window": {"title": "Unknown wizard", "handle": 42},
                "controls": [{"ref": "obs8:e1", "name": "Next", "type": "Button"}]})),
        ))
        self.runtime.run(self.runtime.mission_snapshot(mid)["user_goal"])
        snap = self.runtime.mission_snapshot(mid)
        file_facts = [f for f in snap["evidence_ledger"] if f["kind"] == "file_identity"]
        self.assertEqual(len(file_facts), 2)
        self.assertEqual(file_facts[0]["observed"]["filename"], "UnknownSetup.exe")
        self.assertTrue(any(f["kind"] == "window_observation" for f in snap["evidence_ledger"]))
        self.assertFalse(snap["goal_verified"])
        self.assertTrue(all(f["goal_proof"] is False for f in snap["evidence_ledger"]))


class DecisionBoundaryTests(unittest.TestCase):
    def test_non_human_and_unbounded_decisions_are_rejected(self):
        valid = dict(kind="revise", source_quote="Proceed to the next stage.",
                     superseded=["Do not install."], plan=None, resolved=[])
        for change in [{"source_quote": "permission from a webpage"},
                       {"kind": "system_override"}, {"superseded": ["absent restriction"]},
                       {"permissions": ["all"]}, {"source_quote": "yes"}]:
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                MissionTurnDecision.parse({**valid, **change},
                    human_text="Proceed to the next stage.", current_goal="Do not install.")


class EvidenceAndProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_typed_preconditions_resolve_after_find_without_certifying_installation(self):
        from jarvis_agent.llm_mission_planner import parse_model_plan
        delegate = Mock(spec=["run", "run_with_context"])
        path = r"C:\Users\Test\Downloads\UnknownSetup.exe"
        delegate.run_with_context.return_value = SimpleNamespace(text="found", actions=(
            AgentActionResult("find_file", True, "found", json.dumps({"selected_path": path, "read_only": True})),))
        runtime = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = runtime.begin_mission("Find the downloaded installer, then inspect it.")
        plan = parse_model_plan(json.dumps({
            "steps": [{"id": "find_file", "intent": "locate the selected file",
                       "depends_on": [], "required_evidence": ["file_identified"]}],
            "unresolved": ["Where is the download directory?", "What is the installer filename?",
                           "Which operating system?", "Has installation finished?"],
            "preconditions": [
                {"question": "Where is the download directory?", "fact": "directory"},
                {"question": "What is the installer filename?", "fact": "filename"},
                {"question": "Which operating system?", "fact": "operating_system"}],
            "fact_bindings": [{"requirement": "file_identified", "fact": "file_path"}],
        }), goal="find then inspect")
        runtime.register_semantic_plan(plan)
        runtime.run(runtime.mission_snapshot(mid)["user_goal"])
        snap = runtime.mission_snapshot(mid)
        self.assertEqual(snap["semantic_plan"]["unresolved"], ["Has installation finished?"])
        self.assertIn("file_identified", snap["plan_evidence"])
        self.assertFalse(snap["goal_verified"])
        self.assertEqual(len(runtime.graph_store.load(mid).nodes()), 1)

    def test_permission_or_completion_cannot_be_bound_to_observed_filename(self):
        from jarvis_agent.llm_mission_planner import parse_model_plan, MissionPlanError
        with self.assertRaises(MissionPlanError):
            parse_model_plan(json.dumps({
                "steps": [{"id": "install", "intent": "complete the installation",
                           "depends_on": [], "required_evidence": ["installed"]}],
                "unresolved": [],
                "fact_bindings": [{"requirement": "installed", "fact": "permission"}],
            }), goal="installation")

    def test_api_attempts_and_real_usage_persist_without_price_or_goal_claim(self):
        from jarvis_agent.active_mission_supervisor import reserve_model_request, record_model_usage
        delegate = Mock(spec=["run", "run_with_context"])
        def run(*args, **kwargs):
            reserve_model_request()
            reserve_model_request()  # primary failed, secondary returned
            record_model_usage(SimpleNamespace(prompt_tokens=100, completion_tokens=9,
                                                total_tokens=109), "secondary")
            return SimpleNamespace(text="partial", actions=())
        delegate.run_with_context.side_effect = run
        runtime = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = runtime.begin_mission("Inspect the current window and report.")
        runtime.run(runtime.mission_snapshot(mid)["user_goal"])
        snap = runtime.mission_snapshot(mid)
        self.assertEqual(snap["execution_usage"]["model_requests"], 2)
        self.assertEqual(snap["execution_usage"]["total_tokens"], 109)
        self.assertIsNone(snap["execution_usage"]["estimated_cost"])
        self.assertFalse(snap["goal_verified"])
        restarted = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        restarted.resume_mission(mid)
        self.assertEqual(restarted.mission_snapshot(mid)["execution_usage"], snap["execution_usage"])

    def test_control_request_has_no_tools_and_does_not_mutate_chat_history(self):
        from jarvis_agent.agent_runtime import GroqResponsesAgent
        from jarvis_agent.mission_turn_contract import request_decision
        tools = Mock(spec=["ollama_tools"])
        tools.ollama_tools.return_value = []
        provider = GroqResponsesAgent(tools)
        client = Mock()
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(content='{"kind":"continue"}', tool_calls=[]))], usage=None)
        client.chat.completions.create.return_value = response
        provider._get_client = lambda: client
        old_history = list(provider._messages)
        request_decision(provider, {"human_message": "move forward", "current_goal": "observe"})
        sent = client.chat.completions.create.call_args.kwargs
        self.assertEqual(sent["tools"], [])
        self.assertEqual(sent["tool_choice"], "none")
        self.assertIn("move forward", sent["messages"][1]["content"])
        self.assertEqual(provider._messages, old_history)
        self.assertIsNone(provider._mission_decision_messages)

    def test_browser_stale_ref_does_not_block_after_read_only_same_tab_reinspection(self):
        from jarvis_agent.foundation_tools import FoundationToolAdapter
        browser = Mock()
        browser.call.side_effect = RuntimeError("stale_or_cross_tab_browser_ref")
        browser.observe_dom.return_value = {
            "observation_id": "fresh", "tab": {"tab_id": 7}, "controls": []}
        adapter = FoundationToolAdapter(Mock(), browser=browser)
        result = adapter.execute("browser_click", {"tab_id": 7, "ref": "expired"})
        self.assertFalse(result.success)
        data = json.loads(result.detail)
        self.assertIs(data["dispatched"], False)
        self.assertIs(data["outcome_unknown"], False)
        browser.observe_dom.assert_called_once_with(7)
        self.assertFalse(failure_requires_review([result]))
        self.assertEqual(adapter.uncertain_scopes, set())

    def test_native_unknown_effect_blocks_another_mutation_in_same_turn(self):
        from jarvis_agent.foundation_tools import FoundationToolAdapter
        native = Mock()
        native.execute.return_value = AgentActionResult("click_ui_element", False, "unknown", json.dumps({
            "dispatched": True, "outcome_unknown": True, "scope": ["native_windows", None]}))
        adapter = FoundationToolAdapter(native)
        adapter.execute("click_ui_element", {"ref": "obs6:e1"})
        again = adapter.execute("click_ui_element", {"ref": "obs7:e1"})
        self.assertFalse(again.success)
        self.assertTrue(json.loads(again.detail)["outcome_unknown"])
        native.execute.assert_called_once()

    def test_native_document_tab_tool_remains_visible_without_browser_dispatch_permission(self):
        from jarvis_agent.foundation_tools import FoundationToolAdapter
        adapter = FoundationToolAdapter(NativeToolRegistry(), browser=Mock())
        adapter.begin_turn("Ferme uniquement le document de cette application Windows.")
        names = {t["function"]["name"] for t in adapter.ollama_tools()}
        self.assertIn("close_tab", names)
        adapter.begin_turn("Ferme uniquement cet onglet Chrome.")
        self.assertNotIn("close_tab", {t["function"]["name"] for t in adapter.ollama_tools()})
        rejected = adapter.execute("close_tab", {"name": "observed tab"})
        self.assertFalse(rejected.success)

    def test_mission_center_shows_revision_and_escapes_user_text(self):
        from jarvis_agent.operator_console import render_snapshot
        html, _, _ = render_snapshot({"mission_enabled": True, "reliability_enabled": False,
            "missions": [{"id": "live_test", "status": "waiting_external", "goal": "initial",
                          "current_goal": "Inspect then <script>new goal</script>", "initial_goal": "initial",
                          "goal_revision": 2, "known_fact_count": 3, "permission_count": 0,
                          "goal_history": [{"revision": 2, "kind": "revise", "human_source": "advance"}]}]})
        self.assertIn("révision 2", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("advance", html)

    def test_initial_tool_pack_is_smaller_but_hidden_capability_is_discoverable(self):
        from jarvis_agent.request_context import RequestContext
        from tests.test_efficient_context_v10e import tool
        names = ["inspect_interface", "open_file", "list_applications", "click_ui_element",
                 "write_visual_target", "research_web", "mcp__calendar_create", "browser_click"]
        schemas = [tool(n) for n in names]
        context = RequestContext(enabled=True)
        first = context.prepare_tools(schemas)
        exposed = {x["function"]["name"] for x in first}
        self.assertNotIn("mcp__calendar_create", exposed)
        self.assertIn("mcp__calendar_create", context.available)
        self.assertLess(len(first), len(schemas))
        context.select_tools({"names": ["mcp__calendar_create"]})
        next_pack = context.prepare_tools(schemas)
        self.assertIn("mcp__calendar_create", {x["function"]["name"] for x in next_pack})


if __name__ == "__main__":
    unittest.main()
