"""Offline generic mission-boundary and trace regressions; no real UI/API."""

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from jarvis_agent.config import settings as real_settings
from jarvis_agent.mission_trace_v10h import requested_steps
from jarvis_agent.mission_reasoning_v10i import (
    post_mutation_observation_missing, preterminal_continuation_pending,
)
from jarvis_agent.native_tools import AgentActionResult
from tests.test_agent_runtime import FakeTools, FakeGroqAgent


def action(name, detail=None, success=True):
    return AgentActionResult(
        name=name, success=success, message="test",
        detail=json.dumps(detail or {}, ensure_ascii=False),
    )


def window(*names):
    return {
        "observation_id": "obs3",
        "window": {"title": "Installation d'un logiciel", "type": "Window"},
        "controls": [
            {"type": "Button", "name": label, "enabled": True}
            for label in names
        ],
    }


def call(name, args, id):
    return {"output": [{"type": "function_call", "call_id": id,
                        "name": name, "arguments": json.dumps(args)}]}


def answer(value):
    return {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": value},
    ]}]}


class MissionBoundaryV10KTests(unittest.TestCase):
    def test_numbered_user_steps_are_not_joined_with_prohibition(self):
        req = (
            "Je t'autorise à accepter une licence déjà affichée.\n"
            "Continue ensuite de manière autonome :\n"
            "1. Observe la fenêtre actuelle.\n"
            "2. Sélectionne l'acceptation autorisée.\n"
            "3. Vérifie que « Suivant » est disponible.\n"
            "4. Clique sur « Suivant » et inspecte le nouvel écran.\n"
            "5. Continue avec les réglages déjà autorisés.\n"
            "6. Vérifie chaque nouvel état.\n\n"
            "Important : arrête-toi avant l'action qui lance l'installation.\n"
            "Ne confirme rien de plus.\n"
        )
        steps = requested_steps(req)
        self.assertEqual(len(steps), 6)
        self.assertEqual(steps[0], "Observe la fenêtre actuelle.")
        self.assertIn("Continue avec", steps[4])
        self.assertNotIn("Ne confirme", "".join(steps))
        self.assertNotIn("autorise à accepter", "".join(steps))

    def test_ordinary_compound_request_keeps_historical_clauses(self):
        steps = requested_steps(
            "Ouvre une fenêtre, vérifie sa présence, puis ferme-la "
            "et vérifie qu'elle a disparu."
        )
        self.assertEqual(len(steps), 4)

    def test_real_uia_post_observation_avoids_redundant_llm_round(self):
        req = "Clique sur le contrôle puis vérifie l'écran obtenu."
        evidence = {
            "source_observation_id": "obs2",
            "auto_reinspection": True,
            "requires_fresh_inspection": False,
            "post_observation": window("Suivant", "Annuler"),
        }
        actions = [action("click_ui_element", evidence)]
        self.assertFalse(post_mutation_observation_missing(req, actions))
        self.assertTrue(post_mutation_observation_missing(
            req,
            [action("click_ui_element", {
                **evidence, "post_observation": {
                    **evidence["post_observation"], "observation_id": "obs2",
                },
            })],
        ))
        self.assertTrue(post_mutation_observation_missing(
            req, [action("close_window", {"verified": False})]
        ))

    def test_intermediate_screen_needs_progress_decision_not_auto_click(self):
        req = ("Continue à parcourir les étapes avec les options par défaut. "
               "Arrête-toi avant de lancer l'installation définitive.")
        self.assertTrue(preterminal_continuation_pending(
            req, [action("inspect_active_window", window("Suivant", "Annuler"))]))
        self.assertTrue(preterminal_continuation_pending(
            req, [action("click_ui_element", {
                "post_observation": window("Next", "Back"),
                "source_observation_id": "obs2",
            })]))

    def test_final_button_or_missing_permission_suppresses_continuation(self):
        req = ("Continue à parcourir les étapes. "
               "Arrête-toi avant de lancer l'installation.")
        for buttons in [
            ("Installer", "Précédent"),
            ("Suivant", "Installer"),
            ("Valider",),
        ]:
            with self.subTest(buttons=buttons):
                self.assertFalse(preterminal_continuation_pending(
                    req, [action("inspect_active_window", window(*buttons))]))
        self.assertFalse(preterminal_continuation_pending(
            "Inspecte et explique les options, sans cliquer.",
            [action("inspect_active_window", window("Suivant"))]))
        self.assertFalse(preterminal_continuation_pending(
            req, [action("inspect_active_window", window("Suivant"), False)]))

    def test_runtime_one_bounded_continuation_round(self):
        req = ("Continue à parcourir les étapes autorisées. "
               "Arrête-toi avant de lancer l'installation définitive.")

        class WindowsFake(FakeTools):
            def execute(self, name, args, *, approved=False):
                self.calls.append((name, args))
                if name == "inspect_active_window":
                    return action(name, window("Suivant", "Précédent"))
                if name == "click_ui_element":
                    return action(name, {
                        "source_observation_id": "obs3",
                        "requires_fresh_inspection": False,
                        "auto_reinspection": True,
                        "post_observation": window("Installer", "Précédent"),
                    })
                return action(name)

        tools = WindowsFake()
        model = FakeGroqAgent(tools, [
            call("inspect_active_window", {}, "inspect"),
            answer("Je m'arrête à l'écran intermédiaire."),
            call("click_ui_element", {"ref": "obs3:e5"}, "forward"),
            answer("J'ai atteint l'écran avec le bouton Installer et je m'arrête."),
        ])
        logs = []
        with patch("jarvis_agent.agent_runtime.settings",
                   replace(real_settings, agent_max_tool_rounds=5)):
            turn = model.run(req, log=logs.append)
        self.assertEqual(
            [name for name, _ in tools.calls],
            ["inspect_active_window", "click_ui_element"],
        )
        self.assertTrue(any(
            "[MISSION] recovery=intermediate_screen_before_stop_boundary" in x
            for x in logs
        ))
        self.assertEqual(len(turn.actions), 2)
        # The model never called the terminal install action.
        self.assertNotIn("installer", repr(tools.calls).lower())


if __name__ == "__main__":
    unittest.main()
