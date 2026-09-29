import unittest

from jarvis_agent.brain import AgentDecision, decision_to_intent
from jarvis_agent.tools import route


class BrainSafetyTests(unittest.TestCase):
    def test_general_question_is_not_a_pc_action(self):
        intent = route("Qui es-tu ?")
        self.assertEqual(intent.name, "unknown")

    def test_ai_can_request_search_when_user_explicitly_asks(self):
        decision = AgentDecision(
            kind="tool",
            tool="browser.search",
            args={"query": "agents IA"},
        )
        intent = decision_to_intent(
            decision,
            user_text="Recherche sur Internet les agents IA",
        )
        self.assertIsNotNone(intent)
        self.assertEqual(intent.name, "browser.search")
        self.assertEqual(intent.args["query"], "agents IA")

    def test_ai_tool_is_blocked_without_explicit_action_request(self):
        decision = AgentDecision(
            kind="tool",
            tool="browser.open_url",
            args={"url": "https://www.cursor.com"},
        )
        self.assertIsNone(
            decision_to_intent(
                decision,
                user_text="Cursor, www.cursor.com",
            )
        )

    def test_ai_cannot_open_unapproved_application(self):
        decision = AgentDecision(
            kind="tool",
            tool="app.open",
            args={"app": "powershell"},
        )
        self.assertIsNone(
            decision_to_intent(
                decision,
                user_text="Ouvre PowerShell",
            )
        )


    def test_ai_cannot_turn_downloads_request_into_cursor(self):
        decision = AgentDecision(
            kind="tool",
            tool="app.open",
            args={"app": "cursor"},
        )
        self.assertIsNone(
            decision_to_intent(
                decision,
                user_text="Ouvre les chargements",
            )
        )

    def test_ai_can_open_downloads_when_grounded(self):
        decision = AgentDecision(
            kind="tool",
            tool="folder.open",
            args={"folder": "downloads"},
        )
        intent = decision_to_intent(
            decision,
            user_text="Ouvre le dossier téléchargement",
        )
        self.assertIsNotNone(intent)
        self.assertEqual(intent.name, "folder.open")

    def test_ai_time_tool_accepts_joined_stt_variant(self):
        decision = AgentDecision(
            kind="tool",
            tool="system.time",
            args={},
        )
        intent = decision_to_intent(
            decision,
            user_text="Quelleur est-il ?",
        )
        self.assertIsNotNone(intent)
        self.assertEqual(intent.name, "system.time")

    def test_ai_cannot_use_non_http_url(self):
        decision = AgentDecision(
            kind="tool",
            tool="browser.open_url",
            args={"url": "file:///C:/Windows/System32"},
        )
        self.assertIsNone(
            decision_to_intent(
                decision,
                user_text="Ouvre ce lien",
            )
        )


if __name__ == "__main__":
    unittest.main()
