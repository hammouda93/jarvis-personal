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
    THINKING = "thinking"
    ACTING = "acting"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    WAITING_APPROVAL = "waiting_approval"
    RECOVERING = "recovering"
    PAUSED = "paused"
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
    AssistantState.THINKING: "RÉFLEXION",
    AssistantState.ACTING: "ACTION EN COURS",
    AssistantState.OBSERVING: "OBSERVATION",
    AssistantState.VERIFYING: "VÉRIFICATION",
    AssistantState.WAITING_APPROVAL: "APPROBATION ATTENDUE",
    AssistantState.RECOVERING: "RÉVISION NÉCESSAIRE",
    AssistantState.PAUSED: "MISSION SUSPENDUE",
    AssistantState.SPEAKING: "RÉPONSE",
    AssistantState.SUCCESS: "TERMINÉ",
    AssistantState.ERROR: "ATTENTION",
}
