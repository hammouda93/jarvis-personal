"""Replay the V14 live failures through real request shaping and SQLite.

Only model responses and operating-system effects are simulated. The runtime,
capability catalogue, budget admission, conversation and local memory are real.
"""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import (
    AgentRuntimeUnavailable, CerebrasResponsesAgent, GroqResponsesAgent,
)
from jarvis_agent.config import settings
from jarvis_agent.foundation_tools import FoundationRuntime, FoundationToolAdapter
from jarvis_agent.memory_core_store import MemoryCoreStore
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent.request_context import ContextToolRegistry, RequestContext
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from tests.test_agent_runtime import FakeTools
from tests.test_memory_direct_agent_v10f import DIRECT, MinimalNativeRegistry


def response(text="", name=None, arguments=None, identity="call"):
    calls = [] if name is None else [SimpleNamespace(
        id=identity, type="function", function=SimpleNamespace(
            name=name, arguments=json.dumps(arguments or {}))) ]
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(content=text, tool_calls=calls))], usage=None)


def definition(name, description="Generic capability"):
    return {"type": "function", "function": {"name": name,
        "description": description, "parameters": {
            "type": "object", "properties": {}, "required": []}}}


class WideCatalogue(FakeTools):
    """A small requested capability competing with expensive unrelated schemas."""
    def __init__(self, requested="open_url"):
        super().__init__()
        self.requested = requested

    def ollama_tools(self):
        return [definition(self.requested), *[
            definition(name, "Original unrelated schema detail. " * 300)
            for name in ("list_windows", "list_applications", "open_folder",
                         "inspect_active_window", "semantic_memory_inspect")]]

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, deepcopy(arguments)))
        return AgentActionResult(name, True, "Effect observed", json.dumps({
            "verified": True, "dispatched": True, "outcome_unknown": False,
            "postcondition": "navigation_observed",
        }))


class DailyUseRegressionTests(unittest.TestCase):
    def test_selected_capability_reaches_actual_wire_then_dispatches_once(self):
        for agent_type in (GroqResponsesAgent, CerebrasResponsesAgent):
            for tool in ("open_url", "open_file", "find_file"):
                with self.subTest(provider=agent_type.__name__, tool=tool):
                    self._exercise_selection(agent_type, tool)

    def _exercise_selection(self, agent_type, tool, *, fallback=None):
        native = WideCatalogue(tool)
        context = RequestContext(enabled=True)
        agent = agent_type(ContextToolRegistry(native, context))
        agent.request_context = context
        agent.api_key = "offline-test"
        requests, exposed = [], []
        client = Mock()
        def model(**request):
            requests.append(deepcopy(request))
            names = {s["function"]["name"] for s in request["tools"] if "function" in s}
            exposed.append(set(context.exposed))
            if len(requests) == 1:
                self.assertNotIn(tool, names, "Fixture must exercise schema pressure")
                return response(name="request_tool_capabilities", arguments={"names": [tool]}, identity="select")
            if len(requests) == 2:
                self.assertIn(tool, names, "Selected schema was lost before the API request")
                return response(name=tool, arguments={}, identity="effect")
            return response("Résultat observé dans cet essai.")
        client.chat.completions.create.side_effect = model
        agent._client = client
        env = {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1",
               "JARVIS_REQUEST_ESTIMATED_TOKEN_BUDGET": "7000",
               "JARVIS_GROQ_FALLBACK_ESTIMATED_TPM_BUDGET": "7000"}
        with patch.dict(os.environ, env):
            if fallback:
                primary = Mock()
                primary.chat.completions.create.side_effect = RuntimeError("429 request_quota_exceeded")
                agent._client = primary
                configured = replace(settings, cerebras_secondary_api_key="", groq_api_key="offline-test",
                                     cerebras_fallback_to_groq=fallback == "groq")
                with patch("jarvis_agent.agent_runtime.settings", configured), \
                     patch("openai.OpenAI", return_value=client):
                    result = agent.run("Utilise la capacité demandée pour cette opération.")
                self.assertEqual(primary.chat.completions.create.call_count, 1)
            else:
                result = agent.run("Utilise la capacité demandée pour cette opération.")
        self.assertEqual(native.calls, [(tool, {})])
        self.assertEqual([a.name for a in result.actions], ["request_tool_capabilities", tool])
        self.assertNotEqual(result.pause_reason, "capability_selection_no_progress")
        self.assertLessEqual(len(requests), 3)
        for request, names in zip(requests, exposed):
            self.assertEqual(names, {s["function"]["name"] for s in request["tools"] if "function" in s})

    def test_groq_fallback_keeps_selected_schema_and_primary_cooldown(self):
        self._exercise_selection(CerebrasResponsesAgent, "open_url", fallback="groq")

    def test_extra_brain_keeps_selected_schema(self):
        from jarvis_agent.brain_cascade import ExtraBrain
        brain = ExtraBrain("offline-extra", "gemini", "offline-model", "https://example.invalid/v1",
                           max_estimated_tokens=7000)
        with patch("jarvis_agent.brain_cascade.load_extra_brains", return_value=(brain,)), \
             patch("jarvis_agent.brain_cascade.resolved_api_key", return_value="offline-test"):
            self._exercise_selection(CerebrasResponsesAgent, "open_file", fallback="extra")

    def test_oversized_selected_schema_stops_before_api_instead_of_reselecting_forever(self):
        context = RequestContext(enabled=True)
        schemas = [definition("open_file", "Protected schema " * 3000)]
        context.prepare_tools(schemas)
        context.select_tools({"names": ["open_file"]})
        tools = FakeTools()
        tools.ollama_tools = lambda: schemas
        agent = GroqResponsesAgent(tools)
        agent.api_key, agent.request_context, agent._client = "offline-test", context, Mock()
        with patch.dict(os.environ, {"JARVIS_REQUEST_ESTIMATED_TOKEN_BUDGET": "7000"}), \
             self.assertRaisesRegex(AgentRuntimeUnavailable, "request_context_budget_exhausted"):
            agent._chat()
        agent._client.chat.completions.create.assert_not_called()
        self.assertFalse(tools.calls)

    def test_saved_fact_and_receipt_are_in_next_real_model_request(self):
        for contextual in (False, True):
            with self.subTest(contextual=contextual), tempfile.TemporaryDirectory() as folder, \
                 patch.dict(os.environ, {**DIRECT, "JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
                store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
                adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
                agent = CerebrasResponsesAgent(adapter)
                agent.api_key = "offline-test"
                agent.request_context = RequestContext(enabled=True)
                agent._client = Mock()
                agent._client.chat.completions.create.return_value = response("Contexte reçu.")
                runtime = LiveMissionContinuityRuntime(FoundationRuntime(agent, adapter),
                                                       base_dir=Path(folder) / "missions")
                fact = "retiens que je vais regarder le film Weekend le 20 octobre 2026"
                agent.start_request_scope()
                saved = (runtime.run_with_context(fact, "Local observation") if contextual else runtime.run(fact))
                agent._client.chat.completions.create.assert_not_called()
                self.assertTrue(saved.actions[0].success)
                self.assertEqual([item.content for item in store.all_memories()], [fact])
                runtime.run("ouvres youtube et recherche le nom du film que je viens de t'annoncer")
                request = agent._client.chat.completions.create.call_args.kwargs
                self.assertIn({"role": "user", "content": fact}, request["messages"])
                self.assertIn('"memory_id"', "\n".join(m.get("content", "") for m in request["messages"]))
                self.assertIn("2026", json.dumps(request["messages"]))
                self.assertFalse(agent._request_scope_pending)
                self.assertEqual(len(store.all_memories()), 1)

    def test_failed_memory_readback_stays_in_conversation_without_false_success(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, DIRECT):
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
            agent = GroqResponsesAgent(adapter)
            runtime = FoundationRuntime(agent, adapter)
            fact = "Retiens que mon film est Weekend."
            with patch.object(store, "get_memory", side_effect=OSError("read unavailable")):
                result = runtime.run(fact)
            self.assertFalse(result.actions[0].success)
            self.assertIn({"role": "user", "content": fact}, agent._messages)
            self.assertIn("success=0", agent._messages[-1]["content"])
            self.assertIn('"outcome_unknown": true', agent._messages[-1]["content"])
            self.assertEqual(len(store.all_memories()), 1)

    def test_automatic_compound_mission_keeps_recent_fact_after_history_boundary(self):
        from jarvis_agent.auto_mission import prepare
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
                **DIRECT, "JARVIS_EFFICIENT_CONTEXT_ENABLED": "1", "JARVIS_AUTO_MISSION_PLANNING": "0"}):
            store = MemoryCoreStore(Path(folder) / "memory.sqlite3")
            adapter = FoundationToolAdapter(MinimalNativeRegistry(), memory=store)
            agent = CerebrasResponsesAgent(adapter)
            agent.api_key = "offline-test"
            agent.request_context = RequestContext(enabled=True)
            agent._client = Mock()
            agent._client.chat.completions.create.return_value = response("Contexte reçu.")
            runtime = LiveMissionContinuityRuntime(FoundationRuntime(agent, adapter),
                                                   base_dir=Path(folder) / "missions")
            runtime.run("Retiens que je vais regarder le film Weekend le 20 octobre 2026.")
            goal = "Ouvre YouTube et recherche le film que je viens de t'annoncer."
            admission = prepare(runtime, goal)
            self.assertTrue(admission.track)
            runtime.run(goal)
            messages = agent._client.chat.completions.create.call_args.kwargs["messages"]
            content = "\n".join(m.get("content", "") for m in messages)
            self.assertIn("Weekend", content)
            self.assertIn("2026", content)
            self.assertEqual(len(store.all_memories()), 1)
            runtime.reset()
            self.assertNotIn("Weekend", json.dumps(agent._messages_for_request()))
            self.assertEqual(len(store.all_memories()), 1)


if __name__ == "__main__":
    unittest.main()
