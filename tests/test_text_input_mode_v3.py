import contextlib
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import AgentTurnResult
from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.config import settings as real_settings


class TextInputModeV3Tests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch(
                "jarvis_agent.assistant_v3.settings",
                replace(
                    real_settings,
                    kernel_shadow_enabled=False,
                    tts_settle_s=0,
                ),
            )
        )
        self.agent = Mock()
        self.agent.active_mission_id = None
        self.agent.run.return_value = AgentTurnResult("Bonjour.")
        self.tts = Mock()
        self.stack.enter_context(
            patch(
                "jarvis_agent.assistant_v3.build_agent_runtime",
                return_value=self.agent,
            )
        )
        self.stack.enter_context(
            patch(
                "jarvis_agent.assistant_v3.ElevenLabsTTS",
                return_value=self.tts,
            )
        )
        self.stack.enter_context(
            patch(
                "jarvis_agent.assistant_v3.build_stt",
                return_value=Mock(),
            )
        )
        self.worker = AssistantWorker()
        self.messages = []
        self.logs = []
        self.worker.conversation_message.connect(
            lambda *args: self.messages.append(args)
        )
        self.worker.log_line.connect(self.logs.append)

    def test_typed_turn_keeps_text_provenance_and_spoken_reply(self):
        self.worker.set_text_mode(True)

        self.worker._process_user_text(
            "Bonjour Jarvis",
            source="text",
        )

        self.assertIn(
            ("user", "Bonjour Jarvis", "text"),
            self.messages,
        )
        self.assertIn(
            ("assistant", "Bonjour.", "text"),
            self.messages,
        )
        self.tts.speak.assert_called_once()
        self.assertEqual(
            self.tts.speak.call_args.args[0],
            "Bonjour.",
        )

    def test_text_mode_never_starts_microphone_pipeline(self):
        self.worker.set_text_mode(True)
        self.worker.submit_text("Bonjour Jarvis")

        def finish(*args, **kwargs):
            self.worker.stop()
            return AgentTurnResult("Bonjour.")

        self.agent.run.side_effect = finish

        with patch(
            "jarvis_agent.assistant_v3.wait_for_double_clap"
        ) as wake, patch(
            "jarvis_agent.assistant_v3.record_utterance"
        ) as record:
            self.worker.run()

        wake.assert_not_called()
        record.assert_not_called()
        self.tts.speak.assert_called_once()
        self.assertTrue(
            any(
                "[INPUT_MODE] text microphone=off" in item
                for item in self.logs
            )
        )

    def test_old_browser_question_cannot_hijack_a_qualified_search(self):
        self.worker.set_text_mode(True)
        self.worker._pending_direct_follow_up = "search_query"
        with patch("jarvis_agent.assistant_v3.execute") as execute:
            self.worker._process_user_text("Cherche dans ma memoire", source="text")
        execute.assert_not_called()
        self.agent.run.assert_called_once()
        self.assertEqual(self.agent.run.call_args.args[0], "Cherche dans ma memoire")
        self.assertEqual(self.worker._pending_direct_follow_up, "")

    def test_plain_reply_to_browser_question_still_searches(self):
        from jarvis_agent.tools import ToolResult
        self.worker.set_text_mode(True)
        self.worker._pending_direct_follow_up = "search_query"
        with patch("jarvis_agent.foundation_tools.enabled", return_value=False), patch(
            "jarvis_agent.assistant_v3.execute", return_value=ToolResult(True, "Recherche ouverte")
        ) as execute:
            self.worker._process_user_text("Piano", source="text")
        self.assertEqual(execute.call_args.args[0].name, "browser.search")
        self.agent.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
