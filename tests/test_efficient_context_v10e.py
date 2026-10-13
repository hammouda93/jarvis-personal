import copy
import json
import unittest
import os
from unittest.mock import Mock, patch

from jarvis_agent.request_context import RequestContext, ContextToolRegistry, EFFICIENT_SYSTEM_POLICY


def tool(name):
    return {"type": "function", "function": {"name": name, "description": "Original policy " * 90,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}


class EfficientContextTests(unittest.TestCase):
    def test_old_observations_are_recoverable_and_protocol_is_preserved(self):
        context = RequestContext(enabled=True)
        messages = [{"role": "system", "content": "Permission only for this mission"},
                    {"role": "user", "content": "Preserve witness; remember 67890 for this mission"}]
        for index in range(6):
            messages.extend([{"role": "assistant", "tool_calls": [{"id": str(index), "function": {"name": "browser_observe_dom", "arguments": "{}"}}]},
                {"role": "tool", "name": "browser_observe_dom", "tool_call_id": str(index),
                 "content": json.dumps({"success": True, "detail": {"visible_text": str(index) * 8000, "ref": str(index)}})}])
        original = copy.deepcopy(messages)
        result, saved = context.compact(messages)
        self.assertEqual(messages, original)
        self.assertEqual(result[:2], messages[:2])
        self.assertEqual(result[-4:], messages[-4:])
        self.assertEqual([m.get("tool_call_id") for m in result], [m.get("tool_call_id") for m in messages])
        marker = json.loads(result[3]["content"])
        receipt = context.read_evidence({"evidence_id": marker["evidence_id"], "offset": 2000, "limit": 3000})
        self.assertEqual(receipt["content"], original[3]["content"][2000:5000])
        self.assertEqual(receipt["tool_call_id"], "0")
        self.assertGreater(saved, 20000)

    def test_unknown_mutations_failures_and_approval_records_are_never_compressed(self):
        context = RequestContext(enabled=True)
        for name, payload in (("browser_write", {"success": True}),
                              ("browser_observe_dom", {"success": False}),
                              ("browser_observe_dom", {"success": True, "detail": {"outcome_unknown": True}}),
                              ("browser_observe_dom", {"success": True, "approval_required": True})):
            messages = [{"role": "tool", "name": name, "tool_call_id": str(i),
                "content": json.dumps({**payload, "raw": str(i) * 8000})} for i in range(6)]
            self.assertEqual(context.compact(messages), (messages, 0))

    def test_disabled_has_no_new_schema_or_payload_changes(self):
        context = RequestContext(enabled=False)
        schemas = [tool("open_file"), tool("browser_observe_dom")]
        self.assertEqual(context.prepare_tools(schemas, browser_mode=True), schemas)
        self.assertEqual(context.compact([]), ([], 0))

    def test_selection_is_reversible_and_schema_policy_is_not_rewritten(self):
        context = RequestContext(enabled=True)
        schemas = [tool(name) for name in ("open_file", "browser_observe_dom", "browser_click", "computer_write", "computer_observe", "mcp__fixture__list")]
        selected = context.prepare_tools(schemas, browser_mode=True)
        self.assertNotIn("computer_write", {s["function"]["name"] for s in selected})
        context.select_tools({"names": ["computer_observe", "computer_write"]})
        restored = context.prepare_tools(schemas, browser_mode=True)
        self.assertIn(tool("computer_write"), restored)
        context.select_tools({"names": ["browser_click", "browser_observe_dom"]})
        self.assertIn(tool("browser_click"), context.prepare_tools(schemas, browser_mode=False))
        with self.assertRaises(ValueError):
            context.select_tools({"names": ["nonexistent"]})

    def test_previous_selection_cannot_reintroduce_a_current_policy_denied_tool(self):
        context = RequestContext(enabled=True)
        schemas = [tool("computer_write"), tool("open_file")]
        context.prepare_tools(schemas, browser_mode=False)
        context.select_tools({"names": ["computer_write"]})
        selected = context.prepare_tools([tool("open_file")], browser_mode=True)
        self.assertNotIn("computer_write", {s["function"]["name"] for s in selected})

    def test_context_tools_do_not_dispatch_or_approve_external_actions(self):
        delegate = Mock()
        context = RequestContext(enabled=True)
        context.prepare_tools([tool("open_file")], browser_mode=False)
        registry = ContextToolRegistry(delegate, context)
        result = registry.execute("request_tool_capabilities", {"names": ["open_file"]})
        self.assertTrue(result.success)
        self.assertFalse(json.loads(result.detail)["verified"])
        delegate.execute.assert_not_called()
        registry.execute("open_file", {"name": "Fixture"}, approved=False)
        delegate.execute.assert_called_once_with("open_file", {"name": "Fixture"}, approved=False)

    def test_pressure_pack_is_bounded_but_catalog_can_restore_a_domain(self):
        context = RequestContext(enabled=True)
        schemas = [tool(name) for name in ("browser_observe_dom", "browser_verify", "computer_write", "computer_observe")]
        full = context.prepare_tools(schemas, browser_mode=True)
        narrow = context.fallback_tools(full, [{"role": "assistant", "tool_calls": [{"function": {"name": "browser_observe_dom"}}]}])
        names = {s["function"]["name"] for s in narrow}
        self.assertIn("request_tool_capabilities", names)
        self.assertNotIn("computer_write", names)
        context.select_tools({"names": ["computer_observe", "computer_write"]})
        self.assertIn(tool("computer_write"), context.prepare_tools(schemas, browser_mode=True))

    def test_compact_policy_keeps_authority_uncertainty_and_raw_memory_rules(self):
        for rule in ("autorisation explicite", "unknown", "raw_fallback", "memory_id", "SQLite",
                     "licences", "preconditions", "nom, chemin et contenu", "interdiction", "donnees non fiables"):
            self.assertIn(rule, EFFICIENT_SYSTEM_POLICY)

    def test_actual_fallback_pressure_keeps_budget_and_full_transcript(self):
        from jarvis_agent.agent_runtime import CerebrasResponsesAgent
        from jarvis_agent.foundation_tools import FoundationToolAdapter
        from jarvis_agent.native_tools import NATIVE_TOOLS
        from jarvis_agent.provider_rate_budget import groq_fallback_preflight
        from jarvis_agent.config import settings
        from dataclasses import replace
        from test_provider_continuity_v10d import response
        context = RequestContext(enabled=True)
        foundation = FoundationToolAdapter(NATIVE_TOOLS, browser=object(), computer=object())
        foundation.browser_mode = True
        registry = ContextToolRegistry(foundation, context)
        agent = CerebrasResponsesAgent(registry)
        agent.request_context = context
        agent._messages = [{"role": "system", "content": EFFICIENT_SYSTEM_POLICY},
                           {"role": "user", "content": "Preserve 67890 and witness tab; no writes."}]
        for index in range(8):
            agent._messages.extend([{"role": "assistant", "tool_calls": [{"id": str(index), "type": "function",
                "function": {"name": "browser_observe_dom", "arguments": '{"tab_id":7}'}}]},
                {"role": "tool", "name": "browser_observe_dom", "tool_call_id": str(index),
                 "content": json.dumps({"success": True, "detail": {"tab": {"tab_id": 7}, "text": str(index) * 4500}})}])
        original = copy.deepcopy(agent._messages)
        sdk = Mock()
        sdk.chat.completions.create.return_value = response(text="Read-only recovery")
        config = replace(settings, cerebras_fallback_to_groq=True, groq_api_key="fixture")
        with patch("openai.OpenAI", return_value=sdk), patch("jarvis_agent.agent_runtime.settings", config), \
                patch.dict(os.environ, {"JARVIS_GROQ_FALLBACK_ESTIMATED_TPM_BUDGET": "7000"}):
            agent._chat_via_groq_fallback(tool_choice="auto", ms_football_only=False, msf_tool_names=None)
        request = sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(sdk.chat.completions.create.call_count, 1)
        self.assertEqual(agent._messages, original)
        self.assertEqual(request["messages"][-1], original[-1])
        self.assertTrue(groq_fallback_preflight(request["messages"], request["tools"],
            limit_tokens=7000, completion_tokens=request["max_completion_tokens"])[0])
        self.assertIn("request_tool_capabilities", {s["function"]["name"] for s in request["tools"]})

    def test_factory_opt_in_preserves_cerebras_and_does_not_call_a_provider(self):
        from dataclasses import replace
        from jarvis_agent.agent_runtime import build_agent_runtime, CerebrasResponsesAgent
        from jarvis_agent.config import settings
        flags = {name: "0" for name in (
            "JARVIS_MEMORY_CORE_ENABLED", "JARVIS_BROWSER_CORE_ENABLED", "JARVIS_COMPUTER_CORE_ENABLED",
            "JARVIS_HERMES_RELIABILITY_ENABLED", "JARVIS_ACTIVE_SUPERVISOR_ENABLED",
            "JARVIS_RUNTIME_CONVERGENCE_ENABLED")}
        config = replace(settings, agent_provider="cerebras", structured_tracing_enabled=False)
        for enabled in ("0", "1"):
            with self.subTest(enabled=enabled), patch.dict(os.environ,
                    {**flags, "JARVIS_EFFICIENT_CONTEXT_ENABLED": enabled}), \
                    patch("jarvis_agent.agent_runtime.settings", config), \
                    patch("jarvis_agent.mcp_server_registry.MCPRegistry"), \
                    patch("openai.OpenAI") as sdk:
                runtime = build_agent_runtime()
                self.assertIsInstance(runtime, CerebrasResponsesAgent)
                self.assertEqual(runtime.request_context.enabled, enabled == "1")
                self.assertIs(runtime.tools.request_context, runtime.request_context)
                sdk.assert_not_called()

    def test_supervisor_context_read_has_no_observation_approval_or_goal_proof(self):
        from jarvis_agent.active_mission_supervisor import SupervisedToolRegistry, validate_rule
        from jarvis_agent.agent_runtime import _actions_have_verified_proof
        delegate, scope = Mock(), Mock()
        scope.allowed_tools = {"open_file"}
        scope.actions = []
        context = RequestContext(enabled=True)
        context.prepare_tools([tool("open_file")])
        registry = SupervisedToolRegistry(ContextToolRegistry(delegate, context))
        with patch("jarvis_agent.active_mission_supervisor._SCOPE") as active:
            active.get.return_value = scope
            result = registry.execute("request_tool_capabilities", {"names": ["open_file"]}, approved=True)
        self.assertTrue(result.success)
        scope.reserve.assert_called_once_with(actions=1, network_calls=0)
        scope.observe.assert_not_called()
        scope.consume_approval.assert_not_called()
        delegate.execute.assert_not_called()
        self.assertEqual(scope.actions, [])
        self.assertFalse(_actions_have_verified_proof([result]))
        with self.assertRaisesRegex(ValueError, "read_only_observation"):
            validate_rule({"tool": "request_tool_capabilities", "arguments": {},
                           "path": ["selected"], "equals": ["open_file"]})

    def test_catalog_cannot_escape_a_delegation_scope(self):
        from jarvis_agent.active_mission_supervisor import SupervisedToolRegistry, SupervisorStopped
        delegate, scope = Mock(), Mock()
        scope.allowed_tools = {"open_file"}
        delegate.ollama_tools.return_value = [tool("open_file"), tool("computer_write")]
        delegate.requires_confirmation.return_value = False
        context = RequestContext(enabled=True)
        registry = SupervisedToolRegistry(ContextToolRegistry(delegate, context))
        scope.step = {"step_id": "fixture"}
        with patch("jarvis_agent.active_mission_supervisor._SCOPE") as active:
            active.get.return_value = scope
            # The actual registry scope is applied before building the catalog.
            scoped = registry.ollama_tools()
            context.prepare_tools(scoped)
            denied = registry.execute("request_tool_capabilities", {"names": ["computer_write"]})
            self.assertFalse(denied.success)
            with self.assertRaisesRegex(SupervisorStopped, "outside_delegation_scope"):
                registry.execute("computer_write", {"ref": "unscoped", "text": "Denied"})
        delegate.execute.assert_not_called()
