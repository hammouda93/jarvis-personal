import unittest
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from jarvis_agent.assistant_v3 import AssistantWorker, TextTurnInbox
from jarvis_agent.tools import ToolIntent
from jarvis_agent.agent_runtime import AgentTurnResult
from jarvis_agent.states import AssistantState


class CompletionDisplayTests(unittest.TestCase):
    def test_unverified_goal_is_not_displayed_as_success_in_voice_or_text(self):
        for source in ("voice","text"):
            worker=SimpleNamespace(_conversation_language="fr",_pending_direct_follow_up="",
                conversation_message=Mock(),log_line=Mock(),detail_changed=Mock(),
                _handle_lifecycle=Mock(return_value=(False,True)),_handle_simple_direct_action=Mock(return_value=False),
                _state=Mock(),_agent_phase=Mock(),_shadow_observe=Mock(),_deliver_reply=Mock(),_level=Mock(),
                _agent=Mock(),_stop=threading.Event())
            worker._agent.run.return_value=AgentTurnResult("Résultat non confirmé",goal_completed=False,mission_status="inconclusive")
            self.assertTrue(AssistantWorker._process_user_text(worker,"Cherche une cible",source=source,legacy_intent=ToolIntent("unknown",{})))
            worker._state.assert_called_with(AssistantState.ERROR,"Mission non vérifiée")
            self.assertIs(worker._shadow_observe.call_args.kwargs["success"],False)

    def test_stop_interrupts_the_computer_use_controller(self):
        worker=SimpleNamespace(_stop=threading.Event(),_agent=Mock())
        AssistantWorker.stop(worker)
        self.assertTrue(worker._stop.is_set())
        worker._agent.cancel.assert_called_once()


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
