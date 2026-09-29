import unittest
from unittest.mock import patch

from jarvis_agent.agent_core import AgentCore
from jarvis_agent.planner import PlanDecision, PlannedStep
from jarvis_agent.registry import ToolRegistry
from jarvis_agent.tools import ToolIntent, ToolResult


class FakePlanner:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []

    def plan(self, user_text, *, context=""):
        self.calls.append((user_text, context))
        return self.decisions.pop(0)


class AgentCoreTests(unittest.TestCase):
    @patch("jarvis_agent.mission.execute")
    def test_direct_intent_becomes_a_mission(self, execute_mock):
        execute_mock.return_value = ToolResult(True, "ok", "youtube")
        planner = FakePlanner([])
        core = AgentCore(registry=ToolRegistry(), planner=planner)

        result = core.handle(
            "Ouvre YouTube",
            deterministic_intent=ToolIntent(
                "browser.open_url",
                {"url": "https://www.youtube.com"},
            ),
        )

        self.assertEqual(result.kind, "mission")
        self.assertTrue(result.mission.success)
        self.assertEqual(len(result.mission.mission.steps), 1)
        self.assertEqual(len(planner.calls), 0)

    @patch("jarvis_agent.mission.execute")
    def test_planner_can_create_multi_step_mission(self, execute_mock):
        execute_mock.side_effect = [
            ToolResult(True, "ok", "chrome"),
            ToolResult(True, "ok", "search"),
        ]
        planner = FakePlanner([
            PlanDecision(
                kind="mission",
                steps=(
                    PlannedStep("app.open", {"app": "chrome"}),
                    PlannedStep("browser.search", {"query": "agents IA"}),
                ),
            )
        ])
        core = AgentCore(registry=ToolRegistry(), planner=planner)

        result = core.handle(
            "Ouvre Chrome et recherche les agents IA",
            deterministic_intent=ToolIntent("unknown", {"text": "x"}),
        )

        self.assertEqual(result.kind, "mission")
        self.assertTrue(result.mission.success)
        self.assertEqual(execute_mock.call_count, 2)

    def test_clarification_keeps_objective_in_context(self):
        planner = FakePlanner([
            PlanDecision(kind="clarify", question="Quel dossier ?"),
            PlanDecision(kind="answer", message="Merci."),
        ])
        core = AgentCore(registry=ToolRegistry(), planner=planner)

        first = core.handle(
            "Je veux ouvrir un dossier spécifique",
            deterministic_intent=ToolIntent("unknown"),
        )
        self.assertEqual(first.kind, "clarify")

        core.handle(
            "baristas",
            deterministic_intent=ToolIntent("unknown"),
        )
        second_context = planner.calls[1][1]
        self.assertIn("OBJECTIF EN ATTENTE", second_context)
        self.assertIn("Je veux ouvrir un dossier spécifique", second_context)



    def test_reset_session_clears_short_term_context(self):
        planner = FakePlanner([
            PlanDecision(kind="clarify", question="Quel dossier ?"),
        ])
        core = AgentCore(registry=ToolRegistry(), planner=planner)

        core.handle(
            "Je veux ouvrir un dossier spécifique",
            deterministic_intent=ToolIntent("unknown"),
        )
        self.assertIn("OBJECTIF EN ATTENTE", core.context_text())

        core.reset_session()
        self.assertEqual(core.context_text(), "")

if __name__ == "__main__":
    unittest.main()
