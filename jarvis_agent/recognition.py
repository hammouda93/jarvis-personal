from __future__ import annotations

from typing import Callable

import numpy as np

from .config import settings
from .stt import LocalWhisperSTT, TranscriptResult
from .tools import ToolIntent, route


LogFn = Callable[[str], None]


def _is_action_intent(intent: ToolIntent) -> bool:
    return intent.name in {
        "browser.open_url",
        "browser.search",
        "app.open",
        "folder.open",
        "system.time",
        "assistant.stop",
        "assistant.sleep",
    }


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
    low_language_confidence = (
        probability is None
        or probability < settings.stt_retry_language_probability
    )
    low_text_confidence = (
        first.avg_logprob is not None
        and first.avg_logprob < -0.70
    )
    different_language = (
        retry_language is not None
        and first.language is not None
        and first.language != retry_language
    )

    # A short hallucinated transcript must never become a PC action merely
    # because it happens to contain "ouvre Chrome". For actionable intents,
    # weak language/text confidence triggers a second decode in the preferred
    # command language. If the two decodes disagree, prefer the safer retry.
    should_retry = (
        settings.stt_language is None
        and retry_language is not None
        and (
            intent.name == "unknown"
            or (
                _is_action_intent(intent)
                and (
                    different_language
                    or low_language_confidence
                    or low_text_confidence
                )
            )
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

    if intent.name == "unknown":
        if retry.text:
            return retry, retry_intent
        return first, intent

    # First pass looked actionable but was low-confidence. Do not execute it
    # unless the preferred-language pass supports the same action. If the retry
    # says something else (including a normal question), use the retry instead.
    if retry.text:
        return retry, retry_intent

    return first, ToolIntent("unknown", {"text": first.text})
