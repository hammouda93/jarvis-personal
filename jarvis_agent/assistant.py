from __future__ import annotations

import threading
import time
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from .audio import record_utterance, wait_for_double_clap
from .config import settings
from .states import AssistantState, STATE_LABELS
from .stt import LocalWhisperSTT
from .tools import execute, route
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

    @Slot()
    def stop(self) -> None:
        self._stop.set()

    @Slot()
    def run(self) -> None:
        self.log_line.emit("[BOOT] Jarvis voice core started")
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
                )
                if not detected or self._stop.is_set():
                    break

                self._state(AssistantState.WAKE, "Réveil détecté")
                self.log_line.emit("[WAKE] double clap")

                self._speak(settings.wake_phrase)
                if self._stop.is_set():
                    break

                self._state(AssistantState.LISTENING, "Je vous écoute…")
                audio = record_utterance(
                    self._stop,
                    on_level=self._level,
                    on_status=self.status_changed.emit,
                )
                if audio is None:
                    if self._stop.is_set():
                        break
                    self._state(AssistantState.ERROR, "Je ne vous ai pas entendu")
                    self._speak("Je ne vous ai pas entendu.")
                    time.sleep(0.5)
                    continue

                self._state(
                    AssistantState.TRANSCRIBING,
                    "Transcription locale…",
                )
                self.log_line.emit(
                    f"[STT] model={settings.whisper_model} device={settings.whisper_device}"
                )
                text, language = self._stt.transcribe(audio)
                if not text:
                    self._state(AssistantState.ERROR, "Phrase non comprise")
                    self._speak("Je n'ai pas compris.")
                    time.sleep(0.5)
                    continue

                self.transcript_changed.emit(text)
                self.log_line.emit(f"[YOU] {text}")
                if language:
                    self.detail_changed.emit(f"Langue détectée · {language}")

                self._state(
                    AssistantState.UNDERSTANDING,
                    "Compréhension de la demande…",
                )
                intent = route(text)
                self.detail_changed.emit(f"Intent · {intent.name}")
                self.log_line.emit(f"[INTENT] {intent.name} {intent.args}")

                self._state(AssistantState.ACTING, "Exécution…")
                result = execute(intent)
                self.log_line.emit(
                    f"[TOOL] success={result.success} detail={result.detail}"
                )
                if result.detail:
                    self.detail_changed.emit(result.detail)

                self._speak(result.message)

                if result.should_exit:
                    self._stop.set()
                    break

                if result.success:
                    self._state(AssistantState.SUCCESS, "C'est fait")
                else:
                    self._state(AssistantState.ERROR, "Action non disponible")

                time.sleep(0.7)
                self._level(0.0)

            self._state(AssistantState.IDLE, "Jarvis arrêté")

        except Exception as exc:
            self.log_line.emit(f"[ERROR] {type(exc).__name__}: {exc}")
            self.log_line.emit(traceback.format_exc())
            self._state(AssistantState.ERROR, f"Erreur · {type(exc).__name__}")
        finally:
            self.finished.emit()
