import unittest

from jarvis_agent.language import normalize_language, tool_message
from jarvis_agent.tools import ToolIntent, ToolResult


class LanguageTests(unittest.TestCase):
    def test_normalize_supported_languages(self):
        self.assertEqual(normalize_language("fr-FR"), "fr")
        self.assertEqual(normalize_language("en-US"), "en")
        self.assertEqual(normalize_language("ar-TN"), "ar")

    def test_tool_success_english(self):
        message = tool_message(
            ToolIntent("app.open", {"app": "chrome"}),
            ToolResult(True, "C'est fait."),
            "en",
        )
        self.assertEqual(message, "Done.")

    def test_tool_success_arabic(self):
        message = tool_message(
            ToolIntent("app.open", {"app": "chrome"}),
            ToolResult(True, "C'est fait."),
            "ar",
        )
        self.assertEqual(message, "تم.")

    def test_search_arabic(self):
        message = tool_message(
            ToolIntent("browser.search", {"query": "وكلاء الذكاء الاصطناعي"}),
            ToolResult(True, "C'est fait."),
            "ar",
        )
        self.assertIn("وكلاء الذكاء الاصطناعي", message)


if __name__ == "__main__":
    unittest.main()
