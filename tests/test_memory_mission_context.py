"""V5 persistence across fresh runtimes and safe operational delegation."""
from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

import test_semantic_memory_v5 as fixtures
from jarvis_agent.agent_runtime import AgentTurnResult
from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.hermes_reliability import ActionLedger, HermesReliabilityRuntime, ReliabilityToolRegistry
from jarvis_agent.memory_core_store import MemoryCoreStore
from jarvis_agent.memory_semantic_interpreter import ModelSemanticMemoryInterpreter
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.semantic_memory import MemoryQueryFrame, MemoryTurnInterpretation
from jarvis_agent.semantic_memory_runtime import SemanticMemoryEngine, SemanticMemoryRuntime


class ContextMemoryTests(unittest.TestCase):
    build_runtime = fixtures.SemanticMemoryRuntimeTests.build_runtime

    def test_explicit_write_in_live_mission_uses_v5_and_survives_fresh_runtime(self):
        from pathlib import Path
        import tempfile
        text, raw = "Memorise : mon code de test est Zeta", "Mon code de test est Zeta"
        store, interpreter, brain, foundation, memory = self.build_runtime(
            turns={text: MemoryTurnInterpretation(operation="write", write_text=raw, confidence=0.99)},
            projections={raw: (fixtures.projection("reference_codeword", "Zeta"),)})
        with tempfile.TemporaryDirectory() as directory:
            registry = ReliabilityToolRegistry(foundation, ActionLedger(Path(directory) / "ledger"))
            memory.tools = registry
            live = LiveMissionContinuityRuntime(memory, base_dir=Path(directory) / "mission")
            runtime = HermesReliabilityRuntime(live, registry)
            runtime.begin_mission("write and verify synthetic persistent fact")
            with patch.dict("os.environ", {"JARVIS_SEMANTIC_MEMORY_V5_ENABLED": "1"}):
                result = runtime.run(text)
            self.assertEqual([a.name for a in result.actions], ["remember_information"])
            self.assertEqual(brain.calls, 0)
            self.assertEqual(interpreter.turn_calls, 1)
            self.assertEqual([a["name"] for a in runtime.recent_actions()], ["remember_information"])
            # New store, interpreter and runtime: no session transcript/facts.
            reopened = MemoryCoreStore(store.db_path)
            question = "Quel est mon code de test ?"
            fresh_interpreter = fixtures.FixtureInterpreter(turns={question: MemoryTurnInterpretation(
                operation="recall", confidence=0.99, query=MemoryQueryFrame(
                    relation="reference_codeword", answer_mode="single", raw_text=question, confidence=0.99))})
            fresh_brain = fixtures.NoLLM()
            fresh = SemanticMemoryRuntime(fresh_brain,
                FoundationToolAdapter(fixtures.ToolSchemaDelegate(), memory=reopened),
                SemanticMemoryEngine(reopened, fresh_interpreter, min_score=0.45))
            self.assertEqual(fresh._session_facts, [])
            self.assertEqual(fresh.run(question).text, "Zeta")
            self.assertEqual(fresh_brain.calls, 0)

    def context_runtime(self):
        store, interpreter, brain, tools, runtime = self.build_runtime(turns={}, projections={})
        contexts = []
        brain.run_with_context = lambda text, context, **kwargs: contexts.append(context) or AgentTurnResult(text="actual delegated turn")
        return store, interpreter, runtime, contexts

    def test_non_memory_classification_preserves_mission_context(self):
        _, interpreter, runtime, contexts = self.context_runtime()
        runtime.run_with_context("generic work", "original whole objective")
        self.assertEqual(interpreter.turn_calls, 1)
        self.assertEqual(contexts, ["original whole objective"])

    def test_classifier_timeout_cannot_block_non_memory_goal_or_lose_context(self):
        store, interpreter, runtime, contexts = self.context_runtime()
        interpreter.interpret_turn = MagicMock(side_effect=TimeoutError("synthetic local timeout"))
        result = runtime.run_with_context("generic work", "original whole objective")
        self.assertEqual(result.text, "actual delegated turn")
        self.assertEqual(contexts, ["original whole objective"])
        self.assertEqual(store.recent_memories(), [])

    def test_internal_windows_delegation_does_not_reclassify_each_continuation(self):
        _, interpreter, runtime, contexts = self.context_runtime()
        with patch("jarvis_agent.active_mission_supervisor.execution_scope_active", return_value=True), patch(
                "jarvis_agent.active_mission_supervisor.delegation_context", return_value={"agent": "windows"}):
            runtime.run_with_context("continue the reviewed step", "whole objective")
        self.assertEqual(interpreter.turn_calls, 0)
        self.assertEqual(contexts, ["whole objective"])

    def test_memory_responsibility_keeps_semantic_interpreter(self):
        _, interpreter, runtime, _ = self.context_runtime()
        with patch("jarvis_agent.active_mission_supervisor.execution_scope_active", return_value=True), patch(
                "jarvis_agent.active_mission_supervisor.delegation_context", return_value={"agent": "memory"}):
            runtime.run_with_context("generic recall", "whole objective")
        self.assertEqual(interpreter.turn_calls, 1)


class LocalModelResponseTests(unittest.TestCase):
    def invoke(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
        self.http = patch("jarvis_agent.memory_semantic_interpreter.urllib.request.urlopen", return_value=response)
        self.addCleanup(self.http.stop)
        self.urlopen = self.http.start()
        interpreter = ModelSemanticMemoryInterpreter(provider="ollama", model="fixture")
        return interpreter._chat_ollama("classify", "synthetic input")

    def test_local_classifier_avoids_unnecessary_thinking(self):
        self.assertEqual(self.invoke({"message": {"content": '{"operation":"pass"}'}}), '{"operation":"pass"}')
        request = self.urlopen.call_args.args[0]
        self.assertIs(json.loads(request.data)["think"], False)

    def test_valid_but_truncated_json_is_not_treated_as_durable_memory_intent(self):
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            self.invoke({"done_reason": "length", "message": {"content": '{"operation":"write"}'}})

    def test_empty_local_response_is_explicitly_unavailable(self):
        with self.assertRaisesRegex(RuntimeError, "empty"):
            self.invoke({"message": {"content": ""}})


if __name__ == "__main__":
    unittest.main()
