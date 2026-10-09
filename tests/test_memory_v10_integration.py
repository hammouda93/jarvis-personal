"""Cross-boundary V10 memory contracts, without accounts or live desktop actions."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.active_mission_supervisor import SupervisedToolRegistry, matches_rule, validate_rule
from jarvis_agent.agent_runtime import AgentRuntimeUnavailable, CerebrasResponsesAgent
from jarvis_agent.hermes_reliability import is_mutating
from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.memory_core_store import MemoryCoreStore
from jarvis_agent.memory_tool_scope import compact_memory_fallback
from jarvis_agent.provider_rate_budget import ProviderRateGate


class MemoryV10IntegrationTests(unittest.TestCase):
    def test_sqlite_inventory_can_verify_exact_reviewed_content_not_a_success_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MemoryCoreStore(Path(directory) / "v10-integration.sqlite3")
            source = "Synthetic acceptance note JARVIS-TEST-4827"
            store.remember(source)
            adapter = FoundationToolAdapter(Mock(), memory=store)
            with patch.dict("os.environ", {name: "1" for name in (
                    "JARVIS_MEMORY_CORE_ENABLED", "JARVIS_SEMANTIC_MEMORY_V5_ENABLED",
                    "JARVIS_MEMORY_AGENT_TOOLS_ENABLED")}, clear=False):
                result = adapter.execute("semantic_memory_inspect", {})
            rule = {"tool": "semantic_memory_inspect", "arguments": {},
                    "path": ["items", 0, "raw"], "equals": source}
            validate_rule(rule)
            self.assertTrue(matches_rule(result, rule))
            self.assertFalse(matches_rule(result, {**rule, "equals": "invented note"}))
            with self.assertRaises(ValueError):
                validate_rule({**rule, "path": ["success"], "equals": True})

    def test_verification_accepts_known_local_date_reads_but_not_arbitrary_or_legacy_tools(self):
        rule = {"arguments": {"date": "2026-10-10"}, "path": ["hits", 0, "event_date"],
                "equals": "2026-10-10"}
        for name in ("semantic_memory_events_on_date", "semantic_memory_events_in_range"):
            arguments = ({"date": "2026-10-10"} if name.endswith("on_date") else
                         {"start_date": "2026-10-10", "end_date": "2026-10-12"})
            validate_rule({**rule, "tool": name, "arguments": arguments})
        for name in ("remember_information", "semantic_memory_search", "mcp__server__read_memory",
                     "semantic_memory_unknown_new_tool"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_rule({**rule, "tool": name})

    def test_memory_reads_are_not_mutations_or_implicit_supervisor_approvals(self):
        delegate = Mock()
        delegate.requires_confirmation.return_value = False
        tools = SupervisedToolRegistry(delegate)
        with patch("jarvis_agent.active_mission_supervisor._SCOPE") as context:
            context.get.return_value = Mock()
            for name in ("semantic_memory_search", "semantic_memory_inspect",
                         "semantic_memory_events_on_date", "semantic_memory_events_in_range"):
                with self.subTest(name=name):
                    self.assertFalse(is_mutating(name))
                    self.assertFalse(tools.requires_confirmation(name))
            self.assertTrue(tools.requires_confirmation("remember_information"))
            self.assertTrue(is_mutating("semantic_memory_unknown_new_tool"))

    def test_fallback_keeps_all_user_constraints_and_historical_tool_evidence(self):
        call = {"role": "assistant", "content": "", "tool_calls": [{
            "id": "old_proof", "type": "function",
            "function": {"name": "semantic_memory_inspect", "arguments": "{}"}}]}
        proof = {"role": "tool", "name": "semantic_memory_inspect",
                 "tool_call_id": "old_proof", "content": '{"items":[{"memory_id":8}]}' }
        extra = {"role": "system", "content": "operator privacy constraint"}
        messages = [{"role": "system", "content": "LONG SYSTEM " * 1900}, extra, call, proof]
        for index in range(7):
            messages.extend(({"role": "user", "content": f"constraint {index}"},
                             {"role": "assistant", "content": f"reply {index}"}))
        boundary = len(messages)
        messages.append({"role": "user", "content": "Inspecte ma memoire"})
        original = copy.deepcopy(messages)
        compact = compact_memory_fallback(messages, turn_start=boundary)
        for message in messages:
            if message.get("role") in {"user", "tool"} or message.get("tool_calls"):
                self.assertIn(message, compact)
        self.assertIn(extra, compact)
        self.assertEqual(messages, original)
        self.assertEqual(compact[-1], messages[-1])

    def fallback(self, *, context="", supervised=False, oversized=False, grounding=None, compact_allowed=False):
        agent = CerebrasResponsesAgent.__new__(CerebrasResponsesAgent)
        agent._provider_rate_gate = ProviderRateGate()
        agent._memory_scope_active, agent._memory_write_allowed = True, False
        agent._pending_function_approval = None
        agent._ephemeral_context = context
        agent._session_grounding = grounding or {}
        agent._request_turn_start_index = 1
        messages = [{"role": "system", "content": ("X" * 40000 if oversized else "mission proof and constraints")},
                    {"role": "user", "content": "Inspecte ma memoire"}]
        agent._messages_for_request = lambda: messages
        agent._tool_definitions = lambda **kwargs: []
        config = SimpleNamespace(cerebras_fallback_to_groq=True, groq_api_key="fixture",
            groq_base_url="https://fixture.example", ai_request_timeout_s=15,
            groq_agent_model="fixture", groq_reasoning_effort="low")
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(usage=None)
        with patch("openai.OpenAI", return_value=client), \
                patch("jarvis_agent.agent_runtime.settings", config), \
                patch("jarvis_agent.active_mission_supervisor.execution_scope_active", return_value=supervised), \
                patch("jarvis_agent.active_mission_supervisor.reserve_model_request"), \
                patch("jarvis_agent.active_mission_supervisor.record_model_usage"), \
                patch("jarvis_agent.memory_tool_scope.compact_memory_fallback", wraps=compact_memory_fallback) as compact:
            if oversized and not compact_allowed:
                with self.assertRaises(AgentRuntimeUnavailable):
                    agent._chat_via_groq_fallback(tool_choice="auto", ms_football_only=False, msf_tool_names=None)
                client.chat.completions.create.assert_not_called()
            else:
                agent._chat_via_groq_fallback(tool_choice="auto", ms_football_only=False, msf_tool_names=None)
                sent = client.chat.completions.create.call_args.kwargs["messages"]
                self.assertEqual(sent[1:], messages[1:])
                if compact_allowed:
                    self.assertNotEqual(sent[0], messages[0])
                else:
                    self.assertEqual(sent[0], messages[0])
            if compact_allowed:
                compact.assert_called_once()
            else:
                compact.assert_not_called()

    def test_injected_context_is_not_replaced_by_read_only_memory_fallback(self):
        self.fallback(context="approved goal, source evidence and permissions")

    def test_supervised_mission_context_is_not_replaced_by_memory_fallback(self):
        self.fallback(supervised=True)

    def test_oversized_mission_fails_closed_instead_of_losing_essential_context(self):
        self.fallback(context="approved goal", oversized=True)

    def test_trusted_session_grounding_is_not_lost_by_compaction(self):
        self.fallback(grounding={"opened_file": "approved document"})

    def test_standalone_read_only_memory_fallback_can_still_fit_without_raising_budget(self):
        self.fallback(oversized=True, compact_allowed=True)


if __name__ == "__main__":
    unittest.main()
