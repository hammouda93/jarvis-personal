from __future__ import annotations

from typing import Callable

import numpy as np

from .config import settings
from .stt import LocalWhisperSTT, TranscriptResult
from .tools import ToolIntent, route


LogFn = Callable[[str], None]


def _is_pc_action(intent: ToolIntent) -> bool:
    return intent.name in {
        "browser.open_url",
        "browser.search",
        "app.open",
        "folder.open",
        "folder.open_named",
        "assistant.stop",
    }


def _language_code(value: str | None) -> str | None:
    if not value:
        return None
    return value.lower().split("-")[0]


def _candidate_score(
    transcript: TranscriptResult,
    intent: ToolIntent,
    *,
    preferred_language: str | None,
) -> float:
    if not transcript.text or transcript.rejected_reason:
        return -999.0

    score = transcript.avg_logprob if transcript.avg_logprob is not None else -1.20

    lang = _language_code(transcript.language)
    preferred = _language_code(preferred_language)

    if lang in settings.stt_supported_languages:
        score += 0.08
    if preferred and lang == preferred:
        score += 0.06
    if intent.name != "unknown":
        score += 0.10

    if transcript.no_speech_probability is not None:
        score -= max(0.0, transcript.no_speech_probability - 0.20) * 0.8

    return score


def _needs_multilingual_retry(transcript: TranscriptResult) -> bool:
    if not transcript.text or transcript.rejected_reason:
        return True

    lang = _language_code(transcript.language)
    probability = transcript.language_probability
    weak_language = (
        probability is None
        or probability < settings.stt_language_confidence
    )
    weak_text = (
        transcript.avg_logprob is not None
        and transcript.avg_logprob < -0.82
    )
    unsupported_language = lang not in settings.stt_supported_languages

    return weak_language or weak_text or unsupported_language


def recognize_command(
    stt: LocalWhisperSTT,
    audio: np.ndarray,
    *,
    log: LogFn | None = None,
    preferred_language: str | None = None,
) -> tuple[TranscriptResult, ToolIntent]:
    """Recognize one utterance in French, English or Arabic.

    The first pass is automatic. Only uncertain audio is decoded again with
    forced language candidates, avoiding the latency of three Whisper passes
    for every normal sentence.
    """
    configured_language = (settings.stt_language or "auto").lower()
    first_language = "auto" if configured_language == "auto" else configured_language

    primary = stt.transcribe(audio, language=first_language)
    primary_intent = route(primary.text) if primary.text else ToolIntent("unknown")

    candidates: list[tuple[TranscriptResult, ToolIntent]] = [
        (primary, primary_intent)
    ]

    if log:
        log(
            f"[STT] mode={configured_language} language={primary.language} "
            f"prob={primary.language_probability} "
            f"logprob={primary.avg_logprob} "
            f"no_speech={primary.no_speech_probability} "
            f"rejected={primary.rejected_reason}"
        )

    if configured_language == "auto" and _needs_multilingual_retry(primary):
        ordered_languages: list[str] = []
        preferred = _language_code(preferred_language)
        detected = _language_code(primary.language)

        for language in (
            preferred,
            detected,
            *settings.stt_supported_languages,
        ):
            if (
                language
                and language in settings.stt_supported_languages
                and language not in ordered_languages
            ):
                ordered_languages.append(language)

        for language in ordered_languages:
            retry = stt.transcribe(audio, language=language)
            retry_intent = route(retry.text) if retry.text else ToolIntent("unknown")
            candidates.append((retry, retry_intent))
            if log:
                log(
                    f"[STT] candidate language={language} "
                    f"text={retry.text!r} "
                    f"logprob={retry.avg_logprob} "
                    f"no_speech={retry.no_speech_probability} "
                    f"rejected={retry.rejected_reason}"
                )

    transcript, intent = max(
        candidates,
        key=lambda item: _candidate_score(
            item[0],
            item[1],
            preferred_language=preferred_language,
        ),
    )

    if log:
        log(
            f"[STT] selected language={transcript.language} "
            f"text={transcript.text!r}"
        )

    if not transcript.text:
        return transcript, ToolIntent("unknown")

    # Never let weak speech directly operate the computer. The text can still
    # go to the conversational brain, but PC actions need a reliable transcript.
    weak_text = (
        transcript.avg_logprob is not None
        and transcript.avg_logprob < -0.95
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
