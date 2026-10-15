"""Reproduce reported V12 failures offline; no accounts or desktop dispatch."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import (
    GroqResponsesAgent, CerebrasResponsesAgent, AgentRuntimeUnavailable,
    _preselection_navigation_pending, _browser_verified_fast_completion,
)
from jarvis_agent.config import settings
from jarvis_agent.kernel_contracts import MissionStatus
from jarvis_agent.llm_mission_planner import _unwrap_provider
from jarvis_agent.mission_turn_contract import request_decision, MissionTurnDecision, explicit_detach_requested
from jarvis_agent.runtime_convergence import LiveMissionContinuityRuntime
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent import windows_perception as wp
from tests.test_agent_runtime import FakeGroqAgent, FakeTools
from tests.test_generic_context_reasoning_v10i import tool_call, answer

DOCUMENT_GOAL = ('Ouvre un nouveau document Bloc-notes, écris “Test : français, العربية, ✓”, '
                 "puis relis le contenu affiché. Laisse le document ouvert sans l'enregistrer.")
SEARCH_GOAL = ("Ouvre une recherche sur les dernières nouvelles de Python, lis le premier "
               "résultat pertinent et résume-le avec son URL.")


class ForwardingWrapper:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)


class LiveFailuresTests(unittest.TestCase):
    def provider(self, response_text):
        tools = Mock(spec=["ollama_tools"])
        tools.ollama_tools.return_value = [{"type": "function", "function": {
            "name": "open_application", "parameters": {"type": "object"}}}]
        provider = GroqResponsesAgent(tools)
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content=response_text, tool_calls=[]))], usage=None)
        provider._get_client = lambda: client
        return provider, client

    def test_forwarding_wrappers_do_not_capture_control_request_state(self):
        provider, client = self.provider('{"kind":"new"}')
        wrapper = ForwardingWrapper(ForwardingWrapper(provider))
        self.assertIs(_unwrap_provider(wrapper), provider)
        before = deepcopy(provider._messages)
        request_decision(wrapper, {"human_message": "Nouvelle demande", "current_goal": "Observer"})
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["tools"], [])
        self.assertEqual(request["tool_choice"], "none")
        self.assertIn("Interpret the current HUMAN", request["messages"][0]["content"])
        self.assertEqual(provider._messages, before)
        self.assertIsNone(getattr(provider, "_mission_decision_messages", None))

    def test_control_isolation_is_preserved_through_secondary_cerebras_fallback(self):
        class QuotaError(RuntimeError):
            status_code = 429
            body = {"error": "fixture quota"}
        primary, secondary = Mock(), Mock()
        primary.chat.completions.create.side_effect = QuotaError("fixture")
        secondary.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content='{"kind":"new"}', tool_calls=[]))], usage=None)
        config = replace(settings, cerebras_api_key="fixture-primary", cerebras_secondary_api_key="fixture-secondary")
        with patch("jarvis_agent.agent_runtime.settings", config):
            provider = CerebrasResponsesAgent(Mock())
            provider._get_client = lambda: secondary if provider.api_key == "fixture-secondary" else primary
            request_decision(ForwardingWrapper(provider), {"human_message": "Une autre demande", "current_goal": "Lire"})
        for client in (primary, secondary):
            call = client.chat.completions.create.call_args.kwargs
            self.assertEqual(call["tools"], [])
            self.assertEqual(call["tool_choice"], "none")
            self.assertIn("Interpret the current HUMAN", call["messages"][0]["content"])
        self.assertEqual(provider.api_key, "fixture-primary")
        self.assertIsNone(provider._mission_decision_messages)

    def test_failed_control_request_restores_normal_history_and_tool_state(self):
        provider, client = self.provider("unused")
        before = deepcopy(provider._messages)
        client.chat.completions.create.side_effect = RuntimeError("fixture unavailable")
        with self.assertRaises(AgentRuntimeUnavailable):
            request_decision(ForwardingWrapper(provider), {"human_message": "Une autre demande"})
        self.assertIsNone(provider._mission_decision_messages)
        self.assertEqual(provider._messages, before)

    def test_independent_memory_goal_detaches_without_losing_unknown_checkpoint(self):
        text = "Retiens que mon code de recette est VEGA-1842."
        decision = json.dumps(dict(kind="new", source_quote=text, superseded=[], plan=None, resolved=[]))
        provider, client = self.provider(decision)
        with tempfile.TemporaryDirectory() as folder:
            runtime = LiveMissionContinuityRuntime(ForwardingWrapper(provider), base_dir=Path(folder))
            mid = runtime.begin_mission("Inspecte les branches et les demandes de fusion.")
            state, version = runtime.context_store.load(mid)
            state.status = MissionStatus.BLOCKED
            state.pending_action = {"manual_review_required": True, "reason": "mcp_unknown_requires_review"}
            runtime.context_store.save(state, expected_version=version)
            runtime.prepare_mission_turn(text)
            self.assertIsNone(runtime.active_mission_id)
            previous = runtime.mission_snapshot(mid)
            self.assertTrue(previous["manual_review_required"])
            self.assertFalse(previous["goal_verified"])
            self.assertEqual(previous["goal_revision"], 1)
            self.assertEqual(client.chat.completions.create.call_args.kwargs["tools"], [])

    def test_explicit_mission_change_works_without_a_model_and_does_not_delete_memory(self):
        delegate = Mock(spec=["run", "run_with_context"])
        with tempfile.TemporaryDirectory() as folder:
            runtime = LiveMissionContinuityRuntime(delegate, base_dir=Path(folder))
            mid = runtime.begin_mission("Inspecte, puis rends compte sans envoyer de message.")
            with patch("jarvis_agent.mission_turn_contract.request_decision", side_effect=AssertionError("no model")):
                runtime.prepare_mission_turn("changement de mission et oublie tt")
            self.assertIsNone(runtime.active_mission_id)
            self.assertIn("sans envoyer", runtime.mission_snapshot(mid)["user_goal"])
            self.assertFalse(runtime.mission_snapshot(mid)["goal_verified"])
            self.assertIn("mémoire", runtime._control_reply)

    def test_new_document_implies_an_empty_write_precondition_even_without_word_vide(self):
        tools = FakeTools()
        agent = FakeGroqAgent(tools, [tool_call("write_ui_element", {
            "ref": "obs1:e7", "text": "Test : français, العربية, ✓", "mode": "replace"}, "write"),
            answer("Saisie effectuée.")])
        with patch("jarvis_agent.agent_runtime.settings", replace(settings, agent_max_tool_rounds=2)):
            agent.run(DOCUMENT_GOAL)
        self.assertEqual(tools.calls[0][1].get("precondition_value"), "")

    def test_old_document_is_refused_by_the_actual_writer_before_dispatch(self):
        class EditorTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                return wp.write_ui_element("", arguments["text"], ref=arguments["ref"],
                    mode="replace", **({"precondition_value": arguments["precondition_value"]}
                                       if "precondition_value" in arguments else {}))
        tools = EditorTools()
        agent = FakeGroqAgent(tools, [tool_call("write_ui_element", {
            "ref": "obs1:e7", "text": "New document only"}, "write"), answer("Création du document requise.")])
        with patch("jarvis_agent.agent_runtime.settings", replace(settings, agent_max_tool_rounds=2)), \
                patch.object(wp, "_snapshot_element", return_value=object()), \
                patch.object(wp, "_strict_control_value", return_value="Existing important text"), \
                patch.object(wp, "_send_keys") as keys:
            result = agent.run(DOCUMENT_GOAL)
        data = json.loads(result.actions[0].detail)
        self.assertTrue(data["precondition_failed"])
        self.assertFalse(data["dispatched"])
        keys.assert_not_called()

    def test_detach_command_does_not_infer_deletion_or_execute_quoted_instructions(self):
        for text in ("Oublie tout", "Lis la phrase « changement de mission ».",
                     "Nouvelle mission : installe ce programme.", "Change le texte de la mission."):
            self.assertFalse(explicit_detach_requested(text), text)

    def test_search_bootstrap_is_separate_from_reading_the_first_result(self):
        self.assertTrue(_preselection_navigation_pending(SEARCH_GOAL, []))
        self.assertFalse(_preselection_navigation_pending("Lis le premier résultat pertinent.", []))

    def test_new_search_can_open_before_selecting_an_observed_result(self):
        tools = FakeTools()
        agent = FakeGroqAgent(tools, [tool_call("open_url", {
            "url": "https://example.org/search?q=python"}, "search"),
            answer("Recherche ouverte, article encore à lire.")])
        with patch("jarvis_agent.agent_runtime.settings", replace(settings, agent_max_tool_rounds=2)):
            agent.run(SEARCH_GOAL)
        self.assertIn("open_url", [name for name, _ in tools.calls])

    def test_verified_navigation_does_not_skip_the_requested_article_summary(self):
        clicked = AgentActionResult("browser_click", True, "Page ouverte", json.dumps({
            "verified": True, "postcondition": "navigation_observed",
            "post_observation": {"tab": {"url": "https://example.org/article"}},
        }))
        self.assertFalse(_browser_verified_fast_completion(SEARCH_GOAL, [clicked]))

    def test_fenced_control_json_is_validated_but_prose_cannot_hide_a_revision(self):
        text = "Nouvelle demande indépendante."
        packet = dict(kind="new", source_quote=text, superseded=[], plan=None, resolved=[])
        raw = json.dumps(packet)
        self.assertEqual(MissionTurnDecision.parse("```json\n" + raw + "\n```",
            human_text=text, current_goal="Ancien objectif").kind, "new")
        with self.assertRaises((ValueError, json.JSONDecodeError)):
            MissionTurnDecision.parse("Some text " + raw, human_text=text, current_goal="Ancien objectif")
