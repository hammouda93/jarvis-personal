"""Offline multi-domain planning/recovery regressions; no paid model calls."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.agent_runtime import (
    _browser_verified_fast_completion,
    _compound_result_selection_verified,
)
from jarvis_agent.config import settings as real_settings
from jarvis_agent.mission_reasoning_v10i import (
    recoverable_discovery,
    post_mutation_observation_missing,
)
from jarvis_agent.mission_trace_v10h import requested_steps
from jarvis_agent.native_tools import AgentActionResult
from jarvis_agent.request_context import RequestContext

from tests.test_agent_runtime import FakeGroqAgent, FakeTools


def action(name, success=True, data=None):
    detail = json.dumps(data, ensure_ascii=False) if data is not None else ""
    return AgentActionResult(name=name, success=success, message="test", detail=detail)


def tool_call(name, arguments, call_id):
    return {"output": [{"type": "function_call", "call_id": call_id,
                        "name": name, "arguments": json.dumps(arguments)}]}


def answer(value):
    return {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": value},
    ]}]}


class GenericContextReasoningTests(unittest.TestCase):
    def test_quoted_name_and_four_sequential_goals_survive_provisional_outline(self):
        for goal in (
            "Ouvre la Calculatrice Windows, vérifie qu'elle est ouverte, "
            "puis ferme-la et vérifie qu'elle n'apparaît plus dans les fenêtres ouvertes.",
            'Ouvre la "Calculatrice" Windows, vérifie qu\'elle est ouverte, '
            "puis ferme-la et vérifie qu'elle n'apparaît plus dans les fenêtres ouvertes.",
        ):
            with self.subTest(goal=goal):
                steps = requested_steps(goal)
                self.assertEqual(len(steps), 4, steps)
                self.assertIn("ferme-la", steps[2])
                self.assertIn("vérifie", steps[3])
        self.assertIn('"Calculatrice"', requested_steps(
            'Ouvre la "Calculatrice" Windows.')[0])

    def test_quotes_are_data_not_embedded_commands(self):
        text = 'Lis la phrase "ouvre la fenêtre et ferme le document" sans rien effacer.'
        steps = requested_steps(text)
        self.assertEqual(len(steps), 1)
        self.assertIn('"ouvre la fenêtre', steps[0])
        self.assertNotIn('ferme le document" sans', "".join(steps[1:]))

    def test_failed_discovery_is_a_recoverable_observation_not_a_launch(self):
        failed = action("open_application", False, [
            {"name": "Application locale", "source": "start_apps",
             "score": 0.88, "launch_kind": "aumid"},
        ])
        payload = recoverable_discovery([failed])
        self.assertEqual(payload["candidates"][0]["name"], "Application locale")
        self.assertIn("not execution", payload["next_step"].lower())
        self.assertIsNone(recoverable_discovery([action("open_application")]))
        self.assertIsNone(recoverable_discovery([action("list_windows", False, {})]))

    def test_multidomain_schemas_survive_efficient_browser_context(self):
        registry = [
            {"type": "function", "function": {"name": n,
              "description": n, "parameters": {"type": "object"}}}
            for n in ("open_url", "browser_click", "list_windows", "close_window",
                      "inspect_active_window", "semantic_memory_search",
                      "semantic_memory_inspect", "computer_observe", "open_file")
        ]
        context = RequestContext(enabled=True)
        context.begin_turn()
        shown = context.prepare_tools(registry, browser_mode=True)
        names = {(x.get("function") or {}).get("name") for x in shown}
        for essential in ("close_window", "semantic_memory_search",
                          "inspect_active_window", "open_file"):
            self.assertIn(essential, names)

    def test_after_mutation_recheck_must_not_use_pre_mutation_list(self):
        goal = ("Ouvre un logiciel, vérifie la fenêtre, puis ferme-la "
                "et vérifie qu'elle ne figure plus dans la liste des fenêtres.")
        before = [action("open_application"), action("list_windows"),
                  action("close_window")]
        self.assertTrue(post_mutation_observation_missing(goal, before))
        self.assertFalse(post_mutation_observation_missing(
            goal, before + [action("list_windows")]))
        self.assertFalse(post_mutation_observation_missing(
            "Ferme la fenêtre.", before))

    def test_browser_search_click_does_not_complete_later_ordinal_selection(self):
        goal = ("Ouvre un site, recherche une expression, "
                "puis ouvre le premier résultat.")
        actions = [
            action("open_url", True, {"dispatched": True, "verified": False}),
            action("browser_write", True, {"dispatched": True, "verified": True}),
            action("browser_click", True, {
                "dispatched": True, "verified": True,
                "post_observation": {"tab": {"url": "https://example.org/results"}},
            }),
        ]
        self.assertFalse(_compound_result_selection_verified(actions))
        self.assertFalse(_browser_verified_fast_completion(goal, actions))
        more = actions + [action("browser_click", True, {
            "dispatched": True, "verified": True,
            "post_observation": {"tab": {"url": "https://example.org/item"}},
        })]
        self.assertTrue(_compound_result_selection_verified(more))
        self.assertTrue(_browser_verified_fast_completion(goal, more))

    def test_runtime_recovers_from_observed_candidate_without_app_specific_rules(self):
        class GroundedTools(FakeTools):
            def execute(self, name, args, *, approved=False):
                self.calls.append((name, args))
                if name == "open_application" and args.get("name") != "Application locale":
                    return action(name, False, [
                        {"name": "Application locale", "source": "start_apps",
                         "score": 0.82, "launch_kind": "aumid"},
                    ])
                return action(name, True, {"application": args.get("name")})

        tools = GroundedTools()
        agent = FakeGroqAgent(tools, [
            tool_call("open_application", {"name": "Application"}, "first"),
            answer("Je n'ai pas trouvé d'application à lancer."),
            tool_call("open_application", {"name": "Application locale"}, "recovered"),
            answer("L'ouverture a été demandée à Windows."),
        ])
        with patch("jarvis_agent.agent_runtime.settings",
                   replace(real_settings, agent_max_tool_rounds=5)):
            result = agent.run("Ouvre Application locale.")
        self.assertEqual([name for name, _ in tools.calls],
                         ["open_application", "open_application"])
        self.assertTrue(result.actions[-1].success)

    def test_runtime_requires_post_mutation_window_inventory(self):
        class WindowTools(FakeTools):
            def execute(self, name, args, *, approved=False):
                self.calls.append((name, args))
                return action(name, True)

        tools = WindowTools()
        agent = FakeGroqAgent(tools, [
            tool_call("open_application", {"name": "Application locale"}, "start"),
            tool_call("list_windows", {}, "before"),
            tool_call("close_window", {"title": "Application locale"}, "close"),
            answer("La liste confirme que la fenêtre a disparu."),
            tool_call("list_windows", {}, "after"),
            answer("La fermeture a été suivie d'une nouvelle observation."),
        ])
        goal = ("Ouvre une application, vérifie qu'elle est ouverte, "
                "puis ferme-la et vérifie qu'elle a disparu des fenêtres.")
        with patch("jarvis_agent.agent_runtime.settings",
                   replace(real_settings, agent_max_tool_rounds=7)):
            res = agent.run(goal)
        self.assertEqual([name for name, _ in tools.calls],
                         ["open_application", "list_windows", "close_window", "list_windows"])
        self.assertEqual(len(res.actions), 4)


if __name__ == "__main__":
    unittest.main()
