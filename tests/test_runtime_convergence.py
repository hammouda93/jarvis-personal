"""Safety and non-regression tests for the opt-in live mission adapter."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jarvis_agent.kernel_contracts import MissionStatus
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.task_graph import TaskNode, TaskStatus


class FakeDelegate:
    def __init__(self):
        self.calls = []
        self.result = SimpleNamespace(text="ok", actions=())
        self.error = None
        self.reset_calls = 0
        self.external_calls = []

    def run(self, user_text, *, log=None, phase=None):
        self.calls.append(("run", user_text))
        if self.error is not None:
            raise self.error
        return self.result

    def run_with_context(self, user_text, context, *, log=None, phase=None):
        self.calls.append(("context", user_text, context))
        return self.result

    def record_external_turn(self, *args, **kwargs):
        self.external_calls.append((args, kwargs))

    def reset(self):
        self.reset_calls += 1

    def warm_up(self, *, log=None):
        return "warm"


def action(name="browser_click", success=True):
    return SimpleNamespace(name=name, success=success)


class LiveMissionContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.delegate = FakeDelegate()
        self.runtime = LiveMissionContinuityRuntime(
            self.delegate, base_dir=self.root, owner_user_id="user-a"
        )

    def saved(self, mission_id):
        return self.runtime.context_store.load(mission_id)[0]

    def test_ordinary_text_turn_preserves_exact_result(self):
        result = self.runtime.run("bonjour")
        self.assertIs(result, self.delegate.result)
        self.assertEqual(self.delegate.calls, [("run", "bonjour")])
        self.assertIsNone(self.runtime.active_mission_id)
        rows = self.runtime.context_store.list_resumable()
        self.assertEqual(rows, [])
        # The single conversation turn is not an externally acting mission.
        with self.runtime.context_store._connect() as conn:
            row = conn.execute("SELECT mission_id FROM mission_contexts").fetchone()
        ctx = self.saved(row["mission_id"])
        self.assertEqual(ctx.status, MissionStatus.COMPLETED)
        self.assertFalse(ctx.observed_state["goal_verified"])

    def test_successful_tool_does_not_prove_user_goal(self):
        self.delegate.result = SimpleNamespace(
            text="clic exécuté", actions=(action(),)
        )
        self.runtime.run("envoie le message")
        candidates = self.runtime.context_store.list_resumable()
        self.assertEqual(len(candidates), 1)
        ctx = self.saved(candidates[0]["mission_id"])
        self.assertEqual(ctx.status, MissionStatus.WAITING_EXTERNAL)
        self.assertFalse(ctx.observed_state["goal_verified"])
        graph = self.runtime.graph_store.load(ctx.mission_id)
        self.assertEqual(graph.nodes()[0].result["action_names"], ["browser_click"])
        self.assertEqual(graph.summary()["completed"], 1)

    def test_failed_action_blocks_mission_without_rewriting_reply(self):
        self.delegate.result = SimpleNamespace(
            text="erreur", actions=(action(success=False),)
        )
        self.assertIs(self.runtime.run("mission"), self.delegate.result)
        item = self.runtime.context_store.list_resumable()[0]
        self.assertEqual(self.saved(item["mission_id"]).status, MissionStatus.BLOCKED)

    def test_explicit_multiturn_and_proof_gate(self):
        mission_id = self.runtime.begin_mission("chercher puis préparer un rapport")
        self.assertRaises(RuntimeError, self.runtime.begin_mission, "another mission")
        self.runtime.run("trouve la source")
        self.runtime.run_with_context("fais une synthèse", "source observée")
        self.assertEqual(self.delegate.calls[1][0], "context")
        self.assertEqual(self.runtime.active_mission_id, mission_id)
        self.assertEqual(len(self.runtime.graph_store.load(mission_id).nodes()), 2)
        self.assertEqual(self.saved(mission_id).status, MissionStatus.WAITING_EXTERNAL)
        with self.assertRaises(ValueError):
            self.runtime.complete_mission(proof_ref="")
        self.runtime.complete_mission(proof_ref="independent-review-42")
        ctx = self.saved(mission_id)
        self.assertEqual(ctx.status, MissionStatus.COMPLETED)
        self.assertTrue(ctx.observed_state["goal_verified"])
        self.assertEqual(ctx.proof_refs, ["independent-review-42"])
        self.assertIsNone(self.runtime.active_mission_id)

    def test_exception_fails_closed_and_never_retries_delegate(self):
        mission_id = self.runtime.begin_mission("send once")
        self.delegate.error = TimeoutError("tool outcome unknown")
        with self.assertRaises(TimeoutError):
            self.runtime.run("execute")
        self.assertEqual(len(self.delegate.calls), 1)
        self.assertEqual(self.saved(mission_id).status, MissionStatus.BLOCKED)
        other = LiveMissionContinuityRuntime(
            self.delegate, base_dir=self.root, owner_user_id="user-a"
        )
        recovered = other.resume_mission(mission_id)
        self.assertEqual(recovered.status, MissionStatus.BLOCKED)
        with self.assertRaises(RuntimeError):
            other.run("try again")
        self.assertEqual(len(self.delegate.calls), 1)
        with self.assertRaises(RuntimeError):
            other.complete_mission(proof_ref="unverified")

    def test_restart_running_checkpoint_never_auto_replays(self):
        mission_id = self.runtime.begin_mission("external side effect")
        graph = self.runtime.graph_store.load(mission_id)
        graph.add(TaskNode(
            task_id="turn_00001", mission_id=mission_id,
            capability="interaction.live_turn", agent_id="interaction",
            status=TaskStatus.RUNNING,
        ))
        self.runtime.graph_store.save(graph)
        state, version = self.runtime.context_store.load(mission_id)
        state.status = MissionStatus.RUNNING
        self.runtime.context_store.save(state, expected_version=version)
        other = LiveMissionContinuityRuntime(
            self.delegate, base_dir=self.root, owner_user_id="user-a"
        )
        recovered = other.resume_mission(mission_id)
        self.assertEqual(recovered.status, MissionStatus.BLOCKED)
        self.assertTrue(recovered.pending_action["manual_review_required"])
        self.assertEqual(
            other.graph_store.load(mission_id).nodes()[0].status,
            TaskStatus.WAITING_EXTERNAL,
        )
        self.assertEqual(self.delegate.calls, [])
        with self.assertRaises(RuntimeError):
            other.run("continue")
        self.assertEqual(self.delegate.calls, [])

    def test_foreign_mission_cannot_be_resumed(self):
        mission_id = self.runtime.begin_mission("private mission")
        other = LiveMissionContinuityRuntime(
            self.delegate, base_dir=self.root, owner_user_id="user-b"
        )
        with self.assertRaises(PermissionError):
            other.resume_mission(mission_id)

    def test_detach_reset_and_passthrough_are_non_destructive(self):
        mission_id = self.runtime.begin_mission("persistent goal")
        self.runtime.reset()
        self.assertIsNone(self.runtime.active_mission_id)
        self.assertEqual(self.delegate.reset_calls, 1)
        self.assertIsNotNone(self.saved(mission_id))
        self.runtime.record_external_turn("x", "y", success=True)
        self.assertEqual(len(self.delegate.external_calls), 1)
        self.assertEqual(self.runtime.warm_up(), "warm")
        self.runtime.resume_mission(mission_id)
        self.assertEqual(self.runtime.active_mission_id, mission_id)

    def test_explicit_mission_without_tools_still_needs_proof(self):
        mission_id = self.runtime.begin_mission("analyse complexe")
        self.runtime.run("analyse")
        self.assertEqual(self.saved(mission_id).status, MissionStatus.WAITING_EXTERNAL)


if __name__ == "__main__":
    unittest.main()
