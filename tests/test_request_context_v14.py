from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import GroqResponsesAgent, CerebrasResponsesAgent, AgentRuntimeUnavailable
from jarvis_agent.config import settings
from jarvis_agent.kernel_contracts import MissionStatus
from jarvis_agent.request_context import RequestContext
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.provider_rate_budget import groq_fallback_preflight
from tests.test_agent_runtime import FakeTools


def answer(text):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(content=text, tool_calls=[]))], usage=None)


def observation(name, identity, text, **extra):
    return [{"role": "assistant", "tool_calls": [{"id": identity, "type": "function",
                "function": {"name": name, "arguments": "{}"}}]},
            {"role": "tool", "name": name, "tool_call_id": identity,
             "content": json.dumps({"success": True, "detail": {"text": text, **extra}})}]


class RequestContextTests(unittest.TestCase):
    def agent(self, cls=GroqResponsesAgent):
        agent = cls(FakeTools())
        agent.api_key = "offline-fixture"
        agent.request_context = RequestContext(enabled=True)
        client = Mock()
        client.chat.completions.create.return_value = answer("Voici la réponse.")
        agent._client = client
        return agent, client

    def test_new_mission_request_excludes_old_history_without_deleting_transcript(self):
        agent, client = self.agent()
        agent._messages += [{"role": "user", "content": "OLD_PRIVATE_MISSION_" * 3000},
                            {"role": "assistant", "content": "Ancienne réponse"}]
        agent.start_request_scope()
        with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
            agent.run("Explique le fonctionnement général.")
        request = client.chat.completions.create.call_args.kwargs
        self.assertNotIn("OLD_PRIVATE_MISSION_", json.dumps(request))
        self.assertIn("OLD_PRIVATE_MISSION_", json.dumps(agent._messages))
        self.assertTrue(agent.request_context.last_request["admitted"])

    def test_regular_followup_keeps_scope_and_previous_human_constraints(self):
        agent, _ = self.agent()
        anchor = {"role": "user", "content": "Observe puis explique, sans envoyer de message."}
        agent._messages += [anchor, {"role": "assistant", "content": "Le dossier est ouvert."},
                            {"role": "user", "content": "Continue."}]
        agent._request_scope_anchor = anchor
        self.assertEqual(agent._messages_for_request()[1:], agent._messages[1:])
        agent._trim_history()
        self.assertIn("sans envoyer", json.dumps(agent._messages_for_request()))

    def test_implicit_mission_cleanup_keeps_real_conversation_followup(self):
        agent, client = self.agent()
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
            runtime = LiveMissionContinuityRuntime(agent, base_dir=Path(folder))
            runtime.run("Explique la notion de courant alternatif.")
            self.assertIsNone(runtime.active_mission_id)
            self.assertFalse(agent._request_scope_pending)
            runtime.run("Développe cette réponse.")
        sent = client.chat.completions.create.call_args.kwargs
        self.assertIn("courant alternatif", json.dumps(sent))
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_scope_change_cancels_old_pending_consent_without_dispatch(self):
        agent, _ = self.agent()
        agent._pending_function_approval = {"name": "open_application", "arguments": {"name": "old"},
                                            "call_id": "old", "deferred_calls": []}
        agent.start_request_scope()
        self.assertIsNone(agent._pending_function_approval)
        self.assertFalse(agent.tools.calls)
        receipt = json.loads(agent._messages[-1]["content"])
        self.assertFalse(receipt["detail"]["dispatched"])
        self.assertEqual(receipt["detail"]["guard"], "previous_mission_approval_cancelled")

    def test_oversized_primary_request_is_rejected_before_actual_client_call(self):
        agent, client = self.agent()
        agent._messages = [{"role": "system", "content": "Keep constraints"},
                           {"role": "user", "content": "Never discard this human instruction. " * 2000}]
        with self.assertRaisesRegex(AgentRuntimeUnavailable, "request_context_budget"), \
             patch.dict(os.environ, {"JARVIS_REQUEST_ESTIMATED_TOKEN_BUDGET": "1500"}):
            agent._chat()
        client.chat.completions.create.assert_not_called()

    def test_profile_context_cap_applies_even_without_efficient_context_optin(self):
        from jarvis_agent.active_mission_supervisor import _SCOPE
        from jarvis_agent.agent_profiles import defaults
        agent, client = self.agent()
        agent.request_context.enabled = False
        profile = {"id": "browser", "revision": 1, "settings": {**defaults("browser"), "context_tokens": 1500}}
        token = _SCOPE.set(SimpleNamespace(profile=profile))
        try:
            with self.assertRaisesRegex(AgentRuntimeUnavailable, "request_context_budget"):
                agent._fit_provider_request([{"role": "user", "content": "preserved " * 3000}], [],
                    provider="fixture", completion_tokens=512)
        finally:
            _SCOPE.reset(token)
        client.chat.completions.create.assert_not_called()

    def test_desktop_pressure_pack_keeps_observation_write_and_shortcut(self):
        context = RequestContext(enabled=True)
        names = ["inspect_active_window", "write_ui_element", "press_key", "open_application"]
        schemas = [{"type": "function", "function": {"name": name}} for name in names]
        messages = observation("inspect_active_window", "inspect", "target")
        selected = context.fallback_tools(schemas, messages, pressure_minimal=True)
        self.assertEqual({s["function"]["name"] for s in selected}, set(names) - {"open_application"})

    def test_historical_read_archive_remains_recoverable_across_multiple_passes(self):
        context = RequestContext(enabled=True)
        messages = [{"role": "system", "content": "Preserve explicit constraints"},
                    *observation("browser_observe_dom", "old", "old" * 2000),
                    {"role": "user", "content": "Inspecte le document actuel"},
                    *observation("browser_observe_dom", "fresh", "fresh" * 700)]
        before = deepcopy(messages)
        shaped, _ = context.compact(messages, historical_before=3, retain_latest=1, preview_chars=0)
        marker = json.loads(shaped[2]["content"])
        shaped_again, _ = context.compact(shaped, retain_latest=1, preview_chars=0)
        self.assertEqual(context.read_evidence({"evidence_id": marker["evidence_id"]})["content"],
                         before[2]["content"][:3000])
        self.assertEqual(shaped_again[-2:], before[-2:])
        self.assertEqual(messages, before)
        self.assertEqual([m.get("tool_call_id") for m in shaped_again], [m.get("tool_call_id") for m in messages])

    def test_budget_never_shortens_human_permissions_or_unknown_mutations(self):
        agent, client = self.agent()
        messages = [{"role": "system", "content": "No external message without explicit consent"},
                    {"role": "user", "content": "Contrainte humaine " * 1000},
                    *observation("browser_click", "uncertain", "X" * 5000, outcome_unknown=True)]
        before = deepcopy(messages)
        with self.assertRaisesRegex(AgentRuntimeUnavailable, "request_context_budget"):
            agent._fit_provider_request(messages, [], provider="fixture", limit_tokens=1500, completion_tokens=256)
        self.assertEqual(messages, before)
        client.chat.completions.create.assert_not_called()
        self.assertFalse(agent.request_context.last_request["admitted"])

    def test_forced_and_explicit_memory_tools_survive_budget_selection(self):
        context = RequestContext(enabled=True)
        def tool(name, length):
            return {"type": "function", "function": {"name": name, "description": "x" * length,
                    "parameters": {"type": "object", "properties": {}}}}
        messages = [{"role": "user", "content": "Retiens le code demandé"}]
        schemas = [tool("remember_information", 100), tool("browser_observe_dom", 100), tool("unused", 12000)]
        _, selected, admitted = context.fit_request(messages, schemas, limit_tokens=1500, completion_tokens=256,
            forced_tool="browser_observe_dom", preserve_required={"remember_information"})
        self.assertTrue(admitted)
        self.assertEqual({s["function"]["name"] for s in selected}, {"remember_information", "browser_observe_dom"})

    def test_primary_and_groq_fallback_share_new_scope_and_protect_provider_order(self):
        agent, primary = self.agent(CerebrasResponsesAgent)
        anchor = {"role": "user", "content": "Inspecte la page actuelle sans modifier son contenu."}
        agent._messages += [{"role": "user", "content": "OLD_MISSION_" * 4000}, anchor]
        agent._request_scope_anchor = anchor
        agent._request_turn_start_index = len(agent._messages) - 1
        primary.chat.completions.create.side_effect = RuntimeError("429 request_quota_exceeded")
        fallback = Mock()
        fallback.chat.completions.create.return_value = answer("Observation reçue.")
        configured = replace(settings, cerebras_secondary_api_key="", groq_api_key="fixture",
                             cerebras_fallback_to_groq=True)
        with patch("jarvis_agent.agent_runtime.settings", configured), patch("openai.OpenAI", return_value=fallback):
            agent._chat()
        self.assertEqual(primary.chat.completions.create.call_count, 1)
        self.assertEqual(fallback.chat.completions.create.call_count, 1)
        for client in (primary, fallback):
            request = client.chat.completions.create.call_args.kwargs
            self.assertNotIn("OLD_MISSION_", json.dumps(request))
            self.assertIn("sans modifier", json.dumps(request, ensure_ascii=False))

    def test_explicit_memory_write_detaches_blocked_nonmemory_goal_without_model(self):
        agent, client = self.agent()
        with tempfile.TemporaryDirectory() as folder:
            runtime = LiveMissionContinuityRuntime(agent, base_dir=Path(folder))
            mid = runtime.begin_mission("Inspecte les branches puis les demandes de fusion.")
            state, version = runtime.context_store.load(mid)
            state.status = MissionStatus.BLOCKED
            state.pending_action = {"manual_review_required": True, "reason": "mcp_unknown_requires_review"}
            runtime.context_store.save(state, expected_version=version)
            runtime.prepare_mission_turn("Retiens que mon code de test V13 est VEGA-2648.")
            self.assertIsNone(runtime.active_mission_id)
            self.assertTrue(runtime.mission_snapshot(mid)["manual_review_required"])
            self.assertFalse(runtime.mission_snapshot(mid)["goal_verified"])
            client.chat.completions.create.assert_not_called()

    def test_extra_brain_receives_same_isolated_goal_and_declared_tools(self):
        from jarvis_agent.brain_cascade import ExtraBrain
        agent, primary = self.agent(CerebrasResponsesAgent)
        current = {"role": "user", "content": "Observe uniquement sans envoyer de message."}
        agent._messages = [{"role": "system", "content": "Trusted general policy."},
                           {"role": "user", "content": "OLD_PRIVATE" * 9000}, current]
        agent._request_scope_anchor = current
        agent._request_turn_start_index = 2
        primary.chat.completions.create.side_effect = RuntimeError("429 request_quota_exceeded")
        extra = Mock()
        extra.chat.completions.create.return_value = answer("Observation reçue.")
        config = replace(settings, cerebras_secondary_api_key="", cerebras_fallback_to_groq=False)
        brain = ExtraBrain("fixture-extra", "gemini", "fixture-model", "https://example.invalid/v1")
        with patch("jarvis_agent.agent_runtime.settings", config), \
             patch("jarvis_agent.brain_cascade.load_extra_brains", return_value=(brain,)), \
             patch("jarvis_agent.brain_cascade.resolved_api_key", return_value="fixture-key"), \
             patch("openai.OpenAI", return_value=extra):
            agent._chat()
        self.assertEqual(primary.chat.completions.create.call_count, 1)
        extra.chat.completions.create.assert_called_once()
        sent = extra.chat.completions.create.call_args.kwargs
        self.assertNotIn("OLD_PRIVATE", json.dumps(sent))
        self.assertIn("sans envoyer", json.dumps(sent))
        self.assertTrue(sent["tools"])

    def test_mixed_memory_and_external_action_still_requires_mission_interpretation(self):
        agent, client = self.agent()
        text = "Retiens ce code puis envoie-le dans le navigateur."
        client.chat.completions.create.return_value = answer(json.dumps({"kind": "continue", "source_quote": text,
            "superseded": [], "plan": None, "resolved": []}))
        with tempfile.TemporaryDirectory() as folder:
            runtime = LiveMissionContinuityRuntime(agent, base_dir=Path(folder))
            mid = runtime.begin_mission("Inspecte les branches puis les commits.")
            runtime.prepare_mission_turn(text)
            self.assertEqual(runtime.active_mission_id, mid)
            self.assertEqual(client.chat.completions.create.call_args.kwargs["tools"], [])
