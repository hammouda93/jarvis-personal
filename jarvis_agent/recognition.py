from __future__ import annotations

from typing import Callable

import numpy as np

from .config import settings
from .stt import LocalWhisperSTT, TranscriptResult
from .tools import ToolIntent, route


LogFn = Callable[[str], None]


def recognize_command(
    stt: LocalWhisperSTT,
    audio: np.ndarray,
    *,
    log: LogFn | None = None,
) -> tuple[TranscriptResult, ToolIntent]:
    first = stt.transcribe(audio)
    intent = route(first.text) if first.text else ToolIntent("unknown")

    if log:
        log(
            f"[STT] first_pass language={first.language} "
            f"prob={first.language_probability} logprob={first.avg_logprob}"
        )

    retry_language = settings.stt_command_retry_language
    probability = first.language_probability
    low_confidence = (
        probability is None
        or probability < settings.stt_retry_language_probability
    )
    different_language = (
        retry_language is not None
        and first.language is not None
        and first.language != retry_language
    )

    should_retry = (
        settings.stt_language is None
        and retry_language is not None
        and (
            intent.name == "unknown"
            or (different_language and low_confidence)
        )
    )

    if not should_retry:
        return first, intent

    retry = stt.transcribe(audio, language=retry_language)
    retry_intent = route(retry.text) if retry.text else ToolIntent("unknown")

    if log:
        log(
            f"[STT] retry language={retry_language} "
            f"text={retry.text!r} logprob={retry.avg_logprob}"
        )

    if retry_intent.name != "unknown":
        return retry, retry_intent

    return first, intent
