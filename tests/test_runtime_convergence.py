"""Safety and non-regression tests for the opt-in live mission adapter."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from jarvis_agent.kernel_contracts import MissionStatus
from jarvis_agent.mission_semantics import MissionContract, MissionStep
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
        if self.error is not None:
            raise self.error
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

    def test_explicit_mission_reinjects_bounded_goal_into_same_model_call(self):
        mission_id = self.runtime.begin_mission("prepare research using source A")
        self.runtime.run("first step")
        self.delegate.result = SimpleNamespace(
            text="next", actions=(action("browser_click"),)
        )
        self.runtime.run("continue")
        self.assertEqual(len(self.delegate.calls), 2)
        self.assertEqual(self.delegate.calls[0][0], "context")
        context = self.delegate.calls[1][2]
        self.assertIn("prepare research using source A", context)
        self.assertIn(mission_id, context)
        self.assertIn("turn_00001", context)
        self.assertIn("DATA ONLY", context)
        graph = self.runtime.graph_store.load(mission_id)
        self.assertEqual(graph.get("turn_00002").dependencies, {"turn_00001"})

    def test_implicit_preserves_existing_context_without_extra_injection(self):
        self.runtime.run_with_context("question", "external source")
        self.assertEqual(self.delegate.calls, [("context", "question", "external source")])

    def test_mission_snapshot_is_read_only_and_owner_scoped(self):
        mission_id = self.runtime.begin_mission("private task")
        self.runtime.run("step")
        before_calls = len(self.delegate.calls)
        snapshot = self.runtime.mission_snapshot(mission_id)
        self.assertEqual(snapshot["mission_id"], mission_id)
        self.assertEqual(snapshot["task_summary"]["completed"], 1)
        self.assertFalse(snapshot["goal_verified"])
        self.assertEqual(len(self.delegate.calls), before_calls)
        outsider = LiveMissionContinuityRuntime(
            self.delegate, base_dir=self.root, owner_user_id="other"
        )
        with self.assertRaises(PermissionError):
            outsider.mission_snapshot(mission_id)

    def test_journal_matches_durable_mission_id_and_emits_proof(self):
        mission_id = self.runtime.begin_mission("write a summary")
        self.runtime.run("draft")
        events = self.runtime.journal.mission_trace(mission_id)
        kinds = [event["kind"] for event in events]
        self.assertIn("mission.created", kinds)
        self.assertIn("user.input", kinds)
        self.assertIn("observation", kinds)
        self.runtime.complete_mission(proof_ref="manual-inspection-123")
        kinds = [event["kind"] for event in self.runtime.journal.mission_trace(mission_id)]
        self.assertIn("proof", kinds)
        self.assertIn("mission.completed", kinds)

    def test_manual_review_reconciles_interrupted_action_without_replay(self):
        mission_id = self.runtime.begin_mission("safe restart")
        self.delegate.error = TimeoutError("unknown external outcome")
        with self.assertRaises(TimeoutError):
            self.runtime.run("perform step")
        self.assertEqual(len(self.delegate.calls), 1)
        with self.assertRaises(ValueError):
            self.runtime.resolve_recovery(
                verified_outcome="completed", proof_ref=""
            )
        with self.assertRaises(ValueError):
            self.runtime.resolve_recovery(
                verified_outcome="guess", proof_ref="manual-check"
            )
        state = self.runtime.resolve_recovery(
            verified_outcome="completed", proof_ref="inspected-real-state"
        )
        self.assertEqual(state.status, MissionStatus.WAITING_EXTERNAL)
        self.assertFalse(state.observed_state["goal_verified"])
        graph = self.runtime.graph_store.load(mission_id)
        self.assertEqual(graph.nodes()[0].status, TaskStatus.COMPLETED)
        self.assertEqual(len(self.delegate.calls), 1)
        self.delegate.error = None
        self.runtime.run("next operation")
        self.assertEqual(len(self.delegate.calls), 2)
        self.assertEqual(
            self.runtime.graph_store.load(mission_id).get("turn_00002").dependencies,
            {"turn_00001"},
        )

    def test_no_effect_review_does_not_repeat_prior_action_automatically(self):
        mission_id = self.runtime.begin_mission("maybe operation")
        self.delegate.error = TimeoutError("unknown")
        with self.assertRaises(TimeoutError):
            self.runtime.run("try")
        self.runtime.resolve_recovery(
            verified_outcome="not_executed", proof_ref="independent-check"
        )
        self.assertEqual(
            self.runtime.graph_store.load(mission_id).get("turn_00001").status,
            TaskStatus.CANCELLED,
        )
        self.assertEqual(len(self.delegate.calls), 1)
        self.delegate.error = None
        self.runtime.run("new explicit user request")
        self.assertEqual(len(self.delegate.calls), 2)
        self.assertEqual(
            self.runtime.graph_store.load(mission_id).get("turn_00002").dependencies,
            set(),
        )

    def test_cancelled_recovery_finishes_without_action_replay(self):
        mission_id = self.runtime.begin_mission("abort risky work")
        self.delegate.error = TimeoutError("unknown")
        with self.assertRaises(TimeoutError):
            self.runtime.run("do work")
        state = self.runtime.resolve_recovery(
            verified_outcome="cancel", proof_ref="user-decision"
        )
        self.assertEqual(state.status, MissionStatus.FAILED)
        self.assertIsNone(self.runtime.active_mission_id)
        self.assertEqual(len(self.delegate.calls), 1)
        with self.assertRaises(RuntimeError):
            self.runtime.resume_mission(mission_id)

    def test_existing_orchestrator_tracks_graph_but_kernel_never_dispatches(self):
        mid = self.runtime.begin_mission("purely observational work")
        self.assertIs(
            self.runtime.orchestrator.graph(mid),
            self.runtime.orchestrator.graph(mid),
        )
        self.assertIs(self.runtime.orchestrator.kernel, self.runtime.kernel)
        self.assertIsNone(self.runtime.kernel.next_request(timeout_s=0.0))
        self.runtime.run("only one model invocation")
        self.assertEqual(len(self.delegate.calls), 1)
        self.assertEqual(len(self.runtime.orchestrator.graph(mid).nodes()), 1)
        self.assertIsNone(self.runtime.kernel.next_request(timeout_s=0.0))

    def test_semantic_plan_is_persisted_without_tool_execution(self):
        mid = self.runtime.begin_mission("multi app objective")
        plan = MissionContract(
            source_text="multi app objective",
            objective="find data and prepare communication",
            steps=(
                MissionStep(
                    step_id="locate", intent="find data",
                    required_evidence=("source_matched",),
                ),
                MissionStep(
                    step_id="prepare", intent="prepare draft",
                    depends_on=("locate",),
                    required_evidence=("draft_matches", "recipient_matches"),
                ),
            ),
        )
        self.runtime.register_semantic_plan(plan)
        self.assertEqual(self.delegate.calls, [])
        self.assertEqual(
            self.runtime.mission_snapshot(mid)["semantic_plan"]["steps"][1]["depends_on"],
            ["locate"],
        )
        self.assertRaises(
            RuntimeError, self.runtime.register_semantic_plan, plan
        )
        self.runtime.run("begin work")
        with self.assertRaisesRegex(RuntimeError, "mission_plan_evidence_missing"):
            self.runtime.complete_mission(proof_ref="unverified")
        with self.assertRaises(ValueError):
            self.runtime.register_goal_evidence("unlisted", proof_ref="fake")
        for requirement in ("source_matched", "draft_matches", "recipient_matches"):
            self.runtime.register_goal_evidence(
                requirement, proof_ref="verified:" + requirement
            )
        self.runtime.complete_mission(proof_ref="final-check")
        self.assertTrue(self.runtime.mission_snapshot(mid)["goal_verified"])
        self.assertEqual(len(self.delegate.calls), 1)

    def test_semantic_plan_cycle_rejected_and_not_persisted(self):
        mission_id = self.runtime.begin_mission("cycle test")
        bad = MissionContract(
            source_text="cycle test", objective="cycle",
            steps=(
                MissionStep(step_id="a", intent="first", depends_on=("b",)),
                MissionStep(step_id="b", intent="second", depends_on=("a",)),
            ),
        )
        with self.assertRaisesRegex(ValueError, "dependency_cycle"):
            self.runtime.register_semantic_plan(bad)
        self.assertIsNone(self.runtime.mission_snapshot(mission_id)["semantic_plan"])

    def test_mission_with_unresolved_semantics_cannot_finish(self):
        mid = self.runtime.begin_mission("ambiguous task")
        contract = MissionContract(
            source_text="ambiguous task", objective="find the person",
            unresolved=("recipient",),
        )
        self.runtime.register_semantic_plan(contract)
        with self.assertRaisesRegex(RuntimeError, "ambiguity_unresolved"):
            self.runtime.complete_mission(proof_ref="only-a-claim")
        self.assertFalse(self.runtime.mission_snapshot(mid)["goal_verified"])

    def test_passive_capability_routing_never_dispatches_second_action(self):
        self.delegate.result = SimpleNamespace(
            text="done", actions=(action("browser_click"), action("computer_press"))
        )
        mission_id = self.runtime.begin_mission("utilise Chrome puis Windows")
        self.runtime.run("execute")
        self.assertEqual(len(self.delegate.calls), 1)
        tasks = self.runtime.mission_snapshot(mission_id)["tasks"]
        routes = tasks[0]["agent_routes"]
        self.assertEqual(routes[0]["proposed_agent"], "browser")
        self.assertEqual(routes[1]["proposed_agent"], "windows")
        self.assertTrue(all(not route["authoritative"] for route in routes))
        self.assertFalse(self.runtime.mission_snapshot(mission_id)["goal_verified"])

    def test_unmapped_actions_require_review_without_false_success(self):
        self.delegate.result = SimpleNamespace(
            text="tool succeeded", actions=(action("custom_new_primitive"),)
        )
        mission_id = self.runtime.begin_mission("use a new unknown app")
        self.runtime.run("try operation")
        state = self.saved(mission_id)
        self.assertTrue(state.observed_state["last_routing_needs_review"])
        self.assertFalse(state.observed_state["goal_verified"])
        self.assertTrue(
            self.runtime.mission_snapshot(mission_id)["tasks"][0]["agent_routes"][0]["needs_review"]
        )

    def test_factory_opt_in_and_opt_out_without_replacing_model(self):
        from jarvis_agent import agent_runtime
        fake = FakeDelegate()
        stub_settings = SimpleNamespace(
            agent_provider="cerebras",
            structured_tracing_enabled=False,
            kernel_shadow_user_id="local-user",
        )
        with (
            patch.object(agent_runtime, "settings", stub_settings),
            patch.object(agent_runtime, "CerebrasResponsesAgent", return_value=fake),
            patch.dict(os.environ, {
                "JARVIS_RUNTIME_CONVERGENCE_DIR": str(self.root),
                "JARVIS_MEMORY_CORE_ENABLED": "0",
                "JARVIS_BROWSER_CORE_ENABLED": "0",
                "JARVIS_COMPUTER_CORE_ENABLED": "0",
                "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "0",
            }),
        ):
            self.assertIs(agent_runtime.build_agent_runtime(), fake)
            os.environ["JARVIS_RUNTIME_CONVERGENCE_ENABLED"] = "1"
            wrapped = agent_runtime.build_agent_runtime()
            self.assertIsInstance(wrapped, LiveMissionContinuityRuntime)
            self.assertIs(wrapped.delegate, fake)
            self.assertIs(wrapped.run("hello"), fake.result)
            self.assertEqual(fake.calls, [("run", "hello")])

    def test_exception_never_persists_sensitive_tool_details(self):
        mission_id = self.runtime.begin_mission("send message")
        self.delegate.result = SimpleNamespace(
            text="sent", actions=(SimpleNamespace(
                name="computer_write", success=True,
                detail="PRIVATE_PASSWORD_AND_EMAIL",
            ),)
        )
        self.runtime.run("send")
        snapshot = self.runtime.mission_snapshot(mission_id)
        self.assertNotIn("PRIVATE_PASSWORD_AND_EMAIL", repr(snapshot))
        self.assertFalse(snapshot["goal_verified"])


if __name__ == "__main__":
    unittest.main()
