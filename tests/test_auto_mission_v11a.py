"""Offline admission tests. No account, browser or external action."""
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.auto_mission import decide, prepare


class AutomaticMissionTests(unittest.TestCase):
    def test_simple_request_preserves_fast_path(self):
        for text in ("Bonjour", "Ouvre Chrome.", "Merci"):
            self.assertFalse(decide(text).track)

    def test_generic_multistep_and_research_are_detected(self):
        self.assertTrue(decide("Retrouve un document puis inspecte son contenu.").track)
        self.assertTrue(decide("Fais une recherche approfondie des sources officielles.").plan)
        self.assertTrue(decide("1. Observe la fenêtre\n2. Vérifie le résultat").track)

    def test_normal_conversation_needs_no_upfront_plan(self):
        self.assertFalse(decide("Pourquoi les applications consomment de la RAM ?").track)

    def test_prepare_starts_and_plans_without_approval_or_tool_execution(self):
        agent = Mock()
        agent.active_mission_id = None
        agent.begin_mission.return_value = "live_fixture"
        agent.generate_semantic_plan.return_value = {
            "step_count": 3, "unresolved_count": 0}
        events = []
        with patch.dict("os.environ", {"JARVIS_AUTO_MISSION_PLANNING": "1"}):
            result = prepare(agent, "Cherche les sources puis compare-les.", log=events.append)
        self.assertTrue(result.track)
        agent.begin_mission.assert_called_once()
        agent.generate_semantic_plan.assert_called_once_with()
        self.assertTrue(any("PLAN_DRAFTED" in event for event in events))
        agent.run.assert_not_called()
        agent.approve_supervised_plan.assert_not_called()

    def test_existing_mission_keeps_single_owner(self):
        agent = Mock()
        agent.active_mission_id = "live_existing"
        result = prepare(agent, "Ouvre un fichier puis inspecte-le.")
        self.assertFalse(result.track)
        agent.begin_mission.assert_not_called()

    def test_planner_failure_is_not_a_failed_action_or_retry(self):
        agent = Mock()
        agent.active_mission_id = None
        agent.begin_mission.return_value = "live_fixture"
        agent.generate_semantic_plan.side_effect = RuntimeError("secret test data")
        events = []
        prepare(agent, "Analyse les options puis compare les résultats.", log=events.append)
        self.assertTrue(any("PLAN_UNAVAILABLE" in event for event in events))
        self.assertFalse(any("secret test data" in event for event in events))


if __name__ == "__main__":
    unittest.main()
