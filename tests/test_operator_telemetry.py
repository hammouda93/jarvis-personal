"""Operator telemetry and UI regression tests, no real desktop or provider calls."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.hermes_reliability import ActionLedger
from jarvis_agent.operator_telemetry import (
    _readonly, runtime_model_snapshot, snapshot,
)
from jarvis_agent.operator_console import render_snapshot


class OperatorTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.actions_root = self.root / "actions"
        self.missions_root = self.root / "mission"
        self.missions_root.mkdir(parents=True)
        self.env = patch.dict(os.environ, {
            "JARVIS_HERMES_RELIABILITY_ENABLED": "1",
            "JARVIS_HERMES_RELIABILITY_DIR": str(self.actions_root),
            "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "1",
            "JARVIS_RUNTIME_CONVERGENCE_DIR": str(self.missions_root),
            "JARVIS_KERNEL_SHADOW_USER_ID": "owner-1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_no_db_is_created_by_dashboard_reader(self):
        state = snapshot()
        self.assertEqual(state["actions"], [])
        self.assertEqual(state["missions"], [])
        self.assertFalse((self.actions_root / "actions.sqlite3").exists())
        self.assertFalse((self.missions_root / "mission_context.sqlite3").exists())

    def test_real_action_journal_is_rendered_without_secret_args(self):
        ledger = ActionLedger(self.actions_root)
        action_id, _ = ledger.begin(
            turn_id="turn1", name="browser_write",
            arguments={"text": "TOP-SECRET PRIVATE PHONE", "tab_id": 4},
        )
        ledger.finish(action_id, success=True, verified=True)
        panel = snapshot(max_items=5)
        self.assertEqual(panel["actions"][0]["tool"], "browser_write")
        self.assertEqual(panel["actions"][0]["status"], "succeeded")
        self.assertTrue(panel["actions"][0]["verified"])
        self.assertNotIn("TOP-SECRET", repr(panel))
        self.assertTrue((self.actions_root / "actions.sqlite3").is_file())
        self.assertEqual(len(_readonly(ledger.db, "SELECT action_id FROM actions")), 1)

    def test_current_and_other_user_mission_isolation(self):
        db = self.missions_root / "mission_context.sqlite3"
        with sqlite3.connect(db) as cx:
            cx.execute(
                "CREATE TABLE mission_contexts (mission_id TEXT, goal_summary TEXT, "
                "status TEXT, updated_at REAL, state_json TEXT, user_id TEXT)"
            )
            cx.executemany(
                "INSERT INTO mission_contexts VALUES (?,?,?,?,?,?)",
                [
                    ("m1", "open a window", "waiting_external", 8.0,
                     '{"observed_state":{"goal_verified":false},"pending_action":{}}', "owner-1"),
                    ("m2", "another user private goal", "blocked", 9.0,
                     '{"pending_action":{"manual_review_required":true}}', "other"),
                ],
            )
        with sqlite3.connect(self.missions_root / "task_graphs.sqlite3") as cx:
            cx.execute("CREATE TABLE mission_task_graphs (mission_id TEXT, graph_json TEXT)")
            cx.execute(
                "INSERT INTO mission_task_graphs VALUES (?,?)",
                ("m1", '{"nodes":[{"task_id":"turn_00001","status":"completed",'
                 '"capability":"interaction.live_turn",'
                 '"result":{"action_names":["browser_click"]}}]}'),
            )
        panel = snapshot()
        self.assertEqual(len(panel["missions"]), 1)
        self.assertEqual(panel["missions"][0]["id"], "m1")
        self.assertFalse(panel["missions"][0]["verified"])
        self.assertEqual(panel["tasks"][0]["tools"], ["browser_click"])
        self.assertNotIn("private goal", repr(panel))

    def test_disabled_features_hide_stored_historical_details(self):
        ledger = ActionLedger(self.actions_root)
        aid, _ = ledger.begin(turn_id="t", name="open_url", arguments={"url": "https://example.org"})
        ledger.finish(aid, success=True)
        with patch.dict(os.environ, {
            "JARVIS_HERMES_RELIABILITY_ENABLED": "0",
            "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "0",
        }):
            data = snapshot()
        self.assertFalse(data["reliability_enabled"])
        self.assertEqual(data["actions"], [])
        self.assertEqual(data["missions"], [])
        self.assertEqual(data["unresolved_visible"], 0)

    def test_html_escapes_untrusted_mission_and_tool_text(self):
        malicious = "<img src=x onerror=alert(1)>"
        data = {
            "mission_enabled": True,
            "reliability_enabled": True,
            "missions": [{
                "id": "m<script>", "goal": malicious, "status": "waiting_external",
                "verified": False, "needs_review": True,
            }],
            "tasks": [{"name": malicious, "capability": malicious,
                       "status": "running", "tools": [malicious]}],
            "actions": [{"tool": malicious, "status": "unknown",
                         "id": malicious, "verified": False}],
        }
        html = "".join(render_snapshot(data))
        self.assertIn("&lt;img", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("<script>", html)
        self.assertIn("objectif non prouvé", html)
        self.assertIn("INCERTAIN", html)

    def test_model_counters_are_measured_no_fake_usage(self):
        brain = SimpleNamespace(
            provider_name="cerebras", reliability_round_budget=8,
            reliability_rounds_used=3,
            reliability_last_failure_category="rate_limit",
            reliability_last_usage={
                "input_tokens": 50,
                "output_tokens": 20,
                "total_tokens": 70,
            },
        )
        runtime = SimpleNamespace(delegate=SimpleNamespace(delegate=brain))
        state = runtime_model_snapshot(runtime)
        self.assertEqual(state["provider"], "cerebras")
        self.assertEqual(state["rounds_used"], 3)
        self.assertEqual(state["rounds_limit"], 8)
        self.assertEqual(state["failure_category"], "rate_limit")
        self.assertEqual(state["reported_usage"]["total_tokens"], 70)
        self.assertIsNone(runtime_model_snapshot(object())["rounds_limit"])


if __name__ == "__main__":
    unittest.main()
