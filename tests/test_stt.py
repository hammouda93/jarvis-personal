import unittest

from jarvis_agent.stt import LocalWhisperSTT


class STTGuardTests(unittest.TestCase):
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
