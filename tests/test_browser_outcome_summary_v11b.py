"""Offline contracts for evidence-grounded browser status reporting."""
import json
import unittest
from types import SimpleNamespace
from jarvis_agent.browser_outcome_summary import verified_navigation_note


def action(name, payload, success=True):
    return SimpleNamespace(name=name, detail=json.dumps(payload), success=success)


class BrowserOutcomeSummaryTests(unittest.TestCase):
    def test_exact_destination_comes_from_verified_post_observation(self):
        real = "https://example.test/gestion_joueurs/players/"
        note = verified_navigation_note("Clique sur Joueurs, vérifie la page, explique.", [
            action("browser_find", {"matches": [{"name": "Joueurs"}]}),
            action("browser_click", {
                "verified": True, "postcondition": "navigation_observed",
                "post_observation": {"tab": {"title": "Tableau des Joueurs", "url": real}}
            }),
        ], "La page est …/gestionjoueurs/players/")
        self.assertIn(real, note)
        self.assertIn("Navigation vérifiée", note)
        self.assertNotIn("gestionjoueurs", note)

    def test_no_unsupported_success_claim(self):
        for evidence in (
            {"verified": False, "postcondition": "navigation_observed",
             "post_observation": {"tab": {"url": "https://example.test"}}},
            {"verified": True, "postcondition": "click_dispatched_requires_verify",
             "post_observation": {"tab": {"url": "https://example.test"}}},
            {"verified": True, "postcondition": "navigation_observed",
             "post_observation": {"tab": {"url": "chrome://settings"}}},
        ):
            self.assertEqual(verified_navigation_note(
                "Vérifie la page", [action("browser_click", evidence)]), "")

    def test_not_added_without_status_request_or_if_url_already_exact(self):
        action_result = action("browser_click", {
            "verified": True, "postcondition": "navigation_observed",
            "post_observation": {"tab": {"url": "https://example.test/landing"}}
        })
        self.assertEqual(verified_navigation_note("Clique sur le lien", [action_result]), "")
        self.assertEqual(verified_navigation_note("Vérifie la page", [action_result],
                          "https://example.test/landing"), "")

    def test_other_tool_and_failed_result_cannot_prove_navigation(self):
        evidence = {"verified": True, "postcondition": "navigation_observed",
                    "post_observation": {"tab": {"url": "https://example.test"}}}
        self.assertEqual(verified_navigation_note("Vérifie",
                         [action("browser_find", evidence)]), "")
        self.assertEqual(verified_navigation_note("Vérifie",
                         [action("browser_click", evidence, success=False)]), "")


if __name__ == "__main__":
    unittest.main()
