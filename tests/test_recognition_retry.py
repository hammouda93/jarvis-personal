import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from jarvis_agent.recognition import recognize_command
from jarvis_agent.config import settings
from jarvis_agent.stt import TranscriptResult


class FakeSTT:
    def __init__(self, responses):
        self.responses = list(responses)
        self.languages = []

    def transcribe(self, audio, *, language=None):
        self.languages.append(language)
        return self.responses.pop(0)


class RecognitionRetryTests(unittest.TestCase):
    def test_weak_fixed_french_retries_auto_and_prefers_grounded_action(self):
        primary = TranscriptResult(
            text="Over YouTube.",
            language="fr",
            language_probability=1.0,
            avg_logprob=-0.90,
            no_speech_probability=0.05,
        )
        retry = TranscriptResult(
            text="Ouvre YouTube.",
            language="fr",
            language_probability=0.99,
            avg_logprob=-0.55,
            no_speech_probability=0.02,
        )
        fake = FakeSTT([primary, retry])
        with patch("jarvis_agent.recognition.settings", replace(settings, stt_language="fr")):
            transcript, intent = recognize_command(
                fake,
                np.zeros(10, dtype=np.float32),
                preferred_language="fr",
            )

        self.assertEqual(fake.languages, ["fr", "auto"])
        self.assertEqual(transcript.text, "Ouvre YouTube.")
        self.assertEqual(intent.name, "browser.open_url")


if __name__ == "__main__":
    unittest.main()
