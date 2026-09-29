from __future__ import annotations

import threading
import time
import traceback

from .audio import record_utterance, wait_for_double_clap
from .config import settings
from .stt import LocalWhisperSTT
from .tools import execute, route
from .tts import ElevenLabsTTS


def run_headless() -> int:
    """Run the complete voice pipeline without any graphical UI dependency."""
    stop_event = threading.Event()
    stt = LocalWhisperSTT()
    tts = ElevenLabsTTS()

    def level(_value: float) -> None:
        # Reserved for future console meter / remote presence bridge.
        return

    def status(message: str) -> None:
        print(f"[STATUS] {message}")

    print("=" * 68)
    print("JARVIS VOICE CORE — HEADLESS TEST")
    print("double clap -> TTS -> listen -> local STT -> tool -> TTS")
    print("=" * 68)

    try:
        while not stop_event.is_set():
            print("[STATE] calibrating")
            detected = wait_for_double_clap(
                stop_event,
                on_level=level,
                on_status=status,
                on_armed=lambda: print(
                    "[STATE] armed — double clap pour réveiller Jarvis"
                ),
            )
            if not detected:
                break

            print("[STATE] wake")
            print("[WAKE] double clap")
            print(f"[TTS] {settings.wake_phrase}")
            tts.speak(settings.wake_phrase, on_level=level)

            print("[STATE] listening")
            audio = record_utterance(
                stop_event,
                on_level=level,
                on_status=status,
            )
            if audio is None:
                print("[ERROR] aucune phrase détectée")
                tts.speak("Je ne vous ai pas entendu.", on_level=level)
                continue

            print(
                f"[STATE] transcribing — model={settings.whisper_model} "
                f"device={settings.whisper_device}"
            )
            text, language = stt.transcribe(audio)
            if not text:
                print("[ERROR] transcription vide")
                tts.speak("Je n'ai pas compris.", on_level=level)
                continue

            print(f"[YOU] {text}")
            if language:
                print(f"[STT] language={language}")

            print("[STATE] understanding")
            intent = route(text)
            print(f"[INTENT] {intent.name} {intent.args}")

            print("[STATE] acting")
            result = execute(intent)
            print(
                f"[TOOL] success={result.success} "
                f"detail={result.detail!r}"
            )

            print(f"[TTS] {result.message}")
            tts.speak(result.message, on_level=level)

            if result.should_exit:
                stop_event.set()
                break

            print("[STATE] success" if result.success else "[STATE] error")
            time.sleep(0.6)

    except KeyboardInterrupt:
        print("\n[STOP] Jarvis arrêté.")
        return 0
    except Exception as exc:
        print(f"[FATAL] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    return 0
