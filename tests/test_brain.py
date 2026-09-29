import unittest

from jarvis_agent.brain import AgentDecision, decision_to_intent
from jarvis_agent.tools import route


class BrainSafetyTests(unittest.TestCase):
    def test_general_question_is_not_a_pc_action(self):
        intent = route("Qui es-tu ?")
        self.assertEqual(intent.name, "unknown")

    def test_ai_can_request_search(self):
        decision = AgentDecision(
            kind="tool",
            tool="browser.search",
            args={"query": "agents IA"},
        )
        intent = decision_to_intent(decision)
        self.assertIsNotNone(intent)
        self.assertEqual(intent.name, "browser.search")
        self.assertEqual(intent.args["query"], "agents IA")

    def test_ai_cannot_open_unapproved_application(self):
        decision = AgentDecision(
            kind="tool",
            tool="app.open",
            args={"app": "powershell"},
        )
        self.assertIsNone(decision_to_intent(decision))

    def test_ai_cannot_use_non_http_url(self):
        decision = AgentDecision(
            kind="tool",
            tool="browser.open_url",
            args={"url": "file:///C:/Windows/System32"},
        )
        self.assertIsNone(decision_to_intent(decision))


if __name__ == "__main__":
    unittest.main()
