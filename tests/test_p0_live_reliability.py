"""P0 reproductions across the real SQLite, mission and trace boundaries."""
import json
import os
import tempfile
import unittest
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.agent_runtime import AgentTurnResult
from jarvis_agent.foundation_tools import FoundationRuntime, FoundationToolAdapter
from jarvis_agent.kernel_contracts import MissionStatus
from jarvis_agent.memory_core_store import MemoryCoreStore
from jarvis_agent.mission_trace_v10h import ConversationMissionTrace
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from tests.test_memory_direct_agent_v10f import DIRECT, MinimalNativeRegistry
from tests.test_runtime_convergence import FakeDelegate
from tests.test_browser_goal_proof_v11b import GOAL, DEST, journey
from tests.test_browser_goal_proof_v11b import action as receipt_action
from tests.test_agent_runtime import FakeGroqAgent, FakeTools
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent.config import settings
from jarvis_agent.browser_goal_proof import (
    verify_browser_navigation_goal, navigation_goal_scope,
)
from jarvis_agent.memory_goal_proof import (
    MemoryWriteReceipt, parse_memory_write_receipt, readback_memory_write,
)


def with_payload(action, **fields):
    data = json.loads(action.detail)
    data.update(fields)
    return receipt_action(action.name, data, action.success)


def click_readback_journey():
    actions = journey()[:-1]
    actions[-1] = with_payload(actions[-1], independent_readback={
        "tab_id": 7, "url": DEST, "status": "complete"})
    return actions


class ReplayTools(FakeTools):
    def __init__(self, actions):
        super().__init__()
        self.remaining = list(actions)

    def ollama_tools(self):
        return [{"type": "function", "function": {
            "name": name, "description": "Offline generic browser primitive",
            "parameters": {"type": "object", "properties": {
                "tab_id": {"type": "integer"}, "url": {"type": "string"},
                "ref": {"type": "string"}, "text": {"type": "string"}},
                "required": []}}} for name in {
                    "open_url", "browser_get_active_tab", "browser_observe_dom",
                    "browser_find", "browser_click", "browser_verify"}]

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, arguments))
        result = self.remaining.pop(0)
        if result.name != name:
            raise AssertionError((name, result.name))
        return AgentActionResult(name, result.success, "Offline receipt", result.detail)


def tool_response(action, index):
    data = json.loads(action.detail)
    args = {"url": "https://example.test/dashboard/"} if action.name == "open_url" else {"tab_id": 7}
    if action.name == "browser_click":
        args["ref"] = data["source_ref"]
    if action.name == "browser_find":
        args["text"] = "Joueurs"
    return {"output": [{"type": "function_call", "call_id": "p0-" + str(index),
                        "name": action.name, "arguments": json.dumps(args)}]}


def text_response(text):
    return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


class P0LiveReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_json_receipt_acknowledgement_and_trace_agree_with_sqlite(self):
        goal = "Retiens que mon code de recette Jarvis est NEBULA-4827."
        store = MemoryCoreStore(self.root / "memory.sqlite3")
        delegate = FakeDelegate()
        adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
        runtime = LiveMissionContinuityRuntime(
            FoundationRuntime(delegate, adapter), base_dir=self.root / "missions")
        lines = []
        trace = ConversationMissionTrace(goal, lines.append)
        trace.start()
        with patch.dict(os.environ, DIRECT):
            result = runtime.run(goal, log=trace.ingest)
        trace.finish(result.actions)
        self.assertIn("mémorisé durablement", result.text)
        self.assertEqual(delegate.calls, [])
        self.assertEqual(len(store.all_memories()), 1)
        receipt = json.loads(result.actions[0].detail)
        reopened = MemoryCoreStore(store.db_path)
        self.assertEqual(reopened.get_memory(receipt["memory_id"]).content, goal)
        self.assertTrue(any("goal_verification=certified" in line for line in lines))
        self.assertFalse(any("goal_verification=not_certified" in line for line in lines))

    def test_browser_proof_combines_two_turns_after_runtime_restart(self):
        delegate = FakeDelegate()
        runtime = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = runtime.begin_mission(GOAL)
        delegate.result = AgentTurnResult("Menu ouvert", actions=tuple(journey()[:5]))
        runtime.run(GOAL)
        runtime.detach_mission()
        restarted = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        restarted.resume_mission(mid)
        delegate.result = AgentTurnResult("Navigation effectuée", actions=tuple(journey()[5:]))
        lines = []
        restarted.run("continue la mission", log=lines.append)
        state, _ = restarted.context_store.load(mid)
        self.assertEqual(state.status, MissionStatus.COMPLETED)
        self.assertTrue(state.observed_state["goal_verified"])
        self.assertEqual(state.observed_state["verified_navigation_url"], DEST)
        self.assertEqual(state.user_goal, GOAL)
        self.assertEqual(len(delegate.calls), 2)
        self.assertTrue(any("state=COMPLETED" in line for line in lines))

    def test_memory_receipt_is_versioned_and_survives_a_new_process(self):
        store = MemoryCoreStore(self.root / "memory.sqlite3")
        adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
        goal = "Retiens que mon code est NEBULA-4827."
        with patch.dict(os.environ, DIRECT):
            adapter.begin_turn(goal)
            action = adapter.execute("remember_information", {"content": "invented"})
        parsed = parse_memory_write_receipt(action.detail)
        self.assertEqual(parsed.receipt_version, 1)
        process = subprocess.run([sys.executable, "-c",
            "import sys; from jarvis_agent.memory_core_store import MemoryCoreStore; "
            "print(MemoryCoreStore(sys.argv[1]).get_memory(int(sys.argv[2])).content)",
            str(store.db_path), str(parsed.memory_id)], capture_output=True, text=True,
            encoding="utf-8", timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), goal)

    def test_receipt_parser_rejects_malformed_identity_hash_version_and_unknown_effect(self):
        payload = json.loads(MemoryWriteReceipt(1, "a" * 64, "2026-10-13T00:00:00Z").as_json())
        for key, value in (("memory_id", True), ("memory_id", "1"), ("memory_id", -1),
                           ("raw_sha256", "bad"), ("receipt_version", 9),
                           ("dispatched", False), ("outcome_unknown", True),
                           ("db_recorded_at", []), ("verified", "true")):
            with self.subTest(key=key, value=value):
                self.assertIsNone(parse_memory_write_receipt(json.dumps({**payload, key: value})))
        for raw in ("memory_id=1", "{", "null", "[]"):
            self.assertIsNone(parse_memory_write_receipt(raw))

    def test_independent_memory_read_rejects_content_hash_and_timestamp_mismatch(self):
        from hashlib import sha256
        store = MemoryCoreStore(self.root / "memory.sqlite3")
        goal = "Retiens que mon code est NEBULA-4827."
        item = store.remember(goal)
        payload = MemoryWriteReceipt(item.id, sha256(goal.encode()).hexdigest(), item.created_at)
        self.assertEqual(readback_memory_write(goal, payload.as_json(), store).id, item.id)
        for modified in (replace(payload, raw_sha256="a" * 64),
                         replace(payload, db_recorded_at="wrong"), replace(payload, memory_id=987)):
            self.assertIsNone(readback_memory_write(goal, modified.as_json(), store))
        self.assertIsNone(readback_memory_write(goal + " altered", payload.as_json(), store))

    def test_commit_then_exception_never_writes_twice_and_reports_uncertainty(self):
        store = MemoryCoreStore(self.root / "memory.sqlite3")
        original = store.remember
        def commit_then_error(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("lost receipt after commit")
        adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
        with patch.dict(os.environ, DIRECT), patch.object(store, "remember", side_effect=commit_then_error) as write:
            adapter.begin_turn("Retiens que mon code est NEBULA-4827.")
            first = adapter.execute("remember_information", {"content": "unused"})
            second = adapter.execute("remember_information", {"content": "retry"})
        self.assertFalse(first.success)
        self.assertTrue(json.loads(first.detail)["outcome_unknown"])
        self.assertFalse(second.success)
        self.assertEqual(write.call_count, 1)
        self.assertEqual(len(store.all_memories()), 1)

    def test_failed_independent_read_blocks_without_duplicate_or_false_certificate(self):
        store = MemoryCoreStore(self.root / "memory.sqlite3")
        adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
        runtime = LiveMissionContinuityRuntime(FoundationRuntime(FakeDelegate(), adapter), base_dir=self.root / "missions")
        goal = "Retiens que mon code est NEBULA-4827."
        mid = runtime.begin_mission(goal)
        with patch.dict(os.environ, DIRECT), patch.object(store, "get_memory", side_effect=OSError("locked")):
            result = runtime.run(goal)
        state, _ = runtime.context_store.load(mid)
        self.assertEqual(state.status, MissionStatus.BLOCKED)
        self.assertFalse(state.observed_state["goal_verified"])
        self.assertNotIn("mémorisé durablement", result.text)
        with self.assertRaises(RuntimeError):
            runtime.run("continue")
        self.assertEqual(len(store.all_memories()), 1)

    def test_malformed_success_receipt_is_uncertain_and_never_rewritten(self):
        store = MemoryCoreStore(self.root / "memory.sqlite3")
        adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
        execute = adapter.execute
        def corrupt(*args, **kwargs):
            return replace(execute(*args, **kwargs), detail="{malformed")
        runtime = FoundationRuntime(FakeDelegate(), adapter)
        with patch.dict(os.environ, DIRECT), patch.object(adapter, "execute", side_effect=corrupt):
            result = runtime.run("Retiens que mon code est NEBULA-4827.")
        self.assertFalse(result.actions[0].success)
        self.assertTrue(json.loads(result.actions[0].detail)["outcome_unknown"])
        self.assertEqual(len(store.all_memories()), 1)

    def test_unauthorized_memory_mutation_is_refused(self):
        store = MemoryCoreStore(self.root / "memory.sqlite3")
        with patch.dict(os.environ, DIRECT):
            adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
            adapter.begin_turn("Bonjour")
            result = adapter.execute("remember_information", {"content": "unauthorized"})
        self.assertFalse(result.success)
        self.assertEqual(store.all_memories(), [])

    def test_unproven_menu_expansion_never_certifies_the_global_goal(self):
        actions = journey()
        actions[4] = with_payload(actions[4], post_observation={})
        self.assertIsNone(verify_browser_navigation_goal(GOAL, actions))

    def test_stale_find_generation_or_wrong_window_prevents_global_certificate(self):
        actions = journey()
        actions.insert(6, receipt_action("browser_observe_dom", {
            "sensor": "dom", "observation_id": "new-generation",
            "tab": {"tab_id": 7}, "controls": []}))
        self.assertIsNone(verify_browser_navigation_goal(GOAL, actions))
        actions = journey()
        actions[0] = with_payload(actions[0], tab={"tab_id": 7, "window_id": 3})
        actions[6] = with_payload(actions[6], source_window_id=4)
        self.assertIsNone(verify_browser_navigation_goal(GOAL, actions))

    def test_read_failure_then_fresh_same_sensor_can_certify_without_replay(self):
        actions = journey()
        actions.insert(2, receipt_action("browser_observe_dom", {"reason": "loading"}, False))
        self.assertIsNotNone(verify_browser_navigation_goal(GOAL, actions))

    def test_legacy_read_error_does_not_poison_persisted_multi_turn_proof(self):
        delegate = FakeDelegate()
        runtime = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = runtime.begin_mission(GOAL)
        first = journey()[:5]
        first.insert(2, AgentActionResult("browser_observe_dom", False, "Not ready", "RuntimeError: loading"))
        delegate.result = AgentTurnResult("menu", actions=tuple(first))
        runtime.run(GOAL)
        self.assertEqual(runtime.context_store.load(mid)[0].status, MissionStatus.WAITING_EXTERNAL)
        delegate.result = AgentTurnResult("final", actions=tuple(journey()[5:]))
        runtime.run("continue la mission")
        self.assertEqual(runtime.context_store.load(mid)[0].status, MissionStatus.COMPLETED)

    def test_successful_dispatch_with_unknown_effect_still_blocks(self):
        delegate = FakeDelegate()
        runtime = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = runtime.begin_mission(GOAL)
        delegate.result = AgentTurnResult("sent", actions=(receipt_action("browser_click", {
            "dispatched": True, "outcome_unknown": True}, True),))
        runtime.run(GOAL)
        self.assertEqual(runtime.context_store.load(mid)[0].status, MissionStatus.BLOCKED)
        with self.assertRaises(RuntimeError):
            runtime.run("continue")
        self.assertEqual(len(delegate.calls), 1)

    def test_prior_receipts_cannot_certify_a_new_turn_without_current_read(self):
        delegate = FakeDelegate()
        runtime = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = runtime.begin_mission(GOAL)
        delegate.result = AgentTurnResult("paused", actions=tuple(journey()), pause_reason="user_stopped")
        runtime.run(GOAL)
        delegate.result = AgentTurnResult("done", actions=())
        runtime.run("continue")
        self.assertFalse(runtime.context_store.load(mid)[0].observed_state["goal_verified"])

    def test_provider_429_pause_preserves_receipts_and_recovery_certifies_once(self):
        from jarvis_agent.agent_runtime import AgentRuntimeUnavailable
        delegate = FakeDelegate()
        runtime = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = runtime.begin_mission(GOAL)
        delegate._last_partial_actions = journey()[:5]
        delegate.error = AgentRuntimeUnavailable("429 quota fixture")
        with self.assertRaises(AgentRuntimeUnavailable):
            runtime.run(GOAL)
        paused = runtime.graph_store.load(mid).nodes()[0]
        self.assertEqual(len(paused.result["action_receipts"]), 5)
        runtime.detach_mission()
        recovered = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        recovered.resume_mission(mid)
        delegate.error = None
        delegate.result = AgentTurnResult("secondary result", actions=tuple(journey()[5:]))
        result = recovered.run("continue la mission")
        self.assertIn(DEST, result.text)
        self.assertEqual(recovered.context_store.load(mid)[0].status, MissionStatus.COMPLETED)
        self.assertEqual(len(delegate.calls), 2)

    def test_resume_guard_rejects_duplicate_navigation_and_click_before_dispatch(self):
        native = FakeTools()
        adapter = FoundationToolAdapter(native)
        with navigation_goal_scope(GOAL, journey()[:5]):
            for name, args in (("open_url", {"url": "https://example.test/dashboard/"}),
                               ("browser_click", {"tab_id": 7, "ref": "menu-ref"}),
                               ("browser_click", {"tab_id": 7, "ref": "new-ref"})):
                result = adapter.execute(name, args)
                self.assertFalse(result.success)
                self.assertFalse(json.loads(result.detail)["dispatched"])
        self.assertEqual(native.calls, [])

    def test_ordinary_navigation_continues_after_model_yield_within_existing_budget(self):
        actions = click_readback_journey()
        tools = ReplayTools(actions)
        responses = [tool_response(a, i) for i, a in enumerate(actions[:5])]
        responses.append(text_response("Menu ouvert, voulez-vous continuer ?"))
        responses.extend(tool_response(a, i + 5) for i, a in enumerate(actions[5:]))
        agent = FakeGroqAgent(tools, responses)
        runtime = LiveMissionContinuityRuntime(agent, base_dir=self.root)
        mid = runtime.begin_mission(GOAL)
        with patch("jarvis_agent.agent_runtime.settings", replace(settings, agent_max_tool_rounds=10)):
            result = runtime.run(GOAL)
        self.assertIn(DEST, result.text)
        self.assertEqual(runtime.context_store.load(mid)[0].status, MissionStatus.COMPLETED)
        self.assertEqual(len(agent.payloads), 8)
        self.assertEqual([n for n, _ in tools.calls], [a.name for a in actions])

    def test_action_boundary_certificate_uses_original_goal_and_prior_turn(self):
        delegate = FakeDelegate()
        runtime = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = runtime.begin_mission(GOAL)
        delegate.result = AgentTurnResult("menu", actions=tuple(journey()[:5]))
        runtime.run(GOAL)
        runtime.detach_mission()
        actions = click_readback_journey()[5:]
        agent = FakeGroqAgent(ReplayTools(actions), [tool_response(a, i) for i, a in enumerate(actions)])
        restarted = LiveMissionContinuityRuntime(agent, base_dir=self.root)
        restarted.resume_mission(mid)
        result = restarted.run("continue la mission")
        self.assertIn(DEST, result.text)
        self.assertEqual(len(agent.payloads), 2)
        self.assertEqual(restarted.context_store.load(mid)[0].status, MissionStatus.COMPLETED)

    def test_completed_unverified_ui_has_a_distinct_amber_label(self):
        from jarvis_agent.operator_console import render_snapshot
        common = {"mission_enabled": True, "missions": [{"status": "completed", "verified": False}]}
        self.assertIn("OBJECTIF NON CERTIFIÉ", render_snapshot(common)[0])
        common["missions"][0]["verified"] = True
        self.assertIn("OBJECTIF CERTIFIÉ", render_snapshot(common)[0])
        self.assertNotIn("OBJECTIF NON CERTIFIÉ", render_snapshot(common)[0])

    def test_trace_retains_prior_step_activity_for_the_same_durable_goal(self):
        lines = []
        previous = ConversationMissionTrace(GOAL, lines.append, mission_id="durable-p0")
        previous.start()
        previous.finish(journey()[:5])
        follow = ConversationMissionTrace("continue la mission", lines.append,
            mission_id="durable-p0", previous=previous, original_goal=GOAL)
        follow.start()
        follow.finish(journey()[5:6])
        self.assertEqual(follow.steps, previous.steps)
        self.assertTrue(any("prior_turn_evidence=recorded" in line for line in lines))
        self.assertFalse(follow._goal_proof_logged)

    def test_latest_dom_contradicting_final_url_invalidates_old_readback(self):
        actions = journey() + [receipt_action("browser_observe_dom", {
            "sensor": "dom", "observation_id": "new-page",
            "tab": {"tab_id": 7, "url": "https://example.test/elsewhere"}, "controls": []})]
        self.assertIsNone(verify_browser_navigation_goal(GOAL, actions))

    def test_cerebras_429_secondary_continues_in_the_same_loop_without_double_action(self):
        from jarvis_agent.agent_runtime import CerebrasResponsesAgent, GroqResponsesAgent, AgentRuntimeUnavailable
        actions = click_readback_journey()
        responses = [FakeGroqAgent._response_from_dict(tool_response(a, i)) for i, a in enumerate(actions)]
        for response in responses:
            response.choices[0].finish_reason = "tool_calls"
        tools = ReplayTools(actions)
        config = replace(settings, cerebras_api_key="fixture-primary",
            cerebras_secondary_api_key="fixture-secondary", cerebras_fallback_to_groq=False,
            agent_max_tool_rounds=10)
        with patch("jarvis_agent.agent_runtime.settings", config), patch.object(
            GroqResponsesAgent, "_chat", side_effect=responses[:5] +
            [AgentRuntimeUnavailable("API error 429: fixture quota")] + responses[5:]) as chat:
            agent = CerebrasResponsesAgent(tools)
            runtime = LiveMissionContinuityRuntime(agent, base_dir=self.root)
            mid = runtime.begin_mission(GOAL)
            result = runtime.run(GOAL)
        self.assertEqual(chat.call_count, 8)
        self.assertFalse(agent._provider_rate_gate.available("primary"))
        self.assertEqual(agent.last_effective_credential, "secondary")
        self.assertEqual([n for n, _ in tools.calls].count("open_url"), 1)
        self.assertEqual([n for n, _ in tools.calls].count("browser_click"), 2)
        self.assertIn(DEST, result.text)
        self.assertEqual(runtime.context_store.load(mid)[0].status, MissionStatus.COMPLETED)

    def test_automatic_resume_grounding_is_persisted_and_reused_for_fresh_dom(self):
        from tests.test_browser_goal_proof_v11b import SOURCE
        delegate = FakeDelegate()
        first = LiveMissionContinuityRuntime(delegate, base_dir=self.root)
        mid = first.begin_mission(GOAL)
        delegate.result = AgentTurnResult("menu", actions=tuple(journey()[:5]))
        first.run(GOAL)
        first.detach_mission()
        link = {"type": "link", "name": "Joueurs", "ref": "fresh-link", "href": DEST, "actionable": True}
        class Browser:
            current_url = SOURCE
            calls = []
            def get_active_tab(self):
                self.calls.append("get_active_tab")
                return {"tab_id": 7, "url": self.current_url, "status": "complete"}
            def observe_dom(self, tab_id):
                self.calls.append("observe_dom")
                return {"sensor": "dom", "observation_id": "fresh-resume-dom",
                    "tab": {"tab_id": tab_id, "url": self.current_url, "status": "complete"}, "controls": [link]}
            def call(self, operation, **args):
                self.calls.append(operation)
                if operation == "find":
                    return {"observation_id": "fresh-resume-dom", "unique": True, "matches": [link]}
                if operation == "click":
                    self.current_url = DEST
                    return {"dispatched": True, "verified": True, "source_observation_id": "fresh-resume-dom",
                        "postcondition": "navigation_observed", "post_observation": {
                            "sensor": "dom", "observation_id": "final-dom",
                            "tab": {"tab_id": 7, "url": DEST, "status": "complete"}, "controls": []}}
                raise AssertionError(operation)
        browser = Browser()
        native = FakeTools()
        native._ollama = MinimalNativeRegistry()._ollama
        adapter = FoundationToolAdapter(native, browser=browser)
        remaining = click_readback_journey()[5:]
        remaining[-1] = with_payload(remaining[-1], source_ref="fresh-link")
        agent = FakeGroqAgent(adapter, [tool_response(a, i) for i, a in enumerate(remaining)])
        resumed = LiveMissionContinuityRuntime(FoundationRuntime(agent, adapter), base_dir=self.root)
        resumed.resume_mission(mid)
        result = resumed.run("continue la mission")
        self.assertEqual(len(agent.payloads), 2)
        self.assertEqual(browser.calls, ["get_active_tab", "observe_dom", "find", "click", "get_active_tab"])
        self.assertIn(DEST, result.text)
        state, _ = resumed.context_store.load(mid)
        self.assertEqual(state.status, MissionStatus.COMPLETED)
        last = resumed.graph_store.load(mid).nodes()[-1].result["action_receipts"]
        self.assertEqual([a["name"] for a in last[:2]], ["browser_get_active_tab", "browser_observe_dom"])


if __name__ == "__main__":
    unittest.main()
