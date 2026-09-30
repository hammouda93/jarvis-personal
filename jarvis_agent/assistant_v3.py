from __future__ import annotations

import re
import threading
import time
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from .agent_runtime import AgentRuntimeUnavailable, build_agent_runtime
from .audio import record_utterance, wait_for_double_clap
from .config import settings
from .language import normalize_language, repeat_prompt, tool_message
from .recognition import recognize_command
from .states import AssistantState, STATE_LABELS
from .stt import build_stt
from .tools import ToolIntent, execute, route
from .tts import ElevenLabsTTS


class AssistantWorker(QObject):
    """Voice shell around the model-native Agent Runtime.

    Natural language goes to one conversational agent loop. The old rule router
    is retained only for explicit Jarvis lifecycle commands, not for deciding
    normal app/folder/web tasks.
    """

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
        self._stt = build_stt()
        self._tts = ElevenLabsTTS()
        self._agent = build_agent_runtime()
        self._conversation_language = "fr"
        self._pending_direct_follow_up = ""

    def _state(self, state: AssistantState, status: str | None = None) -> None:
        self.state_changed.emit(state.value)
        self.status_changed.emit(status or STATE_LABELS[state])
        self.log_line.emit(f"[STATE] {state.value}")

    def _level(self, value: float) -> None:
        self.audio_level_changed.emit(max(0.0, min(1.0, float(value))))

    def _speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._state(AssistantState.SPEAKING, text)
        self.log_line.emit(f"[TTS] {text}")
        self._tts.speak(text, on_level=self._level)
        if settings.tts_settle_s > 0:
            time.sleep(settings.tts_settle_s)

    def _agent_phase(self, phase: str) -> None:
        if phase == "thinking":
            self._state(AssistantState.THINKING, "Jarvis réfléchit…")
        elif phase == "acting":
            self._state(AssistantState.ACTING, "Jarvis agit…")

    @Slot()
    def stop(self) -> None:
        self._stop.set()

    def _handle_lifecycle(self, user_text: str) -> tuple[bool, bool]:
        """Return (handled, keep_listening)."""
        intent = route(user_text)
        if intent.name not in {"assistant.stop", "assistant.sleep"}:
            return False, True

        result = execute(intent)
        spoken = tool_message(intent, result, self._conversation_language)
        self.log_line.emit(
            f"[DIRECT] lifecycle={intent.name} success={result.success}"
        )
        self._speak(spoken)

        if result.should_exit:
            self._stop.set()
            return True, False
        if result.end_session:
            return True, False
        return True, True

    @staticmethod
    def _is_simple_direct_action(user_text: str, intent: ToolIntent) -> bool:
        """Fast path only for one explicit deterministic action.

        Complex/compound language still goes to the conversational agent loop.
        This follows the local-first rule: simple commands should not depend on
        an LLM when the typed intent is already unambiguous.
        """
        if intent.name not in {
            "browser.open_url",
            "browser.search",
            "browser.search_prompt",
            "app.open",
            "folder.open",
            "folder.open_named",
            "folder.open_prompt",
            "system.time",
        }:
            return False

        text = user_text.lower()
        # Never let the legacy router truncate a compound mission. If the
        # utterance contains sequencing or a second obvious action, let the
        # native agent handle the complete objective.
        if re.search(r"\b(et|puis|ensuite|après|apres|and|then)\b", text):
            return False
        if re.search(r"\b(ouvre|ouvrir|lance)\b", text) and re.search(
            r"\b(recherche|cherche)\b",
            text,
        ):
            return False
        return True

    def _handle_simple_direct_action(
        self,
        user_text: str,
        intent: ToolIntent,
    ) -> bool:
        if not self._is_simple_direct_action(user_text, intent):
            return False

        result = execute(intent)
        self.log_line.emit(
            f"[DIRECT] simple={intent.name} success={result.success} "
            f"args={intent.args}"
        )
        spoken = tool_message(intent, result, self._conversation_language)
        self.detail_changed.emit(
            f"{intent.name}:{'ok' if result.success else 'erreur'}"
        )
        self._speak(spoken)
        self._state(
            AssistantState.SUCCESS if result.success else AssistantState.ERROR,
            "Prêt" if result.success else "Action non terminée",
        )
        return True

    def _listen_turn(self, *, first_turn: bool) -> bool:
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
                self.log_line.emit("[VOICE] aucune phrase détectée")
            else:
                self.log_line.emit(
                    "[SESSION] silence — retour au mode réveil, contexte conservé"
                )
            return False

        self._state(AssistantState.TRANSCRIBING, "Transcription locale…")
        if settings.stt_provider == "groq":
            self.log_line.emit(
                f"[STT] provider=groq model={settings.groq_stt_model}"
            )
        else:
            self.log_line.emit(
                f"[STT] provider=local model={settings.whisper_model} "
                f"device={settings.whisper_device}"
            )

        stt_started = time.perf_counter()
        transcript, legacy_intent = recognize_command(
            self._stt,
            audio,
            log=self.log_line.emit,
            preferred_language=self._conversation_language,
        )
        self.log_line.emit(
            f"[PERF] stt_total_seconds="
            f"{time.perf_counter() - stt_started:.2f}"
        )

        if not transcript.text:
            self._state(AssistantState.ERROR, "Phrase non comprise")
            self._speak(repeat_prompt(self._conversation_language))
            return True

        self._conversation_language = normalize_language(transcript.language)
        user_text = transcript.text.strip()
        self.transcript_changed.emit(user_text)
        self.log_line.emit(
            f"[YOU] {user_text} [lang={self._conversation_language}]"
        )

        weak_text = (
            transcript.avg_logprob is not None
            and transcript.avg_logprob < -0.95
        )
        probable_silence = (
            transcript.no_speech_probability is not None
            and transcript.no_speech_probability >= 0.45
        )
        if weak_text or probable_silence:
            self.log_line.emit(
                "[STT] transcript too uncertain for agent actions"
            )
            self._speak(repeat_prompt(self._conversation_language))
            return True

        lifecycle_handled, keep_listening = self._handle_lifecycle(user_text)
        if lifecycle_handled:
            return keep_listening

        if self._handle_simple_direct_action(user_text, legacy_intent):
            return True

        normalized = user_text.lower().strip(" .!?")
        if normalized in {
            "nouvelle conversation",
            "nouvelle discussion",
            "oublie cette conversation",
        }:
            self._agent.reset()
            self._speak("Très bien. On repart sur une nouvelle conversation.")
            return True

        self._state(
            AssistantState.UNDERSTANDING,
            "Compréhension de votre demande…",
        )

        try:
            turn = self._agent.run(
                user_text,
                log=self.log_line.emit,
                phase=self._agent_phase,
            )
        except AgentRuntimeUnavailable as exc:
            self.log_line.emit(f"[AGENT] unavailable: {exc}")
            self._state(AssistantState.ERROR, "Cerveau agent indisponible")
            self._speak(
                "Mon cerveau agent n'est pas disponible pour le moment. "
                "Vérifiez le modèle configuré puis réessayez."
            )
            return True

        if turn.actions:
            details = " · ".join(
                f"{action.name}:{'ok' if action.success else 'erreur'}"
                for action in turn.actions
            )
            self.detail_changed.emit(details)
            self.log_line.emit(
                f"[AGENT] completed_actions={len(turn.actions)}"
            )
        else:
            self.detail_changed.emit("Conversation")

        self._speak(turn.text)

        if turn.should_exit:
            self._stop.set()
            return False

        if turn.end_session:
            self.log_line.emit("[SESSION] agent requested standby")
            return False

        self._state(AssistantState.SUCCESS, "Prêt")
        self._level(0.0)
        return True

    @Slot()
    def run(self) -> None:
        self.log_line.emit("[BOOT] Jarvis native agent runtime started")
        self.log_line.emit(
            f"[AI] provider={settings.agent_provider} "
            f"stt_provider={settings.stt_provider} "
            f"local_model={settings.ollama_agent_model} "
            f"groq_model={settings.groq_agent_model} "
            f"openai_model={settings.openai_agent_model}"
        )
        self._state(AssistantState.STARTING, "Initialisation de Jarvis…")

        try:
            self.status_changed.emit("Chargement du cerveau local…")
            self._agent.warm_up(log=self.log_line.emit)
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

                # Important: agent context intentionally survives wake/sleep.
                # A short microphone timeout must not erase the conversation.
                self.log_line.emit("[SESSION] conversation active")
                first_turn = True

                while not self._stop.is_set():
                    keep_listening = self._listen_turn(first_turn=first_turn)
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
