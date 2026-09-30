import unittest

from jarvis_agent.stt import LocalWhisperSTT


class STTFilterTests(unittest.TestCase):
    def test_amara_subtitle_hallucination_is_rejected(self):
        reason = LocalWhisperSTT._hallucination_reason(
            "Sous-titres réalisés par la communauté d'Amara.org"
        )
        self.assertEqual(reason, "known_whisper_hallucination")

    def test_normal_sentence_is_not_hallucination(self):
        reason = LocalWhisperSTT._hallucination_reason(
            "Ouvre Chrome et recherche les agents IA"
        )
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
