"""Offline contracts: a tool call is not a verified mission outcome."""
from __future__ import annotations

import json
import unittest

from jarvis_agent.mission_evidence_v10g import guard_unverified_mission_reply
from jarvis_agent.native_tools import AgentActionResult


def action(name, *, success=True, detail=""):
    return AgentActionResult(name=name, success=success, message="test", detail=detail)


class MissionEvidenceV10GTests(unittest.TestCase):
    def test_observation_does_not_count_as_a_click(self):
        observed = action("inspect_active_window", detail='{"window":"installer"}')
        text, reason = guard_unverified_mission_reply(
            "Continue la mission en cours", "Je clique sur Suivant.",
            [observed], continuation=True,
        )
        self.assertEqual(reason, "action_claim_without_dispatch")
        self.assertIn("pas exécuté", text)

    def test_actual_click_can_be_reported_without_claiming_final_goal(self):
        clicked = action("click_ui_element", detail='{"verified":true}')
        answer, reason = guard_unverified_mission_reply(
            "Clique sur Suivant", "Je clique sur Suivant.", [clicked],
        )
        self.assertEqual(reason, "")
        self.assertEqual(answer, "Je clique sur Suivant.")

    def test_installation_success_is_not_inferred_from_next_click(self):
        clicked = action(
            "click_ui_element",
            detail=json.dumps({"verified": True, "postcondition": "navigation_observed"}),
        )
        answer, reason = guard_unverified_mission_reply(
            "Installe le logiciel depuis le setup", "L'installation est terminée.",
            [clicked],
        )
        self.assertEqual(reason, "terminal_goal_without_proof")
        self.assertIn("pas encore été vérifié", answer)

    def test_explicit_terminal_attestation_is_accepted(self):
        proof = action("computer_verify", detail=json.dumps({
            "verified": True,
            "goal_verified": True,
            "postcondition": "software_present_and_launchable",
        }))
        answer, reason = guard_unverified_mission_reply(
            "Installe le logiciel", "L'installation est terminée.", [proof],
        )
        self.assertEqual(reason, "")
        self.assertEqual(answer, "L'installation est terminée.")

    def test_mixed_memory_and_window_claim_without_observation(self):
        only_memory = action("semantic_memory_search", detail='{"raw_fallback":[]}')
        answer, reason = guard_unverified_mission_reply(
            "Vérifie la fenêtre et retrouve mon code dans la mémoire",
            "Je clique pour continuer.", [only_memory],
        )
        self.assertEqual(reason, "action_claim_without_dispatch")
        self.assertIn("pas exécuté", answer)

    def test_implicit_window_verification_cannot_be_claimed_from_memory(self):
        only_memory = action("semantic_memory_search", detail='{"raw_fallback":[{"raw":"test"}]}')
        text, reason = guard_unverified_mission_reply(
            "Vérifie qu'elle est ouverte, puis retrouve mon code dans ta mémoire.",
            "Vérification : l'application est bien ouverte et le code est 123.",
            [only_memory],
        )
        self.assertEqual(reason, "external_verification_without_observation")
        self.assertIn("pas effectué de nouvelle observation", text)

    def test_observed_window_allows_true_verification_report(self):
        seen = action("inspect_active_window", detail='{"title":"Application"}')
        text, reason = guard_unverified_mission_reply(
            "Vérifie qu'elle est ouverte et retrouve mon code dans ta mémoire.",
            "L'application est ouverte, et le code est 123.",
            [seen, action("semantic_memory_search")],
        )
        self.assertEqual(reason, "")
        self.assertIn("ouverte", text)

    def test_unrelated_success_does_not_prove_click(self):
        text, reason = guard_unverified_mission_reply(
            "Continue", "Je clique sur Suivant.",
            [action("search_agent_knowledge")],
        )
        self.assertEqual(reason, "action_claim_without_dispatch")

    def test_mutation_cannot_forge_terminal_proof(self):
        fake_click = action("click_ui_element", detail=json.dumps({
            "goal_verified": True, "verified": True,
            "postcondition": "software_present_and_launchable",
        }))
        text, reason = guard_unverified_mission_reply(
            "Installe le logiciel", "L'installation est terminée.", [fake_click],
        )
        self.assertEqual(reason, "terminal_goal_without_proof")

        answer, reason = guard_unverified_mission_reply(
            "Vas-y", "C'est fait, opération terminée.",
            [action("inspect_interface")], continuation=True,
        )
        self.assertEqual(reason, "continuation_without_effect")
        self.assertIn("mission précédente reste en attente", answer)

    def test_readonly_answer_and_true_intermediate_step_stay_unchanged(self):
        text, reason = guard_unverified_mission_reply(
            "Explique-moi ce que contient la mémoire.",
            "J'ai retrouvé deux souvenirs.",
            [action("semantic_memory_inspect")],
        )
        self.assertEqual(reason, "")
        self.assertEqual(text, "J'ai retrouvé deux souvenirs.")

        text, reason = guard_unverified_mission_reply(
            "Installe le logiciel",
            "J'ai cliqué sur Suivant. Il reste à vérifier l'installation.",
            [action("click_ui_element")],
        )
        self.assertEqual(reason, "")
        self.assertIn("Il reste", text)


if __name__ == "__main__":
    unittest.main()
