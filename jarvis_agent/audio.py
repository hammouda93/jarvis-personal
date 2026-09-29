from __future__ import annotations

import collections
import os
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd

from .config import settings


LevelCallback = Callable[[float], None]
StatusCallback = Callable[[str], None]


def rms_mono(block: np.ndarray) -> float:
    if block.ndim > 1:
        data = np.mean(block.astype(np.float64), axis=1)
    else:
        data = block.astype(np.float64)
    if data.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(data**2)))


def normalized_level(rms: float, reference: float = 0.08) -> float:
    return float(max(0.0, min(1.0, rms / max(reference, 1e-6))))


def _audio_devices(input_device: bool) -> list[tuple[int, dict]]:
    key = "max_input_channels" if input_device else "max_output_channels"
    return [
        (i, dev)
        for i, dev in enumerate(sd.query_devices())
        if int(dev[key]) >= 1
    ]


def _resolve_device(spec: str, *, input_device: bool) -> int | None:
    spec = (spec or "").strip()
    if not spec:
        default_idx = sd.default.device[0 if input_device else 1]
        return int(default_idx) if default_idx is not None and default_idx >= 0 else None

    if spec.isdigit():
        idx = int(spec)
        sd.query_devices(idx)
        return idx

    needle = spec.lower()
    for idx, dev in _audio_devices(input_device):
        if needle in str(dev["name"]).lower():
            return idx

    kind = "input" if input_device else "output"
    raise RuntimeError(f"No {kind} audio device matches {spec!r}")


def input_device_index() -> int | None:
    return _resolve_device(settings.input_device, input_device=True)


def output_device_index() -> int | None:
    return _resolve_device(settings.output_device, input_device=False)


def block_samples() -> int:
    return max(1, int(settings.sample_rate * settings.block_ms / 1000))


def wait_for_double_clap(
    stop_event: threading.Event,
    *,
    on_level: LevelCallback | None = None,
    on_status: StatusCallback | None = None,
) -> bool:
    """Wait for a double clap. Returns False when shutdown is requested."""
    blocksize = block_samples()
    device = input_device_index()

    if on_status:
        on_status("Calibration du microphone…")

    with sd.InputStream(
        device=device,
        samplerate=settings.sample_rate,
        channels=1,
        dtype="float32",
        blocksize=blocksize,
    ) as stream:
        warmup_blocks = max(
            1, int(settings.clap_warmup_s * settings.sample_rate / blocksize)
        )
        warmup_levels: list[float] = []

        for _ in range(warmup_blocks):
            if stop_event.is_set():
                return False
            data, overflowed = stream.read(blocksize)
            if overflowed:
                continue
            level = rms_mono(data)
            warmup_levels.append(level)
            if on_level:
                on_level(normalized_level(level))

        noise_floor = max(
            float(np.median(warmup_levels)) if warmup_levels else 1e-4,
            1e-4,
        )
        first_clap_time: float | None = None
        armed = True

        if on_status:
            on_status("Prêt — double clap pour réveiller Jarvis")

        while not stop_event.is_set():
            data, overflowed = stream.read(blocksize)
            if overflowed:
                continue

            level = rms_mono(data)
            if on_level:
                on_level(normalized_level(level))

            quiet_gate = noise_floor * settings.clap_quiet_gate_mult
            if level < quiet_gate:
                alpha = settings.clap_noise_floor_alpha
                noise_floor = alpha * noise_floor + (1.0 - alpha) * level
                noise_floor = max(noise_floor, 1e-7)

            threshold = max(
                noise_floor * settings.clap_spike_ratio,
                settings.clap_min_rms,
            )
            retrigger_level = threshold * settings.clap_retrigger_ratio
            now = time.monotonic()

            if level < retrigger_level:
                armed = True

            if not armed or level < threshold:
                continue

            armed = False
            if first_clap_time is None:
                first_clap_time = now
                continue

            gap = now - first_clap_time
            if gap < settings.clap_min_gap_s:
                continue
            if gap <= settings.clap_max_gap_s:
                if on_status:
                    on_status(f"Réveil détecté · {gap:.3f}s")
                return True

            first_clap_time = now

    return False


def record_utterance(
    stop_event: threading.Event,
    *,
    on_level: LevelCallback | None = None,
    on_status: StatusCallback | None = None,
) -> np.ndarray | None:
    """Record one phrase, starting on voice activity and ending after silence."""
    blocksize = block_samples()
    device = input_device_index()

    calibration_blocks = max(1, int(0.45 * settings.sample_rate / blocksize))
    pre_roll_blocks = max(1, int(0.30 * settings.sample_rate / blocksize))
    silence_blocks_needed = max(
        1, int(settings.speech_silence_s * settings.sample_rate / blocksize)
    )
    max_blocks = max(
        1, int(settings.speech_max_duration_s * settings.sample_rate / blocksize)
    )

    with sd.InputStream(
        device=device,
        samplerate=settings.sample_rate,
        channels=1,
        dtype="float32",
        blocksize=blocksize,
    ) as stream:
        calibration: list[float] = []
        for _ in range(calibration_blocks):
            if stop_event.is_set():
                return None
            data, _ = stream.read(blocksize)
            level = rms_mono(data)
            calibration.append(level)
            if on_level:
                on_level(normalized_level(level))

        noise = float(np.median(calibration)) if calibration else 0.001
        threshold = max(
            settings.speech_min_rms,
            noise * settings.speech_threshold_multiplier,
        )

        if on_status:
            on_status("Je vous écoute…")

        pre_roll: collections.deque[np.ndarray] = collections.deque(
            maxlen=pre_roll_blocks
        )
        captured: list[np.ndarray] = []
        started = False
        silent_blocks = 0
        total_blocks = 0
        deadline = time.monotonic() + settings.speech_start_timeout_s

        while total_blocks < max_blocks and not stop_event.is_set():
            data, overflowed = stream.read(blocksize)
            total_blocks += 1
            if overflowed:
                continue

            mono = data[:, 0].astype(np.float32, copy=True)
            level = rms_mono(mono)
            if on_level:
                on_level(
                    normalized_level(level, reference=max(threshold * 5.0, 0.05))
                )

            if not started:
                pre_roll.append(mono)
                if level >= threshold:
                    started = True
                    captured.extend(list(pre_roll))
                    pre_roll.clear()
                    captured.append(mono)
                    silent_blocks = 0
                    if on_status:
                        on_status("Voix détectée")
                elif time.monotonic() >= deadline:
                    return None
                continue

            captured.append(mono)

            if level < threshold * 0.75:
                silent_blocks += 1
            else:
                silent_blocks = 0

            if silent_blocks >= silence_blocks_needed:
                break

    if not captured:
        return None

    audio = np.concatenate(captured).astype(np.float32, copy=False)
    if audio.size < int(settings.sample_rate * 0.20):
        return None
    return audio


def save_temp_wav(audio: np.ndarray) -> Path:
    pcm = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)

    fd, name = tempfile.mkstemp(prefix="jarvis_", suffix=".wav")
    os.close(fd)
    path = Path(name)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(settings.sample_rate)
        wf.writeframes(pcm16.tobytes())
    return path
