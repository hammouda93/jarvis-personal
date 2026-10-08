"""Live-wrapper regression: Semantic Memory V5 and Hermes ledger share a turn safely."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jarvis_agent.active_mission_supervisor import SupervisedToolRegistry
from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.hermes_reliability import (
    ActionLedger, HermesReliabilityRuntime, ReliabilityToolRegistry,
)
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent.semantic_memory import MemoryTurnInterpretation
from jarvis_agent.semantic_memory_runtime import SemanticMemoryRuntime


class _NativeTools:
    def __init__(self):
        self.calls = []

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, dict(arguments), approved))
        return AgentActionResult(
            name=name, success=True, message="observed",
            detail='{"verified":true}',
        )


class _Model:
    def run(self, user_text, *, log=None, phase=None):
        return SimpleNamespace(text="response:" + user_text, actions=())


class _PassMemoryInterpreter:
    def __init__(self, tools):
        self.tools = tools
        self.calls = 0

    def interpret_turn(self, user_text, *, pending_query=None, log=None):
        self.calls += 1
        # This observation must be recorded in the SAME outer reliability turn
        # and must not create a new ledger turn when memory prepares its context.
        observed = self.tools.execute("get_memory_stats", {})
        if not observed.success:
            raise AssertionError("test observation was denied")
        return MemoryTurnInterpretation(
            operation="pass", confidence=0.99, reason="fixture_pass",
        )


class MemoryHermesTurnContractTests(unittest.TestCase):
    def test_live_text_turn_preserves_memory_context_and_outer_ledger_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            foundation = FoundationToolAdapter(_NativeTools())
            registry = ReliabilityToolRegistry(foundation, ActionLedger(Path(directory)))
            supervised = SupervisedToolRegistry(registry)
            interpreter = _PassMemoryInterpreter(supervised)
            memory = SemanticMemoryRuntime(_Model(), supervised, interpreter)
            runtime = HermesReliabilityRuntime(memory, registry)

            reply = runtime.run("Bonjour Jarvis")
            self.assertEqual(reply.text, "response:Bonjour Jarvis")
            self.assertEqual(foundation.current_user_text, "Bonjour Jarvis")
            self.assertEqual(interpreter.calls, 1)
            self.assertEqual(
                [row["name"] for row in runtime.recent_actions()],
                ["get_memory_stats"],
            )
            self.assertEqual(foundation.delegate.calls, [
                ("get_memory_stats", {}, False),
            ])

    def test_text_preparation_does_not_reset_failure_or_turn_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            foundation = FoundationToolAdapter(_NativeTools())
            tools = ReliabilityToolRegistry(foundation, ActionLedger(Path(directory)))
            outer_id = tools.begin_turn()
            tools._local.failures = {"known": 1}
            self.assertIsNone(tools.begin_turn("a memory request"))
            self.assertEqual(tools._local.turn_id, outer_id)
            self.assertEqual(tools._local.failures, {"known": 1})
            self.assertEqual(foundation.current_user_text, "a memory request")
            tools.end_turn()
            self.assertNotEqual(tools.begin_turn(), outer_id)


if __name__ == "__main__":
    unittest.main()
