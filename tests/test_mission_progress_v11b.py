"""Read-only generic mission progress reporting, without model calls."""
import json
import unittest
from types import SimpleNamespace

from jarvis_agent.mission_progress import action_evidence_counts, partial_mission_report


def result(name, detail, success=True):
    return SimpleNamespace(name=name, success=success, detail=json.dumps(detail))


class MissionProgressTests(unittest.TestCase):
    def test_verified_final_browser_read_does_not_claim_whole_goal(self):
        actions = [
            result("open_url", {"dispatched": True, "verified": False}),
            result("browser_click", {"dispatched": True, "verified": False}),
            result("browser_get_active_tab", {
                "tab_id": 7, "url": "https://example.test/gestion_joueurs/",
                "status": "complete"}),
        ]
        text = partial_mission_report(actions)
        self.assertIn("Mission encore non certifiée", text)
        self.assertIn("https://example.test/gestion_joueurs/", text)
        self.assertIn("Actions envoyées ou incertaines : 2", text)
        self.assertNotIn("mission terminée", text.lower())

    def test_failures_and_proofs_are_distinct(self):
        actions = [
            result("browser_click", {"verified": True}),
            result("browser_write", {"dispatched": True, "outcome_unknown": True}),
            result("browser_find", {"success": False}, False),
        ]
        self.assertEqual(action_evidence_counts(actions), {
            "verified": 1, "pending": 1, "failed": 1,
        })
        text = partial_mission_report(actions)
        self.assertIn("Actions avec effet vérifié : 1", text)
        self.assertIn("Appels en échec : 1", text)

    def test_no_action_does_not_fabricate_result(self):
        text = partial_mission_report(())
        self.assertIn("aucune action vérifiable", text)
        self.assertNotIn("terminée", text.lower())


if __name__ == "__main__":
    unittest.main()
