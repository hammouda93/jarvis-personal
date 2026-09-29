from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import save_temp_wav
from .config import settings


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str | None
    language_probability: float | None
    avg_logprob: float | None


class LocalWhisperSTT:
    """Lazy-loaded local STT provider using faster-whisper."""

    def __init__(self) -> None:
        self._model = None

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                settings.whisper_model,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
        return self._model

    @staticmethod
    def _dedupe_exact_repeat(text: str) -> str:
        words = text.split()
        if len(words) >= 4 and len(words) % 2 == 0:
            half = len(words) // 2
            if words[:half] == words[half:]:
                return " ".join(words[:half])
        return text

    def _decode(self, path, *, language: str | None) -> TranscriptResult:
        model = self._get_model()

        # Our own recorder already trims the utterance. Keeping Whisper's VAD
        # enabled here could remove short commands such as "ouvre YouTube".
        segments, info = model.transcribe(
            str(path),
            beam_size=5,
            language=language,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0.0,
            initial_prompt=settings.stt_initial_prompt or None,
        )

        segment_list = list(segments)
        text = " ".join(
            segment.text.strip()
            for segment in segment_list
            if segment.text.strip()
        ).strip()
        text = self._dedupe_exact_repeat(text)

        logprobs = [
            float(segment.avg_logprob)
            for segment in segment_list
            if getattr(segment, "avg_logprob", None) is not None
        ]
        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else None

        return TranscriptResult(
            text=text,
            language=getattr(info, "language", language),
            language_probability=getattr(info, "language_probability", None),
            avg_logprob=avg_logprob,
        )

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None = None,
    ) -> TranscriptResult:
        path = save_temp_wav(audio)
        try:
            effective_language = (
                language if language is not None else settings.stt_language
            )
            return self._decode(path, language=effective_language)
        finally:
            path.unlink(missing_ok=True)
