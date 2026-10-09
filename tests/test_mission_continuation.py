"""Mission dispositions are control requests, never permissions or goal proofs."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_active_mission_supervisor as fixtures
import test_agent_runtime as provider_fixtures
from jarvis_agent.active_mission_supervisor import MissionLimits, SupervisorStopped, reserve_model_request
from jarvis_agent.native_tools import AgentActionResult


class MissionContinuationTests(unittest.TestCase):
    approve = fixtures.SupervisorTests.approve
    state = fixtures.SupervisorTests.state

    def setUp(self):
        fixtures.SupervisorTests.setUp(self)
        self.requests = []
        self.contexts = []
        original = self.registry.execute

        def execute(name, arguments, **kwargs):
            result = original(name, arguments, **kwargs)
            if name == "open_url":
                self.registry.location = "after" if arguments.get("url") == "https://example.org/final" else "intermediate"
            return AgentActionResult(name=result.name, success=result.success, message="fixture", detail=result.detail)
        self.registry.execute = execute

    def incremental_delegate(self, *, count=3, repeat=False):
        def turn(text, context, **kwargs):
            reserve_model_request()
            self.requests.append(text)
            self.contexts.append(context)
            url = "https://example.org/same" if repeat else (
                "https://example.org/final" if len(self.requests) == count else f"https://example.org/step-{len(self.requests)}")
            result = self.tools.execute("open_url", {"url": url})
            checkpoint = self.tools.execute("mission_checkpoint", {"status": "continue"})
            self.assertTrue(checkpoint.success)
            return SimpleNamespace(text="Installation terminee!", actions=(result,), end_session=False, should_exit=False)
        self.delegate.run_with_context = turn

    def test_one_large_step_continues_until_independent_final_predicate(self):
        self.incremental_delegate()
        self.approve(rules={"destination": self.rule})
        progress = []
        result = self.supervisor.run_until_pause(progress=progress.append)
        self.assertTrue(result["goal_verified"])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual([r["status"] for r in progress], ["READY", "READY", "COMPLETED"])
        self.assertEqual(len(result["_turn_result"].actions), 3)
        self.assertEqual(self.state().observed_state["active_supervisor"]["usage"]["model_calls"], 3)
        self.assertNotIn("Installation", result["text"])

    def test_conversation_coordinates_without_extra_user_messages_and_keeps_whole_goal(self):
        self.incremental_delegate()
        self.approve(rules={"destination": self.rule})
        phases = []
        turn = self.runtime.run("proceed", phase=phases.append)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[0], "proceed")
        self.assertTrue(all("reach the observed destination" in c for c in self.contexts))
        self.assertEqual(len(turn.actions), 3)
        self.assertEqual(sum(p.startswith("mission_progress:") for p in phases), 3)
        self.assertTrue(self.state().observed_state["goal_verified"])

    def test_identical_successful_mutation_is_not_replayed_on_continuation(self):
        self.incremental_delegate(repeat=True)
        self.approve(rules={"destination": self.rule})
        with self.assertRaisesRegex(SupervisorStopped, "repeat_successful_action"):
            self.supervisor.run_until_pause()
        self.assertEqual(len([c for c in self.registry.calls if c[0] == "open_url"]), 1)
        self.assertFalse(self.state().observed_state["goal_verified"])

    def test_continue_without_actual_work_pauses_instead_of_spinning(self):
        self.approve()
        def turn(*args, **kwargs):
            self.requests.append("turn")
            result = self.tools.execute("mission_checkpoint", {"status": "continue"})
            return SimpleNamespace(text="done", actions=(result,))
        self.delegate.run_with_context = turn
        first = self.supervisor.run_until_pause()
        self.assertEqual(first["steps_advanced"], 1)
        self.assertEqual(first["state"], "RECOVERING")
        self.supervisor.run_until_pause()
        self.assertEqual(self.requests, ["turn"])
        self.assertEqual(self.registry.calls, [])

    def test_checkpoint_cannot_complete_goal_or_grant_approval(self):
        self.approve()
        decisions = []
        def turn(*args, **kwargs):
            for packet in ({"status": "completed"}, {"status": "continue", "approved": True}, {"status": []}):
                decisions.append(self.tools.execute("mission_checkpoint", packet))
            return SimpleNamespace(text="done", actions=())
        self.delegate.run_with_context = turn
        self.supervisor.advance()
        self.assertTrue(all(not d.success for d in decisions))
        self.assertFalse(self.state().observed_state["goal_verified"])
        self.assertEqual(self.registry.calls, [])

    def test_false_final_claim_is_replaced_and_verification_does_not_replay(self):
        self.approve()
        def turn(*args, **kwargs):
            result = self.tools.execute("open_url", {"url": "https://example.org/intermediate"})
            self.tools.execute("mission_checkpoint", {"status": "awaiting_verification"})
            return SimpleNamespace(text="Installation terminee!", actions=(result,))
        self.delegate.run_with_context = turn
        result = self.supervisor.run_until_pause()
        self.assertFalse(result["goal_verified"])
        self.assertNotIn("Installation", result["text"])
        self.supervisor.run_until_pause()
        self.assertEqual(len(self.registry.calls), 1)

    def test_model_budget_is_cumulative_across_continuation_turns(self):
        self.incremental_delegate(count=5)
        self.approve(limits=MissionLimits(model_calls=2))
        with self.assertRaisesRegex(SupervisorStopped, "model_calls"):
            self.supervisor.run_until_pause()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(self.registry.calls), 2)

    def test_turn_round_limit_yields_without_retiring_unfinished_mission_step(self):
        from jarvis_agent.agent_runtime import AgentTurnResult
        self.approve(rules={"destination": self.rule})
        def turn(*args, **kwargs):
            self.requests.append("turn")
            final = len(self.requests) == 2
            result = self.tools.execute("open_url", {"url": "https://example.org/final" if final else "https://example.org/intermediate"})
            return AgentTurnResult(text="bounded yield", actions=(result,), pause_reason="turn_budget_exhausted")
        self.delegate.run_with_context = turn
        result = self.supervisor.run_until_pause()
        self.assertTrue(result["goal_verified"])
        self.assertEqual(len(self.requests), 2)

    def test_operator_stop_between_intermediate_reports_prevents_next_effect(self):
        self.incremental_delegate()
        self.approve()
        stop = threading.Event()
        with self.assertRaisesRegex(SupervisorStopped, "operator_requested_stop"):
            self.supervisor.run_until_pause(stop_event=stop, progress=lambda _: stop.set())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.registry.calls), 1)
        self.assertFalse(self.state().observed_state["goal_verified"])

    def test_unknown_effect_cannot_request_another_turn(self):
        self.incremental_delegate()
        self.registry.unknown = True
        self.approve()
        with self.assertRaisesRegex(SupervisorStopped, "outcome_requires_review"):
            self.supervisor.run_until_pause()
        self.assertEqual(len(self.registry.calls), 1)

    def test_provider_requires_disposition_before_yielding_bare_intermediate_prose(self):
        def response(name=None, arguments=None, text=""):
            output = [{"type": "function_call", "name": name, "arguments": json.dumps(arguments or {}),
                       "call_id": "call_" + str(id(arguments))}] if name else [{"type": "message",
                       "content": [{"type": "output_text", "text": text}]}]
            return {"output": output}
        provider = provider_fixtures.FakeGroqAgent(self.tools, [
            response("open_url", {"url": "https://example.org/intermediate"}),
            response(text="Click Next yourself; finished."),
            response("mission_checkpoint", {"status": "continue"}), response(text="progress"),
            response("open_url", {"url": "https://example.org/final"}),
            response("mission_checkpoint", {"status": "awaiting_verification"}), response(text="done"),
        ])
        self.runtime.delegate = provider
        self.approve(rules={"destination": self.rule})
        turn = self.runtime.run("complete the workflow")
        self.assertTrue(self.state().observed_state["goal_verified"])
        self.assertEqual(len(provider.payloads), 7)
        self.assertTrue(any("MISSION_DISPOSITION_REQUIRED" in str(p["messages"]) for p in provider.payloads))
        self.assertNotIn("Click Next", turn.text)


if __name__ == "__main__":
    unittest.main()
