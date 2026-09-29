import unittest
from unittest.mock import patch

from jarvis_agent.mission import Mission, MissionEngine, MissionStatus, MissionStep, StepStatus
from jarvis_agent.registry import ToolRegistry
from jarvis_agent.tools import ToolResult


class MissionEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = MissionEngine(ToolRegistry())

    @patch("jarvis_agent.mission.execute")
    def test_multi_step_mission_runs_in_order(self, execute_mock):
        execute_mock.side_effect = [
            ToolResult(True, "ok", "chrome"),
            ToolResult(True, "ok", "search"),
        ]
        mission = Mission(
            objective="Ouvre Chrome et recherche les agents IA",
            steps=[
                MissionStep("app.open", {"app": "chrome"}),
                MissionStep("browser.search", {"query": "agents IA"}),
            ],
        )

        outcome = self.engine.execute(mission)

        self.assertTrue(outcome.success)
        self.assertEqual(mission.status, MissionStatus.SUCCEEDED)
        self.assertEqual(execute_mock.call_count, 2)
        self.assertEqual(mission.steps[0].status, StepStatus.SUCCEEDED)
        self.assertEqual(mission.steps[1].status, StepStatus.SUCCEEDED)

    @patch("jarvis_agent.mission.execute")
    def test_failure_stops_remaining_steps(self, execute_mock):
        execute_mock.return_value = ToolResult(False, "no", "missing")
        mission = Mission(
            objective="test",
            steps=[
                MissionStep("app.open_named", {"query": "VLC Media Player"}),
                MissionStep("browser.search", {"query": "should not run"}),
            ],
        )

        outcome = self.engine.execute(mission)

        self.assertFalse(outcome.success)
        self.assertEqual(execute_mock.call_count, 1)
        self.assertEqual(mission.steps[0].status, StepStatus.FAILED)
        self.assertEqual(mission.steps[1].status, StepStatus.SKIPPED)

    @patch("jarvis_agent.mission.execute")
    def test_planner_app_open_vlc_is_canonicalized(self, execute_mock):
        execute_mock.return_value = ToolResult(True, "ok", "vlc")
        mission = Mission(
            objective="Ouvre VLC",
            steps=[MissionStep("app.open", {"app": "VLC Media Player"})],
        )

        outcome = self.engine.execute(mission)

        self.assertTrue(outcome.success)
        called_intent = execute_mock.call_args.args[0]
        self.assertEqual(called_intent.name, "app.open_named")


if __name__ == "__main__":
    unittest.main()
