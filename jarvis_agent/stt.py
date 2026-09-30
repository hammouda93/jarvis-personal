from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from .audio import save_temp_wav
from .config import settings
from .tools import application_speech_hints


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str | None
    language_probability: float | None
    avg_logprob: float | None
    no_speech_probability: float | None
    rejected_reason: str | None = None


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

    @staticmethod
    def _hallucination_reason(text: str) -> str | None:
        normalized = " ".join(
            text.lower().replace("’", "'").split()
        )
        common_false_transcripts = (
            "sous-titres réalisés par la communauté d'amara.org",
            "sous titres réalisés par la communauté d'amara.org",
            "amara.org",
            "merci d'avoir regardé cette vidéo",
            "merci d'avoir regardé",
        )
        for phrase in common_false_transcripts:
            if normalized == phrase or normalized.startswith(phrase + " "):
                return "known_whisper_hallucination"
        return None

    @staticmethod
    def _repetition_reason(text: str) -> str | None:
        words = [w.strip(".,!?;:").lower() for w in text.split() if w.strip()]
        if len(words) < 6:
            return None

        counts = Counter(words)
        most_common_word, count = counts.most_common(1)[0]
        if count >= 4 and count / len(words) >= 0.45:
            return f"repetition:{most_common_word}"

        # Detect repeated short phrases such as "www.cursor.com www.cursor.com".
        for size in (2, 3):
            if len(words) < size * 3:
                continue
            chunks = [
                tuple(words[i : i + size])
                for i in range(0, len(words) - size + 1, size)
            ]
            if chunks and len(set(chunks)) == 1 and len(chunks) >= 3:
                return "repeated_phrase"

        return None

    def _decode(self, path, *, language: str | None) -> TranscriptResult:
        model = self._get_model()

        app_hints = application_speech_hints()
        prompt_parts = [settings.stt_initial_prompt.strip()]
        if app_hints:
            prompt_parts.append(
                "Noms d'applications installées susceptibles d'être prononcés : "
                + ", ".join(app_hints)
                + "."
            )
        prompt_parts.append(
            "Contexte : assistant Windows. L'utilisateur peut demander d'ouvrir "
            "une application, une fenêtre ou un dossier, cliquer, écrire, fermer "
            "une fenêtre ou rechercher sur Internet."
        )
        initial_prompt = " ".join(part for part in prompt_parts if part)

        segments, info = model.transcribe(
            str(path),
            beam_size=5,
            language=language,
            vad_filter=False,
            condition_on_previous_text=False,
            temperature=0.0,
            initial_prompt=initial_prompt or None,
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
        no_speech_values = [
            float(segment.no_speech_prob)
            for segment in segment_list
            if getattr(segment, "no_speech_prob", None) is not None
        ]

        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else None
        no_speech_probability = (
            sum(no_speech_values) / len(no_speech_values)
            if no_speech_values
            else None
        )

        rejected_reason = self._hallucination_reason(text)
        if rejected_reason is None:
            rejected_reason = self._repetition_reason(text)
        if (
            rejected_reason is None
            and no_speech_probability is not None
            and no_speech_probability >= 0.72
        ):
            rejected_reason = "probable_silence"

        if rejected_reason:
            text = ""

        return TranscriptResult(
            text=text,
            language=getattr(info, "language", language),
            language_probability=getattr(info, "language_probability", None),
            avg_logprob=avg_logprob,
            no_speech_probability=no_speech_probability,
            rejected_reason=rejected_reason,
        )

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None = None,
    ) -> TranscriptResult:
        path = save_temp_wav(audio)
        try:
            requested = language if language is not None else settings.stt_language
            if (
                requested == "auto"
                and settings.stt_language not in {None, "", "auto"}
            ):
                requested = settings.stt_language
            effective_language = None if requested in {None, "", "auto"} else requested
            return self._decode(path, language=effective_language)
        finally:
            path.unlink(missing_ok=True)
