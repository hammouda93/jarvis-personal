import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.native_tools import NativeToolRegistry
from jarvis_agent.windows_perception import inspect_active_window, _score_name


class _FakeRect:
    left = 10
    top = 20
    right = 810
    bottom = 620


class _FakeWindow:
    handle = 4242
    element_info = SimpleNamespace(
        name="Chrome - Test",
        control_type="Window",
        automation_id="",
    )

    def window_text(self):
        return "Chrome - Test"

    def rectangle(self):
        return _FakeRect()

    def descendants(self):
        return []


class WindowsPerceptionTests(unittest.TestCase):
    @patch("jarvis_agent.windows_perception._uia_window_from_handle")
    @patch("jarvis_agent.windows_perception._native_target_window")
    @patch("jarvis_agent.windows_perception._active_window")
    def test_inspection_recovers_from_uia_enumeration_failure(
        self,
        active_mock,
        native_mock,
        attach_mock,
    ):
        active_mock.side_effect = OSError(6, "Descripteur non valide")
        native_mock.return_value = {
            "handle": 4242,
            "title": "Chrome - Test",
            "bounds": (10, 20, 810, 620),
        }
        attach_mock.return_value = _FakeWindow()

        result = inspect_active_window()

        self.assertTrue(result.success)
        self.assertIn("Chrome - Test", result.detail)
        self.assertIn("win32_handle_to_uia", result.detail)
        attach_mock.assert_called_once_with(4242)

    @patch("jarvis_agent.windows_perception._uia_window_from_handle")
    @patch("jarvis_agent.windows_perception._native_target_window")
    @patch("jarvis_agent.windows_perception._active_window")
    def test_inspection_returns_native_window_when_uia_attach_fails(
        self,
        active_mock,
        native_mock,
        attach_mock,
    ):
        active_mock.side_effect = OSError(6, "Descripteur non valide")
        native_mock.return_value = {
            "handle": 4242,
            "title": "Cursor - Project",
            "bounds": (0, 0, 1200, 800),
        }
        attach_mock.side_effect = OSError(6, "Descripteur non valide")

        result = inspect_active_window()

        self.assertTrue(result.success)
        self.assertIn("Cursor - Project", result.detail)
        self.assertIn("win32_window_only", result.detail)
        self.assertIn('"controls":[]', result.detail)

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

    def test_ui_tools_accept_snapshot_refs_and_named_inspection(self):
        tools = {
            item["function"]["name"]: item["function"]["parameters"]
            for item in NativeToolRegistry().ollama_tools()
        }
        self.assertIn(
            "title",
            tools["inspect_active_window"]["properties"],
        )
        self.assertIn(
            "ref",
            tools["click_ui_element"]["properties"],
        )
        self.assertIn(
            "ref",
            tools["write_ui_element"]["properties"],
        )

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
