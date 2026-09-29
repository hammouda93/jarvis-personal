from __future__ import annotations

import threading
import time
import traceback

from .audio import record_utterance, wait_for_double_clap
from .config import settings
from .language import normalize_language, tool_message
from .recognition import recognize_command
from .stt import LocalWhisperSTT
from .tools import execute
from .tts import ElevenLabsTTS


def run_headless() -> int:
    """Run the complete voice pipeline without any graphical UI dependency."""
    stop_event = threading.Event()
    stt = LocalWhisperSTT()
    tts = ElevenLabsTTS()
    preferred_language = "fr"

    def level(_value: float) -> None:
        return

    def status(message: str) -> None:
        print(f"[STATUS] {message}")

    print("=" * 68)
    print("JARVIS VOICE CORE — HEADLESS TEST")
    print("French / English / Arabic")
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
                continue

            print(
                f"[STATE] transcribing — model={settings.whisper_model} "
                f"device={settings.whisper_device}"
            )
            transcript, intent = recognize_command(
                stt,
                audio,
                log=print,
                preferred_language=preferred_language,
            )
            if not transcript.text:
                print("[ERROR] transcription vide")
                continue

            preferred_language = normalize_language(transcript.language)
            print(f"[YOU] {transcript.text} [lang={preferred_language}]")
            print(f"[INTENT] {intent.name} {intent.args}")

            print("[STATE] acting")
            result = execute(intent)
            print(
                f"[TOOL] success={result.success} "
                f"detail={result.detail!r}"
            )

            spoken = tool_message(intent, result, preferred_language)
            print(f"[TTS] {spoken}")
            tts.speak(spoken, on_level=level)

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
