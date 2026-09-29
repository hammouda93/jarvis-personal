from __future__ import annotations

import threading
import time
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from .audio import record_utterance, wait_for_double_clap
from .brain import (
    AIProviderUnavailable,
    build_ai_provider,
    validate_tool_decision,
)
from .capabilities import (
    confirmation_prompt,
    detect_missing_capability,
    is_affirmative,
    is_negative,
)
from .config import settings
from .language import (
    action_mismatch_prompt,
    ai_unavailable_prompt,
    cancellation_prompt,
    capability_unavailable_prompt,
    no_speech_prompt,
    normalize_language,
    repeat_prompt,
    tool_message,
)
from .recognition import recognize_command
from .states import AssistantState, STATE_LABELS
from .stt import LocalWhisperSTT
from .tools import ToolIntent, execute
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
        self._brain = build_ai_provider()
        self._pending_confirmation_intent: ToolIntent | None = None
        self._conversation_language: str = "fr"

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

    @Slot()
    def stop(self) -> None:
        self._stop.set()

    def _use_ai_if_needed(
        self,
        user_text: str,
        intent: ToolIntent,
        language: str | None,
    ) -> tuple[ToolIntent | None, str | None]:
        if intent.name != "unknown":
            return intent, None

        missing = detect_missing_capability(user_text)
        if missing is not None:
            self.log_line.emit(f"[CAPABILITY] missing={missing.key}")
            return None, missing.message(language)

        self._state(AssistantState.THINKING, "Réflexion locale…")
        try:
            decision = self._brain.decide(user_text)
        except AIProviderUnavailable as exc:
            self.log_line.emit(f"[AI] unavailable: {exc}")
            return None, ai_unavailable_prompt(language)

        self.log_line.emit(
            f"[AI] kind={decision.kind} tool={decision.tool} "
            f"args={decision.args}"
        )

        if decision.kind == "answer":
            return None, decision.message

        validation = validate_tool_decision(
            decision,
            user_text=user_text,
        )
        self.log_line.emit(
            f"[AI] tool_validation={validation.reason}"
        )

        if validation.reason == "ok" and validation.intent is not None:
            return validation.intent, None

        if (
            validation.reason == "confirmation_required"
            and validation.intent is not None
        ):
            self._pending_confirmation_intent = validation.intent
            return None, confirmation_prompt(validation.intent, language)

        missing = detect_missing_capability(user_text)
        if missing is not None:
            self.log_line.emit(f"[CAPABILITY] missing={missing.key}")
            return None, missing.message(language)

        if validation.reason == "unsupported_tool":
            return None, capability_unavailable_prompt(language)

        return None, action_mismatch_prompt(language)

    def _listen_turn(
        self,
        *,
        first_turn: bool,
        pending_follow_up: str | None,
    ) -> tuple[bool, str | None]:
        self._state(AssistantState.LISTENING, "Je vous écoute…")
        timeout = None if first_turn else settings.conversation_followup_timeout_s
        if not first_turn and self._pending_confirmation_intent is not None:
            timeout = settings.confirmation_timeout_s
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
            transcript = self._stt.transcribe(audio, language="auto")
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

        if self._pending_confirmation_intent is not None:
            if is_affirmative(user_text):
                intent = self._pending_confirmation_intent
                self._pending_confirmation_intent = None
                self.log_line.emit(
                    f"[CONFIRM] accepted intent={intent.name} {intent.args}"
                )
            elif is_negative(user_text):
                self.log_line.emit("[CONFIRM] rejected")
                self._pending_confirmation_intent = None
                self._speak(cancellation_prompt(self._conversation_language))
                return True, None
            else:
                self.log_line.emit(
                    "[CONFIRM] no clear yes/no — treating as a new request"
                )
                self._pending_confirmation_intent = None
        if transcript.language:
            self.detail_changed.emit(
                f"Langue détectée · {transcript.language}"
            )

        self._state(
            AssistantState.UNDERSTANDING,
            "Compréhension de la demande…",
        )
        self.detail_changed.emit(f"Intent · {intent.name}")
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
                "[STT] weak open-ended transcript rejected before AI"
            )
            self._speak(repeat_prompt(self._conversation_language))
            return True, None

        intent, direct_answer = self._use_ai_if_needed(
            user_text,
            intent,
            self._conversation_language,
        )

        if direct_answer is not None:
            self._speak(direct_answer)
            self._state(AssistantState.SUCCESS, "Réponse terminée")
            time.sleep(0.25)
            self._level(0.0)
            return True, None

        if intent is None:
            self._state(AssistantState.ERROR, "Demande non disponible")
            return True, None

        self._state(AssistantState.ACTING, "Exécution…")
        tool_result = execute(intent)
        self.log_line.emit(
            f"[TOOL] success={tool_result.success} "
            f"detail={tool_result.detail}"
        )
        if tool_result.detail:
            self.detail_changed.emit(tool_result.detail)

        spoken_result = tool_message(
            intent,
            tool_result,
            self._conversation_language,
        )

        try:
            self._brain.remember_tool_result(
                user_text,
                intent,
                spoken_result,
            )
        except Exception as exc:
            self.log_line.emit(f"[AI] memory note skipped: {exc}")

        self._speak(spoken_result)

        if tool_result.should_exit:
            self._stop.set()
            return False, None

        if tool_result.end_session:
            self.log_line.emit("[SESSION] retour en veille demandé")
            return False, None

        if tool_result.success:
            self._state(AssistantState.SUCCESS, "C'est fait")
        else:
            self._state(AssistantState.ERROR, "Action non disponible")

        time.sleep(0.30)
        self._level(0.0)
        return True, tool_result.follow_up

    @Slot()
    def run(self) -> None:
        self.log_line.emit("[BOOT] Jarvis voice core started")
        self.log_line.emit(
            f"[AI] provider={settings.ai_provider} model={settings.ollama_model}"
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
