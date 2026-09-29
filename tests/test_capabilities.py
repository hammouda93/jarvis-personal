import unittest

from jarvis_agent.capabilities import (
    confirmation_prompt,
    detect_missing_capability,
    is_affirmative,
    is_negative,
)
from jarvis_agent.tools import ToolIntent


class CapabilityTests(unittest.TestCase):
    def test_youtube_confirmation_is_precise(self):
        intent = ToolIntent(
            "browser.open_url",
            {"url": "https://www.youtube.com"},
        )
        self.assertEqual(
            confirmation_prompt(intent),
            "Voulez-vous que j'ouvre YouTube ?",
        )

    def test_detect_vlc_missing_capability(self):
        result = detect_missing_capability("Ouvre VLC Media Player")
        self.assertIsNotNone(result)
        self.assertEqual(result.key, "app.vlc")

    def test_detect_whatsapp_missing_capability(self):
        result = detect_missing_capability(
            "Envoie un message WhatsApp à mon ami"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.key, "communication.whatsapp")

    def test_detect_weather_missing_capability(self):
        result = detect_missing_capability(
            "Quelle est la météo de demain ?"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.key, "weather.current")

    def test_confirmation_yes_no(self):
        self.assertTrue(is_affirmative("Oui vas-y"))
        self.assertTrue(is_negative("Non merci"))


if __name__ == "__main__":
    unittest.main()
