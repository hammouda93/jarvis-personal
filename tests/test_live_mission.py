import tempfile
import unittest
from pathlib import Path

from jarvis_agent.live_mission import LiveMissionTracker
from jarvis_agent.mission_context_store import MissionContextStore


class LiveMissionBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = MissionContextStore(
            Path(self.tmp.name) / "missions.sqlite3"
        )
        self.tracker = LiveMissionTracker(store)

    def test_youtube_search_context_survives_into_result_followup(self):
        self.tracker.record_direct(
            "Ouvre YouTube",
            "browser.open_url",
            {"url": "https://www.youtube.com"},
            success=True,
            detail="https://www.youtube.com",
            response_text="C'est fait.",
        )
        self.tracker.record_direct(
            "Recherche Messi",
            "browser.search",
            {"query": "messi", "scope": "context"},
            success=True,
            detail="https://www.youtube.com/results?search_query=messi",
            response_text="Je recherche messi sur YouTube.",
        )

        block = self.tracker.prepare_turn(
            "Ouvre la première vidéo"
        )

        self.assertIn("[LIVE_MISSION]", block)
        self.assertIn('"site":"YouTube"', block)
        self.assertIn('"query":"messi"', block)
        self.assertIn('"page_kind":"search_results"', block)
        self.assertIn("Ouvre la première vidéo", block)

    def test_browser_fast_paths_share_one_live_mission(self):
        first = self.tracker.record_direct(
            "Ouvre YouTube",
            "browser.open_url",
            {"url": "https://www.youtube.com"},
            success=True,
            detail="https://www.youtube.com",
        )
        first_id = self.tracker.view().mission_id

        second = self.tracker.record_direct(
            "Recherche Messi",
            "browser.search",
            {"query": "messi", "scope": "context"},
            success=True,
            detail="https://www.youtube.com/results?search_query=messi",
        )
        second_id = self.tracker.view().mission_id

        self.assertEqual(first_id, second_id)
        self.assertIn("YouTube", first + second)

    def test_unrelated_non_browser_turn_is_not_forced_into_browser_context(self):
        self.tracker.record_direct(
            "Ouvre YouTube",
            "browser.open_url",
            {"url": "https://www.youtube.com"},
            success=True,
            detail="https://www.youtube.com",
        )

        block = self.tracker.prepare_turn("Quel temps fait-il demain ?")

        self.assertEqual(block, "")


if __name__ == "__main__":
    unittest.main()
