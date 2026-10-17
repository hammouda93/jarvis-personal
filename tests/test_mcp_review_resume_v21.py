"""Reproduce the user's successful journal review + blocked next turn."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.kernel_contracts import MissionStatus
from jarvis_agent.mcp_control import MCPCommand
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.task_graph import TaskStatus


class Delegate:
    def __init__(self):
        self.calls = 0
        self.result = None
    def run(self, *args, **kwargs):
        self.calls += 1
        return self.result
    def run_with_context(self, *args, **kwargs):
        return self.run(*args, **kwargs)


class ReviewResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = MCPRegistry(self.root / "mcp.json")
        self.registry.add_http("gmail", "https://example.test/mcp")
        self.registry.set_enabled("gmail", True)
        self.registry.discover("gmail", [{"name": name, "input_schema": {"type": "object", "properties": {}}}
            for name in ("gmail_find_email", "gmail_send_email")])
        self.registry.note_successful_discovery("gmail")
        self.registry.set_auto_readonly("gmail", True)
        self.registry.allow_tool("gmail", "gmail_send_email", True)
        self.registry.set_runtime_enabled(True)
        self.delegate = Delegate()
        self.runtime = LiveMissionContinuityRuntime(self.delegate, base_dir=self.root / "missions")
        self.mid = self.runtime.begin_mission("Vérifie le compte et mes non-lus")

    def suspend(self, tool="gmail_find_email", *, actual_dispatch=False):
        store = self.registry.activity()
        attempt = store.reserve("gmail", "call", self.registry.get_server("gmail"), tool=tool)
        store.finish(attempt, "unknown")
        detail = {"guard": "mcp_unknown_requires_review", "blocked_by_previous_attempt": True,
            "previous_attempt_id": attempt, "previous_outcome": "unknown", "reviewable": True,
            "dispatched": actual_dispatch, "outcome_unknown": True, "verified": False}
        self.delegate.result = SimpleNamespace(text="Lecture bloquée", pause_reason="mcp_unknown_requires_review",
            actions=(AgentActionResult("mcp__gmail__" + tool, False, "guard", json.dumps(detail)),))
        self.runtime.run("Vérifie le compte et mes non-lus")
        self.assertEqual(self.runtime.context_store.load(self.mid)[0].status, MissionStatus.BLOCKED)
        return attempt

    def test_exact_review_releases_only_non_dispatched_read_waits_for_new_user_turn(self):
        attempt = self.suspend()
        self.assertFalse(self.runtime.release_reviewed_mcp_read("gmail", attempt, self.registry))
        self.registry.activity().review_outcome("gmail", attempt)
        self.assertTrue(self.runtime.release_reviewed_mcp_read("gmail", attempt, self.registry))
        self.assertEqual(self.delegate.calls, 1)  # No automatic replay/model call.
        state = self.runtime.context_store.load(self.mid)[0]
        self.assertEqual(state.status, MissionStatus.WAITING_EXTERNAL)
        self.assertFalse(state.observed_state["goal_verified"])
        self.assertEqual(state.proof_refs, [])
        node = self.runtime.graph_store.load(self.mid).nodes()[0]
        self.assertEqual(node.status, TaskStatus.CANCELLED)
        self.assertNotIn("recovery_verified", node.result)
        self.assertEqual(node.result["action_receipts"][0]["success"], False)
        self.delegate.result = SimpleNamespace(text="Nouvelle lecture demandée", actions=())
        self.runtime.run("Réessaie la lecture du compte")
        self.assertEqual(self.delegate.calls, 2)

    def test_unknown_write_is_never_released_by_read_review_path(self):
        attempt = self.suspend(tool="gmail_send_email")
        self.registry.activity().review_outcome("gmail", attempt)
        self.assertFalse(self.runtime.release_reviewed_mcp_read("gmail", attempt, self.registry))
        self.assertEqual(self.runtime.context_store.load(self.mid)[0].status, MissionStatus.BLOCKED)

    def test_actual_unknown_read_dispatch_still_requires_independent_recovery(self):
        attempt = self.suspend(actual_dispatch=True)
        self.registry.activity().review_outcome("gmail", attempt)
        self.assertFalse(self.runtime.release_reviewed_mcp_read("gmail", attempt, self.registry))

    def test_changed_read_permission_or_wrong_server_cannot_release_mission(self):
        attempt = self.suspend()
        self.registry.activity().review_outcome("gmail", attempt)
        self.assertFalse(self.runtime.release_reviewed_mcp_read("wrong", attempt, self.registry))
        self.registry.set_auto_readonly("gmail", False)
        self.assertFalse(self.runtime.release_reviewed_mcp_read("gmail", attempt, self.registry))

    def test_worker_review_notifies_actual_facade_without_tool_or_llm_dispatch(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        attempt = self.suspend()
        worker = AssistantWorker()
        worker._agent, worker._mcp_registry = self.runtime, self.registry
        results = []
        worker.mcp_control_result.connect(results.append)
        worker._run_mcp_control(MCPCommand("review_outcome", "gmail", str(attempt)))
        self.assertTrue(results[0]["success"])
        self.assertTrue(results[0]["read_guard_released"])
        self.assertEqual(self.delegate.calls, 1)
