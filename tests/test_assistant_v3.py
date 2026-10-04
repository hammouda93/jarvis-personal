import unittest

from jarvis_agent.assistant_v3 import AssistantWorker, TextTurnInbox
from jarvis_agent.tools import ToolIntent


class TextTurnInboxTests(unittest.TestCase):
    def test_text_inbox_is_fifo_and_signals_pending_state(self):
        inbox = TextTurnInbox()

        self.assertFalse(inbox.pending())
        self.assertTrue(inbox.submit("premier"))
        self.assertTrue(inbox.submit("deuxième"))
        self.assertTrue(inbox.pending())
        self.assertEqual(inbox.pop_nowait(), "premier")
        self.assertTrue(inbox.pending())
        self.assertEqual(inbox.pop_nowait(), "deuxième")
        self.assertFalse(inbox.pending())

    def test_text_inbox_rejects_empty_messages(self):
        inbox = TextTurnInbox()

        self.assertFalse(inbox.submit("   "))
        self.assertIsNone(inbox.pop_nowait())
        self.assertFalse(inbox.pending())


class AssistantV3FastPathTests(unittest.TestCase):
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

    def test_compound_youtube_search_uses_direct_site_search(self):
        self.assertTrue(
            AssistantWorker._is_simple_direct_action(
                "Ouvre YouTube et recherche Messi.",
                ToolIntent(
                    "browser.search_site",
                    {"site": "youtube", "query": "messi"},
                ),
            )
        )

    def test_close_tab_uses_direct_browser_primitive(self):
        self.assertTrue(
            AssistantWorker._is_simple_direct_action(
                "Ferme l'onglet YouTube uniquement.",
                ToolIntent("browser.close_tab", {"name": "youtube"}),
            )
        )

    def test_browser_back_uses_direct_browser_primitive(self):
        self.assertTrue(
            AssistantWorker._is_simple_direct_action(
                "Retour en arrière.",
                ToolIntent("browser.back"),
            )
        )

    def test_in_app_search_stays_with_agent(self):
        self.assertFalse(
            AssistantWorker._is_simple_direct_action(
                "Recherche dans la barre de recherche YouTube sur le sport.",
                ToolIntent(
                    "browser.search",
                    {"query": "dans la barre de recherche youtube sur le sport"},
                ),
            )
        )

    def test_in_app_video_selection_stays_with_agent(self):
        self.assertFalse(
            AssistantWorker._is_simple_direct_action(
                "Ouvre la première vidéo pertinente dans l'onglet YouTube.",
                ToolIntent(
                    "browser.open_url",
                    {"url": "https://www.youtube.com"},
                ),
            )
        )

    def test_compound_cursor_installation_stays_in_agent_until_continuation(self):
        self.assertFalse(
            AssistantWorker._is_simple_direct_action(
                "Ouvre l'installation de Cursor dans Téléchargements et poursuis l'installation.",
                ToolIntent("app.open", {"app": "cursor"}),
            )
        )

    def test_installer_request_stays_with_agent(self):
        self.assertFalse(
            AssistantWorker._is_simple_direct_action(
                "Ouvre-moi le fichier d'installation de Cursor maintenant.",
                ToolIntent("app.open", {"app": "cursor"}),
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
