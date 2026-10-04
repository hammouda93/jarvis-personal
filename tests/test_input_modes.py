"""Reproduce cross-channel contamination and microphone mode transitions."""
import contextlib
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from jarvis_agent import audio
from jarvis_agent.agent_runtime import AgentTurnResult
from jarvis_agent.assistant_v3 import (
    AssistantWorker, InputModeGate, TextTurnInbox, VoiceCaptureInterrupt,
)
from jarvis_agent.config import settings as real_settings
from jarvis_agent.tools import ToolIntent, ToolResult


class InputModeTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("jarvis_agent.assistant_v3.settings", replace(
            real_settings, kernel_shadow_enabled=False, tts_settle_s=0)))
        self.agent = Mock()
        self.agent.run.return_value = AgentTurnResult("Bonjour.", mission_status="answered")
        self.tts, self.stt = Mock(), Mock()
        self.stack.enter_context(patch("jarvis_agent.assistant_v3.build_agent_runtime", return_value=self.agent))
        self.stack.enter_context(patch("jarvis_agent.assistant_v3.ElevenLabsTTS", return_value=self.tts))
        self.stack.enter_context(patch("jarvis_agent.assistant_v3.build_stt", return_value=self.stt))
        self.worker = AssistantWorker()
        self.messages, self.logs = [], []
        self.worker.conversation_message.connect(lambda *args: self.messages.append(args))
        self.worker.log_line.connect(self.logs.append)

    def test_typed_reply_is_displayed_and_spoken_with_text_provenance(self):
        self.worker.set_text_mode(True)
        self.worker._process_user_text("Bonjour Jarvis", source="text")
        self.assertIn(("user", "Bonjour Jarvis", "text"), self.messages)
        self.assertIn(("assistant", "Bonjour.", "text"), self.messages)
        self.tts.speak.assert_called_once()
        self.assertEqual(self.tts.speak.call_args.args[0], "Bonjour.")
        self.assertTrue(any("[TEXT_REPLY] Bonjour." in x for x in self.logs))
        self.stt.transcribe.assert_not_called()

    def test_text_selection_runs_a_turn_without_opening_microphone(self):
        self.worker.set_text_mode(True)
        self.worker.submit_text("Bonjour Jarvis")
        def finish(*args, **kwargs):
            self.worker.stop()
            return AgentTurnResult("Bonjour.", mission_status="answered")
        self.agent.run.side_effect = finish
        with patch("jarvis_agent.assistant_v3.wait_for_double_clap") as wake, \
             patch("jarvis_agent.assistant_v3.record_utterance") as record:
            self.worker.run()
        wake.assert_not_called()
        record.assert_not_called()
        self.tts.speak.assert_called_once()
        self.assertTrue(any("[INPUT_MODE] text microphone=off" in x for x in self.logs))

    def test_waiting_in_text_mode_does_not_restart_audio_between_messages(self):
        self.worker.set_text_mode(True)
        def idle_wait(timeout):
            self.worker.stop()
            return True
        with patch.object(self.worker._input_mode.changed, "wait", side_effect=idle_wait), \
             patch("jarvis_agent.assistant_v3.wait_for_double_clap") as wake, \
             patch("jarvis_agent.assistant_v3.record_utterance") as record:
            self.worker.run()
        wake.assert_not_called()
        record.assert_not_called()
        self.agent.run.assert_not_called()

    def test_stale_voice_transcription_is_discarded_on_text_selection(self):
        transcript = SimpleNamespace(text="Ouvre un dossier", language="fr", avg_logprob=-0.2,
                                     no_speech_probability=0.0)
        def recognize(*args, **kwargs):
            self.worker.set_text_mode(True)
            return transcript, ToolIntent("folder.open_prompt")
        with patch("jarvis_agent.assistant_v3.record_utterance", return_value=np.ones(10)), \
             patch("jarvis_agent.assistant_v3.recognize_command", side_effect=recognize), \
             patch("jarvis_agent.assistant_v3.execute") as direct:
            self.assertFalse(self.worker._listen_turn(first_turn=True))
        direct.assert_not_called()
        self.agent.run.assert_not_called()
        self.tts.speak.assert_not_called()
        self.assertTrue(any("stale transcription discarded" in x for x in self.logs))

    def test_voice_to_text_to_voice_does_not_resurrect_old_transcription(self):
        transcript = SimpleNamespace(text="Ouvre Chrome", language="fr")
        def recognize(*args, **kwargs):
            self.worker.set_text_mode(True)
            self.worker.set_text_mode(False)
            return transcript, ToolIntent("app.open", {"app": "chrome"})
        with patch("jarvis_agent.assistant_v3.record_utterance", return_value=np.ones(10)), \
             patch("jarvis_agent.assistant_v3.recognize_command", side_effect=recognize), \
             patch("jarvis_agent.assistant_v3.execute") as direct:
            self.assertFalse(self.worker._listen_turn(first_turn=True))
        direct.assert_not_called()
        self.agent.run.assert_not_called()

    def test_mode_change_after_clap_does_not_start_voice_session(self):
        def clap(*args, **kwargs):
            self.worker.set_text_mode(True)
            self.worker.submit_text("Bonjour Jarvis")
            return True
        def finish(*args, **kwargs):
            self.worker.stop()
            return AgentTurnResult("Bonjour.", mission_status="answered")
        self.agent.run.side_effect = finish
        with patch("jarvis_agent.assistant_v3.wait_for_double_clap", side_effect=clap), \
             patch("jarvis_agent.assistant_v3.record_utterance") as record:
            self.worker.run()
        record.assert_not_called()
        self.assertEqual(self.tts.speak.call_args.args[0], "Bonjour.")
        self.assertFalse(any("[WAKE]" in x for x in self.logs))

    def test_voice_folder_follow_up_cannot_consume_typed_greeting(self):
        self.worker._pending_direct_follow_up = "folder_name"
        self.worker._pending_direct_source = "voice"
        self.worker.set_text_mode(True)
        with patch("jarvis_agent.assistant_v3.execute") as direct:
            self.worker._process_user_text("bonjour jarvis", source="text")
        direct.assert_not_called()
        self.agent.run.assert_called_once()
        self.assertEqual(self.worker._pending_direct_follow_up, "")

    def test_greeting_interrupts_follow_up_even_on_same_channel(self):
        self.worker._pending_direct_follow_up = "folder_name"
        self.worker._pending_direct_source = "text"
        with patch("jarvis_agent.assistant_v3.execute") as direct:
            self.worker._process_user_text("Bonjour", source="text")
        direct.assert_not_called()
        self.agent.run.assert_called_once()

    def test_new_explicit_request_is_not_a_folder_name(self):
        self.worker._pending_direct_follow_up = "folder_name"
        self.worker._pending_direct_source = "text"
        result = ToolResult(True, "Il est 17 heures.")
        with patch("jarvis_agent.assistant_v3.execute", return_value=result) as direct:
            self.worker._process_user_text("Quelle heure est-il ?", source="text")
        self.assertEqual(direct.call_args.args[0].name, "system.time")
        self.agent.run.assert_not_called()

    def test_explicit_follow_up_still_works_on_same_channel(self):
        self.worker._pending_direct_follow_up = "folder_name"
        self.worker._pending_direct_source = "text"
        with patch("jarvis_agent.assistant_v3.execute", return_value=ToolResult(True, "Dossier ouvert.")) as direct:
            self.worker._process_user_text("Documents", source="text")
        self.assertEqual(direct.call_args.args[0], ToolIntent("folder.open_named", {"query": "Documents"}))
        self.agent.run.assert_not_called()

    def test_descriptive_speech_is_not_a_direct_launch_request(self):
        self.assertFalse(AssistantWorker._is_simple_direct_action(
            "L'utilisateur peut demander d'ouvrir une application ou un dossier.",
            ToolIntent("folder.open_prompt")))
        self.assertTrue(AssistantWorker._is_simple_direct_action(
            "Peux-tu ouvrir un dossier ?", ToolIntent("folder.open_prompt")))

    def test_disabling_text_mode_restores_voice_admission(self):
        gate = self.worker._input_mode
        original = VoiceCaptureInterrupt(gate, gate.snapshot()[1])
        self.worker.set_text_mode(True)
        self.assertTrue(original.is_set())
        self.worker.set_text_mode(False)
        self.assertTrue(original.is_set())  # A previous capture remains cancelled.
        self.assertFalse(VoiceCaptureInterrupt(gate, gate.snapshot()[1]).is_set())
        self.agent.reset.assert_not_called()


class AudioAdmissionTests(unittest.TestCase):
    def test_disabled_input_never_queries_or_opens_audio_device(self):
        gate = InputModeGate(TextTurnInbox())
        gate.set_text_mode(True)
        interrupt = VoiceCaptureInterrupt(gate, gate.snapshot()[1])
        with patch.object(audio, "input_device_index") as device, patch.object(audio.sd, "InputStream") as stream:
            self.assertFalse(audio.wait_for_double_clap(threading.Event(), interrupt_event=interrupt))
            self.assertIsNone(audio.record_utterance(threading.Event(), interrupt_event=interrupt))
        device.assert_not_called()
        stream.assert_not_called()

    def test_switching_mode_closes_an_active_input_stream(self):
        for function in (audio.wait_for_double_clap, audio.record_utterance):
            with self.subTest(function=function.__name__):
                gate = InputModeGate(TextTurnInbox())
                entered, closed = [], []
                class Stream:
                    def __enter__(self):
                        entered.append(True)
                        return self
                    def read(self, count):
                        gate.set_text_mode(True)
                        return np.zeros((count, 1), dtype=np.float32), False
                    def __exit__(self, *args):
                        closed.append(True)
                with patch.object(audio, "input_device_index", return_value=None), \
                     patch.object(audio.sd, "InputStream", return_value=Stream()):
                    result = function(threading.Event(), interrupt_event=VoiceCaptureInterrupt(gate, 0))
                self.assertFalse(result)
                self.assertEqual(entered, [True])
                self.assertEqual(closed, [True])


class TextModeWidgetTests(unittest.TestCase):
    def test_real_text_button_changes_worker_mode_and_can_restore_voice(self):
        from jarvis_agent.ui import JarvisWindow
        self.app = QApplication.instance() or QApplication([])
        with patch("jarvis_agent.assistant_v3.settings", replace(real_settings, kernel_shadow_enabled=False)), \
             patch("jarvis_agent.ui.settings", replace(real_settings, ui_fullscreen=False)), \
             patch("jarvis_agent.assistant_v3.build_agent_runtime", return_value=Mock()), \
             patch("jarvis_agent.assistant_v3.build_stt", return_value=Mock()), \
             patch("jarvis_agent.assistant_v3.ElevenLabsTTS", return_value=Mock()), \
             patch("jarvis_agent.ui.QThread.start"):
            window = JarvisWindow()
            try:
                window.conversation_button.click()
                self.assertTrue(window._worker._input_mode.snapshot()[0])
                self.assertIn("Micro coupé", window.bottom_hint.text())
                window.conversation_button.click()
                self.assertFalse(window._worker._input_mode.snapshot()[0])
                self.assertIn("claquements", window.bottom_hint.text())
            finally:
                window.close()


class FallbackDiagnosticTests(unittest.TestCase):
    def test_cerebras_fallback_reports_status_without_dumping_api_body(self):
        from jarvis_agent.agent_runtime import CerebrasResponsesAgent, GroqResponsesAgent, AgentRuntimeUnavailable
        class ServiceError(Exception):
            status_code = 429
        cause = ServiceError("sensitive-response-body-and-key")
        error = AgentRuntimeUnavailable("API error 429")
        error.__cause__ = cause
        agent = CerebrasResponsesAgent(Mock())
        agent.api_key = "primary-test-key"
        response = object()
        with patch("jarvis_agent.agent_runtime.settings", replace(real_settings,
                    cerebras_secondary_api_key="secondary-test-key", cerebras_fallback_to_groq=False)), \
             patch.object(GroqResponsesAgent, "_chat", side_effect=[error, response]), \
             patch("builtins.print") as output:
            self.assertIs(agent._chat(), response)
        logs = "\n".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("status_code=429", logs)
        self.assertIn("error_type=ServiceError", logs)
        self.assertNotIn("sensitive-response-body-and-key", logs)
        self.assertNotIn("secondary-test-key", logs)
        self.assertEqual(agent.api_key, "primary-test-key")

    def test_cerebras_429_cooldown_skips_repeating_primary_request(self):
        from jarvis_agent.agent_runtime import (
            AgentRuntimeUnavailable,
            CerebrasResponsesAgent,
            GroqResponsesAgent,
        )

        class Response:
            headers = {"retry-after": "12"}

        class ServiceError(Exception):
            status_code = 429
            response = Response()

        cause = ServiceError("rate limit")
        error = AgentRuntimeUnavailable("API error 429")
        error.__cause__ = cause
        first_secondary = object()
        second_secondary = object()
        agent = CerebrasResponsesAgent(Mock())
        agent.api_key = "primary-test-key"

        with patch(
            "jarvis_agent.agent_runtime.settings",
            replace(
                real_settings,
                cerebras_secondary_api_key="secondary-test-key",
                cerebras_fallback_to_groq=False,
            ),
        ), patch(
            "jarvis_agent.agent_runtime.time.monotonic",
            return_value=100.0,
        ), patch.object(
            GroqResponsesAgent,
            "_chat",
            side_effect=[error, first_secondary, second_secondary],
        ) as chat_mock, patch("builtins.print") as output:
            self.assertIs(agent._chat(), first_secondary)
            self.assertIs(agent._chat(), second_secondary)

        self.assertEqual(chat_mock.call_count, 3)
        self.assertEqual(agent._primary_rate_limited_until, 112.0)
        logs = "\n".join(
            str(call.args[0]) for call in output.call_args_list
        )
        self.assertIn("cooldown=active", logs)

    def test_tts_keeps_local_voice_and_reports_status_without_dumping_api_body(self):
        from jarvis_agent.tts import ElevenLabsTTS
        class ServiceError(Exception):
            status_code = 401
        client = Mock()
        client.text_to_speech.convert.side_effect = ServiceError("sensitive-response-body-and-key")
        with patch("jarvis_agent.tts.settings", replace(real_settings,
                    elevenlabs_api_key="tts-test-key", elevenlabs_voice_id="tts-test-voice")), \
             patch.dict("sys.modules", {"elevenlabs.client": SimpleNamespace(ElevenLabs=Mock(return_value=client))}):
            tts = ElevenLabsTTS()
            with patch.object(tts, "_load_cached", return_value=None), \
                 patch.object(tts, "_speak_windows") as local, patch("builtins.print") as output:
                tts.speak("Bonjour.")
        local.assert_called_once_with("Bonjour.", on_level=None)
        logs = "\n".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("status_code=401", logs)
        self.assertIn("error_type=ServiceError", logs)
        self.assertNotIn("sensitive-response-body-and-key", logs)
        self.assertNotIn("tts-test-key", logs)


if __name__ == "__main__":
    unittest.main()
