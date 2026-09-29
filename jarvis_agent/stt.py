from __future__ import annotations

import numpy as np

from .audio import save_temp_wav
from .config import settings


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

    def transcribe(self, audio: np.ndarray) -> tuple[str, str | None]:
        path = save_temp_wav(audio)
        try:
            model = self._get_model()
            segments, info = model.transcribe(
                str(path),
                beam_size=3,
                language=settings.stt_language,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            text = " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip()
            ).strip()
            return text, getattr(info, "language", None)
        finally:
            path.unlink(missing_ok=True)
