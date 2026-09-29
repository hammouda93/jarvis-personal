from __future__ import annotations

import hashlib
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd

from .audio import output_device_index, rms_mono
from .config import PROJECT_ROOT, settings


LevelCallback = Callable[[float], None]


class ElevenLabsTTS:
    """ElevenLabs provider with local WAV cache and live UI amplitude callback."""

    def __init__(self) -> None:
        self.cache_dir = PROJECT_ROOT / ".cache" / "jarvis_tts"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _pcm_sample_rate(output_format: str) -> int:
        if output_format.startswith("pcm_"):
            try:
                return int(output_format.split("_", 1)[1])
            except (ValueError, IndexError):
                pass
        return 24000

    def _cache_path(self, text: str) -> Path:
        payload = (
            f"{text}|{settings.elevenlabs_voice_id}|"
            f"{settings.elevenlabs_model_id}|{settings.elevenlabs_output_format}"
        ).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()[:24]
        return self.cache_dir / f"{digest}.wav"

    def _load_cached(self, path: Path) -> tuple[np.ndarray, int] | None:
        try:
            with wave.open(str(path), "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                    return None
                rate = wf.getframerate()
                raw = wf.readframes(wf.getnframes())
            if not raw:
                return None
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            return audio, rate
        except (OSError, wave.Error):
            return None

    def _save_wav(self, path: Path, raw: bytes, rate: int) -> None:
        tmp = path.with_suffix(".tmp.wav")
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(raw)
        tmp.replace(path)

    def _play(
        self,
        audio: np.ndarray,
        rate: int,
        on_level: LevelCallback | None = None,
    ) -> None:
        block = 960
        device = output_device_index()

        with sd.OutputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            device=device,
        ) as stream:
            for start in range(0, len(audio), block):
                chunk = audio[start : start + block]
                if chunk.size == 0:
                    continue
                stream.write(chunk.reshape(-1, 1))
                if on_level:
                    on_level(float(min(1.0, rms_mono(chunk) / 0.15)))

        if on_level:
            on_level(0.0)

    def speak(
        self,
        text: str,
        *,
        on_level: LevelCallback | None = None,
    ) -> None:
        text = (text or "").strip()
        if not text:
            return

        if not settings.elevenlabs_api_key or not settings.elevenlabs_voice_id:
            print(f"Jarvis: {text}")
            return

        cache = self._cache_path(text)
        cached = self._load_cached(cache) if cache.is_file() else None
        if cached is not None:
            audio, rate = cached
            self._play(audio, rate, on_level)
            return

        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        chunks = client.text_to_speech.convert(
            voice_id=settings.elevenlabs_voice_id,
            text=text,
            model_id=settings.elevenlabs_model_id,
            output_format=settings.elevenlabs_output_format,
        )
        raw = b"".join(chunks)
        if not raw:
            raise RuntimeError("ElevenLabs returned empty audio")

        rate = self._pcm_sample_rate(settings.elevenlabs_output_format)
        try:
            self._save_wav(cache, raw, rate)
        except OSError:
            pass

        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        self._play(audio, rate, on_level)
