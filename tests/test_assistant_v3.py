import unittest

from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.tools import ToolIntent


class AssistantV3FastPathTests(unittest.TestCase):
    def test_new_conversation_command_accepts_conversational_prefixes(self):
        self.assertTrue(
            AssistantWorker._is_new_conversation_command(
                "Ok. Nouvelle conversation."
            )
        )
        self.assertTrue(
            AssistantWorker._is_new_conversation_command(
                "Très bien, nouvelle discussion."
            )
        )
        self.assertTrue(
            AssistantWorker._is_new_conversation_command(
                "Maintenant oublie cette conversation."
            )
        )
        self.assertFalse(
            AssistantWorker._is_new_conversation_command(
                "Que penses-tu de notre nouvelle conversation sur Atlas ?"
            )
        )

    def test_simple_youtube_open_uses_direct_path(self):
        self.assertTrue(
            AssistantWorker._is_simple_direct_action(
                "Ouvre YouTube.",
                ToolIntent("browser.open_url", {"url": "https://www.youtube.com"}),
            )
        )

    def test_simple_chrome_open_uses_direct_path(self):
        self.assertTrue(
            AssistantWorker._is_simple_direct_action(
                "Ouvre Chrome.",
                ToolIntent("app.open", {"app": "chrome"}),
            )
        )

    def test_compound_command_stays_with_agent(self):
        self.assertFalse(
            AssistantWorker._is_simple_direct_action(
                "Ouvre Chrome et recherche les agents IA.",
                ToolIntent("app.open", {"app": "chrome"}),
            )
        )


if __name__ == "__main__":
    unittest.main()
