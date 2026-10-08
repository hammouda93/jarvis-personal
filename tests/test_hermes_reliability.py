"""Regression tests for Hermes-inspired reliability, without Hermes installed."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.hermes_reliability import (
    ActionLedger, HermesReliabilityRuntime, ReliabilityToolRegistry,
    classify_api_failure, is_mutating,
)
from jarvis_agent.native_tools import AgentActionResult


def action(name, success=True, detail=""):
    return AgentActionResult(
        name=name, success=success, message="observed", detail=detail
    )


class FakeTools:
    def __init__(self):
        self.calls = []
        self.result = None
        self.error = None

    def execute(self, name, args, *, approved=False):
        self.calls.append((name, dict(args), approved))
        if self.error:
            raise self.error
        return self.result or action(name)


class FakeRuntime:
    def __init__(self):
        self.calls = []

    def run(self, text, *, log=None, phase=None):
        self.calls.append(("run", text))
        return SimpleNamespace(text="ok", actions=())

    def run_with_context(self, text, context, *, log=None, phase=None):
        self.calls.append(("context", text, context))
        return SimpleNamespace(text="ok", actions=())

    def reset(self):
        self.calls.append(("reset",))

    def warm_up(self, *, log=None):
        return "warm"


class HermesReliabilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ledger = ActionLedger(self.root)
        self.delegate = FakeTools()
        self.tools = ReliabilityToolRegistry(self.delegate, self.ledger)
        self.turn_id = self.tools.begin_turn()

    def test_exact_argument_digest_never_stores_message_or_secret(self):
        secret = "EMAIL_MESSAGE_private_password_and_person"
        result = self.tools.execute("browser_write", {"text": secret, "tab_id": 7})
        self.assertTrue(result.success)
        rows = self.ledger.recent(turn_id=self.turn_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "succeeded")
        self.assertNotIn(secret.encode(), self.ledger.db.read_bytes())
        self.assertNotIn(secret.encode(), (self.root / "action_hmac.key").read_bytes())

    def test_legacy_result_is_returned_by_identity_and_exactly_one_execution(self):
        result = action("click_ui_element", True, '{"verified": true}')
        self.delegate.result = result
        self.assertIs(self.tools.execute("click_ui_element", {"ref": "obs1:e2"}), result)
        self.assertEqual(len(self.delegate.calls), 1)
        row = self.ledger.recent(turn_id=self.turn_id)[0]
        self.assertEqual(row["verified"], 1)
        self.assertEqual(row["status"], "succeeded")

    def test_tool_exception_creates_durable_unknown_and_blocks_repeat(self):
        self.delegate.error = TimeoutError("lost action response")
        with self.assertRaises(TimeoutError):
            self.tools.execute("browser_click", {"tab_id": 5, "ref": "obs2:e5"})
        self.delegate.error = None
        self.tools.end_turn()
        reopened = ReliabilityToolRegistry(FakeTools(), ActionLedger(self.root))
        reopened.begin_turn()
        blocked = reopened.execute("browser_click", {"ref": "obs2:e5", "tab_id": 5})
        self.assertFalse(blocked.success)
        self.assertIn("prior_outcome_unknown", blocked.detail)
        self.assertEqual(reopened.delegate.calls, [])
        self.assertEqual(self.ledger.recent(turn_id=self.turn_id)[0]["status"], "unknown")

    def test_reported_unknown_is_never_silent_success(self):
        self.delegate.result = action(
            "browser_press", True, '{"outcome_unknown": true, "verified": false}'
        )
        first = self.tools.execute("browser_press", {"key": "Enter", "tab_id": 4})
        self.assertFalse(first.success)
        self.assertIn("tool_reported_outcome_unknown", first.detail)
        next_turn = ReliabilityToolRegistry(FakeTools(), ActionLedger(self.root))
        next_turn.begin_turn()
        second = next_turn.execute("browser_press", {"tab_id": 4, "key": "Enter"})
        self.assertFalse(second.success)
        self.assertEqual(next_turn.delegate.calls, [])

    def test_manual_observation_releases_only_matching_uncertain_action(self):
        self.delegate.error = ConnectionError("disconnect")
        args = {"target": "recipient", "message": "hello"}
        with self.assertRaises(ConnectionError):
            self.tools.execute("computer_write", args)
        row = self.ledger.recent(turn_id=self.turn_id)[0]
        with self.assertRaises(ValueError):
            self.ledger.resolve_unknown(row["action_id"], independently_observed="no_effect", evidence_ref="")
        with self.assertRaises(ValueError):
            self.ledger.resolve_unknown(row["action_id"], independently_observed="guess", evidence_ref="proof")
        self.ledger.resolve_unknown(
            row["action_id"], independently_observed="no_effect",
            evidence_ref="independent-window-observation",
        )
        self.delegate.error = None
        self.assertTrue(self.tools.execute("computer_write", args).success)
        self.assertEqual(len(self.delegate.calls), 2)
        with self.assertRaises(RuntimeError):
            self.ledger.resolve_unknown(row["action_id"], independently_observed="no_effect", evidence_ref="another")

    def test_failed_action_is_not_unknown_and_successful_retry_is_possible(self):
        self.delegate.result = action("open_application", False)
        first = self.tools.execute("open_application", {"name": "custom app"})
        self.assertFalse(first.success)
        self.delegate.result = action("open_application", True)
        self.assertTrue(self.tools.execute("open_application", {"name": "custom app"}).success)
        self.assertEqual(len(self.delegate.calls), 2)

    def test_repeated_identical_failures_are_bounded_without_hardcoding_apps(self):
        self.delegate.result = action("inspect_interface", False)
        for _ in range(2):
            self.assertFalse(self.tools.execute("inspect_interface", {"title": "unknown app"}).success)
        blocked = self.tools.execute("inspect_interface", {"title": "unknown app"})
        self.assertFalse(blocked.success)
        self.assertIn("repeated_exact_failure", blocked.detail)
        self.assertEqual(len(self.delegate.calls), 2)
        self.assertFalse(self.tools.execute("inspect_interface", {"title": "other"}).success)
        self.assertEqual(len(self.delegate.calls), 3)

    def test_failed_prewrite_aborts_before_any_effect(self):
        with patch.object(self.ledger, "begin", side_effect=sqlite3.OperationalError("disk full")):
            reply = self.tools.execute("click_ui_element", {"ref": "obs3:e6"})
        self.assertFalse(reply.success)
        self.assertEqual(self.delegate.calls, [])
        self.assertIn("checkpoint_unavailable", reply.detail)

    def test_unknown_tools_are_mutating_by_default(self):
        self.assertTrue(is_mutating("new_unfamiliar_tool"))
        self.assertFalse(is_mutating("browser_observe_dom"))

    def test_api_error_classification_is_diagnostic_only(self):
        self.assertEqual(classify_api_failure("HTTP 429 retry after"), "rate_limit")
        self.assertEqual(classify_api_failure("HTTP 401"), "authentication")
        self.assertEqual(classify_api_failure("HTTP 503 overloaded"), "transient_provider")
        self.assertEqual(classify_api_failure("request timeout"), "transient_transport")

    def test_wrapped_runtime_preserves_text_context_and_reset(self):
        fake = FakeRuntime()
        wrapped = HermesReliabilityRuntime(fake, self.tools)
        self.assertEqual(wrapped.run("bonjour").text, "ok")
        self.assertEqual(wrapped.run_with_context("continue", "mission context").text, "ok")
        self.assertEqual(fake.calls, [
            ("run", "bonjour"), ("context", "continue", "mission context"),
        ])
        self.assertEqual(wrapped.recent_actions(), [])
        self.assertEqual(wrapped.warm_up(), "warm")
        wrapped.reset()
        self.assertEqual(fake.calls[-1], ("reset",))

    def test_factory_default_off_and_opt_in_are_explicit(self):
        from jarvis_agent import agent_runtime
        settings = SimpleNamespace(
            agent_provider="ollama",
            structured_tracing_enabled=False,
            kernel_shadow_user_id="user-a",
        )
        fake = FakeRuntime()
        flags = {
            "JARVIS_HERMES_RELIABILITY_ENABLED": "0",
            "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "0",
            "JARVIS_HERMES_RELIABILITY_DIR": str(self.root),
            "JARVIS_MEMORY_CORE_ENABLED": "0",
            "JARVIS_BROWSER_CORE_ENABLED": "0",
            "JARVIS_COMPUTER_CORE_ENABLED": "0",
        }
        with (
            patch.object(agent_runtime, "settings", settings),
            patch.object(agent_runtime, "OllamaToolAgent", return_value=fake),
            patch.dict(os.environ, flags),
        ):
            self.assertIs(agent_runtime.build_agent_runtime(), fake)
            os.environ["JARVIS_HERMES_RELIABILITY_ENABLED"] = "1"
            wrapped = agent_runtime.build_agent_runtime()
            self.assertIsInstance(wrapped, HermesReliabilityRuntime)
            self.assertIs(wrapped.delegate, fake)
            self.assertEqual(wrapped.run("hello").text, "ok")
            self.assertEqual(fake.calls, [("run", "hello")])

    def test_budgeted_cerebras_never_retries_or_switches_model_itself(self):
        from jarvis_agent.agent_runtime import AgentRuntimeUnavailable, CerebrasResponsesAgent
        from jarvis_agent.hermes_provider_budget import HermesBudgetedCerebrasAgent
        with patch.dict(os.environ, {"JARVIS_HERMES_MODEL_ROUND_BUDGET": "2"}):
            brain = HermesBudgetedCerebrasAgent(self.tools)
        with patch.object(CerebrasResponsesAgent, "_chat", return_value="response") as upstream:
            self.assertEqual(brain._chat(), "response")
            self.assertEqual(brain._chat(), "response")
            with self.assertRaises(AgentRuntimeUnavailable):
                brain._chat()
            self.assertEqual(upstream.call_count, 2)
            self.assertEqual(brain.reliability_last_failure_category, "logical_round_budget")
        with patch.object(CerebrasResponsesAgent, "_chat", side_effect=AgentRuntimeUnavailable("429 quota")):
            brain.reliability_rounds_used = 0
            with self.assertRaises(AgentRuntimeUnavailable):
                brain._chat()
            self.assertEqual(brain.reliability_last_failure_category, "rate_limit")


if __name__ == "__main__":
    unittest.main()
