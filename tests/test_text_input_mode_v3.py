import contextlib
import json
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

    def _gmail_command_during_voice(self):
        return self.worker.submit_mcp_control("gmail_connect", "gmail", json.dumps({
            "provider": "pipedream", "binding_revision": 0, "access": "plugin"}))

    def _run_with_voice_control(self, wake, *, record=None, recognize=None):
        self.agent.orchestra = self.agent.orchestra_voice = None
        result = {"success": True, "operation": "gmail_connect", "server_id": "gmail"}
        with patch("jarvis_agent.assistant_v3.wait_for_double_clap", side_effect=wake), patch(
            "jarvis_agent.assistant_v3.record_utterance", side_effect=record
        ) as recorder, patch(
            "jarvis_agent.assistant_v3.recognize_command", side_effect=recognize
        ) as stt, patch(
            "jarvis_agent.assistant_v3.perform_mcp_command", return_value=result
        ) as perform:
            self.worker.run()
        return recorder, stt, perform

    def test_gmail_click_interrupts_passive_wake_and_worker_resumes_voice(self):
        waits, interrupts = [], []

        def wake(*args, **kwargs):
            waits.append(True)
            if len(waits) == 1:
                self._gmail_command_during_voice()
                interrupts.append(kwargs["interrupt_event"].is_set())
            else:
                self.worker.stop()
            return False

        recorder, stt, perform = self._run_with_voice_control(wake)
        self.assertEqual(interrupts, [True])
        perform.assert_called_once()
        self.assertEqual(perform.call_args.args[1].operation, "gmail_connect")
        self.assertEqual(len(waits), 2, "Voice must resume after the control command")
        self.assertEqual(self.worker._input_mode.snapshot(), (False, 0))
        recorder.assert_not_called()
        stt.assert_not_called()
        self.agent.run.assert_not_called()

    def test_gmail_click_interrupts_recording_before_transcription(self):
        waits, interrupts = [], []

        def wake(*args, **kwargs):
            waits.append(True)
            if len(waits) > 1:
                self.worker.stop()
                return False
            return True

        def record(*args, **kwargs):
            self._gmail_command_during_voice()
            interrupts.append(kwargs["interrupt_event"].is_set())
            return object()  # Even a late completed capture must be discarded.

        recorder, stt, perform = self._run_with_voice_control(wake, record=record)
        self.assertEqual(interrupts, [True])
        recorder.assert_called_once()
        stt.assert_not_called()
        perform.assert_called_once()
        self.assertEqual(len(waits), 2)
        self.agent.run.assert_not_called()

    def test_gmail_click_during_transcription_discards_stale_voice_turn(self):
        waits = []

        def wake(*args, **kwargs):
            waits.append(True)
            if len(waits) > 1:
                self.worker.stop()
                return False
            return True

        def recognize(*args, **kwargs):
            self._gmail_command_during_voice()
            return Mock(text="Open a website", language="fr"), None

        recorder, stt, perform = self._run_with_voice_control(
            wake, record=lambda *a, **k: object(), recognize=recognize)
        stt.assert_called_once()
        perform.assert_called_once()
        self.assertEqual(len(waits), 2)
        self.agent.run.assert_not_called()
        self.assertTrue(any("stale transcription discarded" in line for line in self.logs))

    def test_rejected_control_does_not_interrupt_passive_voice(self):
        from jarvis_agent.assistant_v3 import VoiceCaptureInterrupt
        generation = self.worker._input_mode.snapshot()[1]
        interrupt = VoiceCaptureInterrupt(self.worker._input_mode, generation)
        self.assertFalse(interrupt.is_set())
        self.assertFalse(self.worker.submit_mcp_control("invalid", "gmail"))
        self.assertFalse(interrupt.is_set())

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
