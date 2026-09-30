import unittest
from unittest.mock import patch

from jarvis_agent.native_tools import NativeToolRegistry
from jarvis_agent.windows_perception import _score_name


class WindowsPerceptionTests(unittest.TestCase):
    def test_exact_ui_label_scores_highest(self):
        self.assertEqual(_score_name("Paramètres", "Paramètres"), 1.0)

    def test_partial_ui_label_is_usable(self):
        self.assertGreaterEqual(
            _score_name("parametres", "Ouvrir les paramètres"),
            0.90,
        )

    def test_unrelated_ui_label_scores_low(self):
        self.assertLess(
            _score_name("Paramètres", "Accueil"),
            0.70,
        )

    def test_native_registry_exposes_perception_tools(self):
        names = {
            item["function"]["name"]
            for item in NativeToolRegistry().ollama_tools()
        }
        self.assertIn("list_windows", names)
        self.assertIn("inspect_active_window", names)
        self.assertIn("activate_window", names)
        self.assertIn("click_ui_element", names)
        self.assertIn("press_key", names)
        self.assertIn("write_ui_element", names)
        self.assertIn("close_window", names)

    @patch("jarvis_agent.native_tools.inspect_active_window")
    def test_inspection_result_reaches_model(self, inspect_mock):
        inspect_mock.return_value.success = True
        inspect_mock.return_value.message = "Fenêtre active inspectée."
        inspect_mock.return_value.detail = "window title Test"

        result = NativeToolRegistry().execute(
            "inspect_active_window",
            {},
        )

        self.assertTrue(result.success)
        self.assertIn("Test", result.detail)


if __name__ == "__main__":
    unittest.main()
