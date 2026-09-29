from __future__ import annotations

from typing import Callable

import numpy as np

from .stt import LocalWhisperSTT, TranscriptResult
from .tools import ToolIntent, route


LogFn = Callable[[str], None]


def _is_pc_action(intent: ToolIntent) -> bool:
    return intent.name in {
        "browser.open_url",
        "browser.search",
        "app.open",
        "folder.open",
        "assistant.stop",
    }


def recognize_command(
    stt: LocalWhisperSTT,
    audio: np.ndarray,
    *,
    log: LogFn | None = None,
) -> tuple[TranscriptResult, ToolIntent]:
    # Current V1 is French-first on purpose. Very short auto-language
    # detection was the source of Hebrew/English/Polish hallucinations.
    transcript = stt.transcribe(audio)
    intent = route(transcript.text) if transcript.text else ToolIntent("unknown")

    if log:
        log(
            f"[STT] language={transcript.language} "
            f"prob={transcript.language_probability} "
            f"logprob={transcript.avg_logprob} "
            f"no_speech={transcript.no_speech_probability} "
            f"rejected={transcript.rejected_reason}"
        )

    if not transcript.text:
        return transcript, ToolIntent("unknown")

    # Safety rule: never let a weak transcript trigger a PC action.
    # A bad transcription can still be sent to the conversational AI as text,
    # but it must not open apps, websites or files by itself.
    weak_text = (
        transcript.avg_logprob is not None
        and transcript.avg_logprob < -0.90
    )
    probable_silence = (
        transcript.no_speech_probability is not None
        and transcript.no_speech_probability >= 0.55
    )

    if _is_pc_action(intent) and (weak_text or probable_silence):
        if log:
            log("[STT] action blocked because transcript confidence is too low")
        return transcript, ToolIntent("unknown", {"text": transcript.text})

    return transcript, intent
