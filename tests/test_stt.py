import unittest
from unittest.mock import patch

from jarvis_agent.stt import LocalWhisperSTT, _stt_grounding_prompt


class STTGuardTests(unittest.TestCase):
    @patch("jarvis_agent.stt.application_speech_hints")
    def test_grounding_prompt_includes_installed_app_names(self, hints_mock):
        hints_mock.return_value = (
            "WhatsApp",
            "Visual Studio Code",
        )

        prompt = _stt_grounding_prompt()

        self.assertIn("WhatsApp", prompt)
        self.assertIn("Visual Studio Code", prompt)
        self.assertIn("assistant Windows", prompt)

    def test_punctuation_only_transcript_is_rejected(self):
        self.assertEqual(
            LocalWhisperSTT._hallucination_reason("..."),
            "punctuation_only",
        )

    def test_normal_short_confirmation_is_not_rejected(self):
        self.assertIsNone(
            LocalWhisperSTT._hallucination_reason("oui")
        )


if __name__ == "__main__":
    unittest.main()
