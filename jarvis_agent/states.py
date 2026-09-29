from __future__ import annotations

from enum import Enum


class AssistantState(str, Enum):
    STARTING = "starting"
    CALIBRATING = "calibrating"
    IDLE = "idle"
    ARMED = "armed"
    WAKE = "wake"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    UNDERSTANDING = "understanding"
    ACTING = "acting"
    SPEAKING = "speaking"
    SUCCESS = "success"
    ERROR = "error"


STATE_LABELS: dict[AssistantState, str] = {
    AssistantState.STARTING: "INITIALISATION",
    AssistantState.CALIBRATING: "CALIBRATION AUDIO",
    AssistantState.IDLE: "EN VEILLE",
    AssistantState.ARMED: "PRÊT",
    AssistantState.WAKE: "RÉVEIL",
    AssistantState.LISTENING: "JE VOUS ÉCOUTE",
    AssistantState.TRANSCRIBING: "TRANSCRIPTION",
    AssistantState.UNDERSTANDING: "COMPRÉHENSION",
    AssistantState.ACTING: "ACTION EN COURS",
    AssistantState.SPEAKING: "RÉPONSE",
    AssistantState.SUCCESS: "TERMINÉ",
    AssistantState.ERROR: "ATTENTION",
}
