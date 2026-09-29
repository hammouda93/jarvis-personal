from __future__ import annotations

import threading
import time
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from .agent_core import AgentCore
from .audio import record_utterance, wait_for_double_clap
from .config import settings
from .language import (
    no_speech_prompt,
    normalize_language,
    repeat_prompt,
    tool_message,
)
from .recognition import recognize_command
from .states import AssistantState, STATE_LABELS
from .stt import LocalWhisperSTT
from .tools import ToolIntent
from .tts import ElevenLabsTTS


class AssistantWorker(QObject):
    state_changed = Signal(str)
    status_changed = Signal(str)
    transcript_changed = Signal(str)
    detail_changed = Signal(str)
    audio_level_changed = Signal(float)
    log_line = Signal(str)
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._stop = threading.Event()
        self._stt = LocalWhisperSTT()
        self._tts = ElevenLabsTTS()
        self._core = AgentCore()
        self._conversation_language = "fr"

    def _state(self, state: AssistantState, status: str | None = None) -> None:
        self.state_changed.emit(state.value)
        self.status_changed.emit(status or STATE_LABELS[state])
        self.log_line.emit(f"[STATE] {state.value}")

    def _level(self, value: float) -> None:
        self.audio_level_changed.emit(max(0.0, min(1.0, float(value))))

    def _speak(self, text: str) -> None:
        self._state(AssistantState.SPEAKING, text)
        self.log_line.emit(f"[TTS] {text}")
        self._tts.speak(text, on_level=self._level)
        if settings.tts_settle_s > 0:
            time.sleep(settings.tts_settle_s)

    def _core_phase(self, phase: str) -> None:
        if phase == "planning":
            self._state(AssistantState.THINKING, "Planification de la mission…")
        elif phase == "acting":
            self._state(AssistantState.ACTING, "Exécution de la mission…")

    @Slot()
    def stop(self) -> None:
        self._stop.set()

    def _listen_turn(
        self,
        *,
        first_turn: bool,
        pending_follow_up: str | None,
    ) -> tuple[bool, str | None]:
        self._state(AssistantState.LISTENING, "Je vous écoute…")
        timeout = None if first_turn else settings.conversation_followup_timeout_s

        audio = record_utterance(
            self._stop,
            on_level=self._level,
            on_status=self.status_changed.emit,
            start_timeout_s=timeout,
        )

        if audio is None:
            if first_turn and not self._stop.is_set():
                self._state(AssistantState.ERROR, "Je ne vous ai pas entendu")
                self._speak(no_speech_prompt(self._conversation_language))
            else:
                self.log_line.emit(
                    "[SESSION] délai dépassé, retour au mode réveil"
                )
            return False, None

        self._state(AssistantState.TRANSCRIBING, "Transcription locale…")
        self.log_line.emit(
            f"[STT] model={settings.whisper_model} device={settings.whisper_device}"
        )

        if pending_follow_up in {"search_query", "folder_name"}:
            transcript = self._stt.transcribe(
                audio,
                language=self._conversation_language,
            )
            if pending_follow_up == "search_query":
                intent = ToolIntent(
                    "browser.search",
                    {"query": transcript.text.strip()},
                )
            else:
                intent = ToolIntent(
                    "folder.open_named",
                    {"query": transcript.text.strip()},
                )
            self.log_line.emit(
                f"[STT] follow_up={pending_follow_up} "
                f"language={transcript.language} "
                f"prob={transcript.language_probability} "
                f"logprob={transcript.avg_logprob}"
            )
        else:
            transcript, intent = recognize_command(
                self._stt,
                audio,
                log=self.log_line.emit,
                preferred_language=self._conversation_language,
            )

        if not transcript.text:
            self._state(AssistantState.ERROR, "Phrase non comprise")
            self._speak(repeat_prompt(self._conversation_language))
            return True, None

        user_text = transcript.text
        self._conversation_language = normalize_language(transcript.language)
        self.transcript_changed.emit(user_text)
        self.log_line.emit(
            f"[YOU] {user_text} [lang={self._conversation_language}]"
        )

        if transcript.language:
            self.detail_changed.emit(
                f"Langue détectée · {transcript.language}"
            )

        self._state(
            AssistantState.UNDERSTANDING,
            "Compréhension de l'objectif…",
        )
        self.detail_changed.emit(f"Intent initial · {intent.name}")
        self.log_line.emit(f"[INTENT] {intent.name} {intent.args}")

        weak_for_conversation = (
            transcript.avg_logprob is not None
            and transcript.avg_logprob < -1.05
        )
        probable_silence = (
            transcript.no_speech_probability is not None
            and transcript.no_speech_probability >= 0.50
        )

        if (
            intent.name == "unknown"
            and (weak_for_conversation or probable_silence)
        ):
            self.log_line.emit(
                "[STT] weak open-ended transcript rejected before Agent Core"
            )
            self._speak(repeat_prompt(self._conversation_language))
            return True, None

        core_result = self._core.handle(
            user_text,
            deterministic_intent=intent,
            log=self.log_line.emit,
            phase=self._core_phase,
        )

        if core_result.kind in {"answer", "clarify"}:
            self._speak(core_result.message)
            label = (
                "Précision demandée"
                if core_result.kind == "clarify"
                else "Réponse terminée"
            )
            self._state(AssistantState.SUCCESS, label)
            self._level(0.0)
            return True, None

        outcome = core_result.mission
        final_intent = core_result.final_intent
        final_result = core_result.final_result

        if outcome is None or final_intent is None or final_result is None:
            self._speak(
                "Je n'ai pas pu préparer cette mission correctement. "
                "Pouvez-vous préciser votre demande ?"
            )
            self._state(AssistantState.ERROR, "Mission non exécutable")
            return True, None

        self.detail_changed.emit(
            f"Mission {outcome.mission.id} · {outcome.mission.status.value}"
        )

        spoken_result = tool_message(
            final_intent,
            final_result,
            self._conversation_language,
        )
        self._speak(spoken_result)

        if final_result.should_exit:
            self._stop.set()
            return False, None

        if final_result.end_session:
            self.log_line.emit("[SESSION] retour en veille demandé")
            return False, None

        if outcome.success:
            self._state(AssistantState.SUCCESS, "Mission terminée")
        else:
            self._state(AssistantState.ERROR, "Mission interrompue")

        time.sleep(0.25)
        self._level(0.0)
        return True, core_result.follow_up

    @Slot()
    def run(self) -> None:
        self.log_line.emit("[BOOT] Jarvis Agent Core started")
        self.log_line.emit(
            f"[AI] planner=ollama model={settings.ollama_model}"
        )
        self._state(AssistantState.STARTING, "Initialisation de Jarvis…")

        try:
            while not self._stop.is_set():
                self.transcript_changed.emit("")
                self.detail_changed.emit("")

                self._state(
                    AssistantState.CALIBRATING,
                    "Calibration du microphone…",
                )
                detected = wait_for_double_clap(
                    self._stop,
                    on_level=self._level,
                    on_status=self.status_changed.emit,
                    on_armed=lambda: self._state(
                        AssistantState.ARMED,
                        "Prêt — double clap pour réveiller Jarvis",
                    ),
                )
                if not detected or self._stop.is_set():
                    break

                self._state(AssistantState.WAKE, "Réveil détecté")
                self.log_line.emit("[WAKE] double clap")
                self._speak(settings.wake_phrase)

                if self._stop.is_set():
                    break

                self._core.reset_session()
                self._conversation_language = (
                    settings.stt_language
                    if settings.stt_language in {"fr", "en", "ar"}
                    else "fr"
                )

                self.log_line.emit("[SESSION] conversation active")
                first_turn = True
                pending_follow_up: str | None = None

                while not self._stop.is_set():
                    keep_listening, pending_follow_up = self._listen_turn(
                        first_turn=first_turn,
                        pending_follow_up=pending_follow_up,
                    )
                    if not keep_listening:
                        break
                    first_turn = False

            self._state(AssistantState.IDLE, "Jarvis arrêté")

        except Exception as exc:
            self.log_line.emit(f"[ERROR] {type(exc).__name__}: {exc}")
            self.log_line.emit(traceback.format_exc())
            self._state(
                AssistantState.ERROR,
                f"Erreur · {type(exc).__name__}",
            )
        finally:
            self.finished.emit()
