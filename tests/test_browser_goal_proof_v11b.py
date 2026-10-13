"""Generic mission certificate tests; offline, no model or browser calls."""
import json
import unittest
from types import SimpleNamespace

from jarvis_agent.browser_goal_proof import verify_browser_navigation_goal


GOAL = ("Ouvre https://example.test/dashboard/, inspecte le menu latéral, "
        "ouvre-le si nécessaire, puis clique sur Joueurs. "
        "Vérifie la page affichée et donne-moi son URL exacte.")
SOURCE = "https://example.test/dashboard/"
DEST = "https://example.test/players/"


def action(name, data, success=True):
    return SimpleNamespace(name=name, success=success, detail=json.dumps(data))


def journey():
    return [
        action("open_url", {"dispatched": True, "scope": ["browser", 7],
                            "verified": False, "postcondition": "navigation_pending"}),
        action("browser_get_active_tab", {"tab_id": 7, "url": SOURCE,
                                          "status": "complete"}),
        action("browser_observe_dom", {"tab": {"tab_id": 7}, "controls": []}),
        action("browser_find", {"scope": ["browser", 7], "unique": False,
                "matches": [], "available_controls": [
                    {"type": "button", "name": "Ouvrir le menu", "ref": "menu-ref"}]}),
        action("browser_click", {"source_ref": "menu-ref", "dispatched": True,
                                 "verified": False}),
        action("browser_find", {"scope": ["browser", 7], "unique": True,
                "matches": [{"type": "link", "name": "Joueurs",
                             "ref": "link-ref", "href": DEST}]}),
        action("browser_click", {"source_ref": "link-ref", "dispatched": True,
                "verified": True, "postcondition": "navigation_observed",
                "post_observation": {"tab": {"tab_id": 7, "url": DEST,
                                             "status": "complete"}}}),
        action("browser_get_active_tab", {"tab_id": 7, "url": DEST,
                                          "status": "complete"}),
    ]


class BrowserGoalProofTests(unittest.TestCase):
    def test_independent_verified_navigation_proves_browser_goal(self):
        proof = verify_browser_navigation_goal(GOAL, journey())
        self.assertIsNotNone(proof)
        self.assertEqual(proof.url, DEST)
        self.assertEqual(proof.target, "Joueurs")
        self.assertTrue(proof.proof_ref.startswith("browser_navigation_verified:"))

    def test_missing_readback_never_proves_goal(self):
        self.assertIsNone(verify_browser_navigation_goal(GOAL, journey()[:-1]))

    def test_unverified_click_or_wrong_target_ref_never_proves_goal(self):
        for key, value in (("verified", False), ("source_ref", "other")):
            data = journey()
            receipt = json.loads(data[6].detail)
            receipt[key] = value
            data[6] = action("browser_click", receipt)
            self.assertIsNone(verify_browser_navigation_goal(GOAL, data))

    def test_wrong_destination_and_tab_never_prove_goal(self):
        for url, tab_id in (("https://example.test/elsewhere", 7), (DEST, 8)):
            data = journey()
            receipt = json.loads(data[-1].detail)
            receipt.update(url=url, tab_id=tab_id)
            data[-1] = action("browser_get_active_tab", receipt)
            self.assertIsNone(verify_browser_navigation_goal(GOAL, data))

    def test_missing_menu_action_blocks_requested_menu_step(self):
        data = journey()
        del data[4]
        self.assertIsNone(verify_browser_navigation_goal(GOAL, data))

    def test_more_than_navigation_or_dispatched_write_never_certifies(self):
        self.assertIsNone(verify_browser_navigation_goal(
            GOAL + " Puis envoie un message.", journey()))
        self.assertIsNone(verify_browser_navigation_goal(
            GOAL, journey() + [action("browser_write", {"verified": True})]))

    def test_model_or_success_flag_alone_never_proves_goal(self):
        self.assertIsNone(verify_browser_navigation_goal(
            GOAL, [action("browser_click", {"verified": True})]))
        self.assertIsNone(verify_browser_navigation_goal(
            GOAL, [*journey()[:-1], action("browser_get_active_tab",
                   {"tab_id": 7, "url": DEST, "status": "complete"}, False)]))


if __name__ == "__main__":
    unittest.main()
