import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.native_tools import NativeToolRegistry
from jarvis_agent.windows_perception import (
    close_tab,
    inspect_active_window,
    type_text_active_window,
    write_ui_element,
    _control_value,
    _uia_window_from_native_with_retry,
    _score_name,
    _title_app_hint,
    _window_identity_score,
)


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


class _FakeTab:
    element_info = SimpleNamespace(
        name="",
        control_type="TabItem",
        automation_id="",
    )

    def __init__(self, name, selected=False, top=0):
        self.name = name
        self.selected = selected
        self.clicked = False
        self.top = top

    def window_text(self):
        return self.name

    def is_visible(self):
        return True

    def is_selected(self):
        return self.selected

    def click_input(self):
        self.clicked = True
        self.selected = True

    def rectangle(self):
        return SimpleNamespace(
            left=20,
            top=self.top,
            right=240,
            bottom=self.top + 48,
        )


class _FakeTabWindow:
    element_info = SimpleNamespace(
        name="Google Chrome",
        control_type="Window",
        automation_id="",
    )

    def __init__(self, tabs):
        self.tabs = tabs

    def window_text(self):
        return "Google Chrome"

    def descendants(self):
        return list(self.tabs)

    def rectangle(self):
        return SimpleNamespace(left=0, top=0, right=1200, bottom=900)


class _FakeComboBox:
    element_info = SimpleNamespace(
        name="Search",
        control_type="ComboBox",
        automation_id="searchbox",
    )

    def __init__(self):
        self.typed = []
        self.focused = False
        self.clicked = False

    def window_text(self):
        return "Search"

    def rectangle(self):
        return _FakeRect()

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def descendants(self):
        return []

    def set_focus(self):
        self.focused = True

    def click_input(self):
        self.clicked = True

    def type_keys(self, value, **_kwargs):
        self.typed.append(value)


class _FakeText:
    element_info = SimpleNamespace(
        name="Sans titre",
        control_type="Text",
        automation_id="",
    )

    def window_text(self):
        return "Sans titre"

    def rectangle(self):
        return _FakeRect()

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def descendants(self):
        return []

    def set_focus(self):
        pass

    def click_input(self):
        pass

    def type_keys(self, *_args, **_kwargs):
        raise AssertionError("Text controls must never receive typed input")


class _FakeDocument:
    element_info = SimpleNamespace(
        name="Bonjour Jarvis",
        control_type="Document",
        automation_id="TextEditor",
    )

    def __init__(self, value="Bonjour Jarvis"):
        self.value = value
        self.focused = False

    def window_text(self):
        return self.value

    def get_value(self):
        return self.value

    def rectangle(self):
        return _FakeRect()

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def descendants(self):
        return []

    def set_focus(self):
        self.focused = True

    def set_edit_text(self, value):
        self.value = value


class _FakeNoValuePatternDocument:
    element_info = SimpleNamespace(
        name="Visible document text",
        control_type="Document",
        automation_id="TextEditor",
    )

    @property
    def iface_value(self):
        raise RuntimeError("ValuePattern unavailable")

    def window_text(self):
        return "Visible document text"

    def legacy_properties(self):
        return {}


class _FakeClipboard:
    CF_UNICODETEXT = 13
    value = "previous"

    @classmethod
    def OpenClipboard(cls):
        return None

    @classmethod
    def CloseClipboard(cls):
        return None

    @classmethod
    def IsClipboardFormatAvailable(cls, _fmt):
        return True

    @classmethod
    def GetClipboardData(cls, _fmt):
        return cls.value

    @classmethod
    def EmptyClipboard(cls):
        cls.value = ""

    @classmethod
    def SetClipboardText(cls, value, _fmt):
        cls.value = value


class WindowsPerceptionTests(unittest.TestCase):
    def test_window_identity_matches_localized_setup_titles(self):
        self.assertGreaterEqual(
            _window_identity_score(
                "Cursor Setup",
                "Installation - Cursor (User)",
            ),
            0.90,
        )
        self.assertGreaterEqual(
            _window_identity_score(
                "Installation Cursor",
                "Installation - Cursor (User)",
            ),
            0.90,
        )
        self.assertGreaterEqual(
            _window_identity_score(
                "CursorUserSetup",
                "Installation - Cursor (User)",
            ),
            0.90,
        )
        self.assertLess(
            _window_identity_score("Search", "(5) YouTube - Google Chrome"),
            0.82,
        )

    @patch("jarvis_agent.windows_perception.time.sleep")
    @patch("jarvis_agent.windows_perception._send_keys")
    @patch("jarvis_agent.windows_perception._active_window")
    def test_close_tab_ignores_page_internal_tabitems(
        self,
        active_window_mock,
        send_keys_mock,
        sleep_mock,
    ):
        browser_tab = _FakeTab("(5) YouTube - Utilisation mémoire", top=4)
        shorts_filter = _FakeTab("Shorts", top=236)
        all_filter = _FakeTab("Tout", top=236)
        before = _FakeTabWindow([all_filter, shorts_filter, browser_tab])
        after = _FakeTabWindow([all_filter, shorts_filter])
        active_window_mock.side_effect = [before, after]

        result = close_tab("YouTube")

        self.assertTrue(result.success)
        self.assertTrue(browser_tab.clicked)
        self.assertFalse(shorts_filter.clicked)
        self.assertFalse(all_filter.clicked)
        send_keys_mock.assert_called_once_with("^w")

    @patch("jarvis_agent.windows_perception.time.sleep")
    @patch("jarvis_agent.windows_perception._send_keys")
    @patch("jarvis_agent.windows_perception._active_window")
    def test_close_named_tab_does_not_use_close_window(
        self,
        active_window_mock,
        send_keys_mock,
        sleep_mock,
    ):
        youtube = _FakeTab("(5) YouTube - Utilisation mémoire")
        pointer = _FakeTab("Pointer GitHub puis tester")
        before = _FakeTabWindow([pointer, youtube])
        after = _FakeTabWindow([pointer])
        active_window_mock.side_effect = [before, after]

        result = close_tab("YouTube")

        self.assertTrue(result.success)
        self.assertTrue(youtube.clicked)
        send_keys_mock.assert_called_once_with("^w")
        self.assertIn('"verified":true', result.detail)

    @patch("jarvis_agent.windows_perception.time.sleep")
    @patch("jarvis_agent.windows_perception._send_keys")
    @patch("jarvis_agent.windows_perception.activate_window")
    def test_focused_window_typing_fallback_pastes_text(
        self,
        activate_mock,
        send_keys_mock,
        sleep_mock,
    ):
        activate_mock.return_value = SimpleNamespace(
            success=True,
            message="ok",
            detail="",
        )
        with patch.dict(sys.modules, {"win32clipboard": _FakeClipboard}):
            result = type_text_active_window(
                "Bonjour Jarvis",
                title="Bloc-notes",
                mode="replace",
            )

        self.assertTrue(result.success)
        activate_mock.assert_called_once_with("Bloc-notes")
        self.assertEqual(
            [call.args[0] for call in send_keys_mock.call_args_list],
            ["^a", "^v"],
        )
        self.assertIn('"verified":false', result.detail)
        self.assertEqual(_FakeClipboard.value, "previous")

    @patch("jarvis_agent.windows_perception.time.sleep")
    @patch("jarvis_agent.windows_perception._uia_window_from_handle")
    def test_uia_native_retry_recovers_after_transient_winerror(
        self,
        attach_mock,
        sleep_mock,
    ):
        attach_mock.side_effect = [
            OSError(6, "Descripteur non valide"),
            OSError(6, "Descripteur non valide"),
            _FakeWindow(),
        ]

        window = _uia_window_from_native_with_retry(
            {"handle": 4242, "title": "Bloc-notes"},
            attempts=4,
            delay_s=0.01,
        )

        self.assertIsInstance(window, _FakeWindow)
        self.assertEqual(attach_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_control_value_tolerates_missing_uia_value_pattern(self):
        wrapper = _FakeNoValuePatternDocument()

        value = _control_value(wrapper)

        self.assertEqual(value, "Visible document text")

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

    def test_title_app_hint_survives_changing_browser_page(self):
        self.assertEqual(
            _title_app_hint(
                "Pointer GitHub puis tester - Google Chrome"
            ),
            "Google Chrome",
        )
        self.assertEqual(
            _title_app_hint(
                ".env - jarvis-main - Visual Studio Code"
            ),
            "Visual Studio Code",
        )

    @patch("jarvis_agent.windows_perception._snapshot_element")
    def test_write_ui_element_types_into_combobox_fallback(
        self,
        snapshot_mock,
    ):
        combo = _FakeComboBox()
        snapshot_mock.return_value = combo

        result = write_ui_element(
            "",
            "Sports et intelligence artificielle",
            ref="e3",
        )

        self.assertTrue(result.success)
        self.assertTrue(combo.focused)
        self.assertTrue(combo.clicked)
        self.assertEqual(
            combo.typed[-1],
            "Sports et intelligence artificielle",
        )

    @patch("jarvis_agent.windows_perception._snapshot_element")
    def test_write_ui_element_rejects_plain_text_label(
        self,
        snapshot_mock,
    ):
        snapshot_mock.return_value = _FakeText()

        result = write_ui_element(
            "",
            "bonjour Jarvis",
            ref="e7",
        )

        self.assertFalse(result.success)
        self.assertIn("Text", result.detail)

    def test_window_score_ignores_hyphen_typography(self):
        self.assertGreaterEqual(
            _score_name("Bloc‑notes", "Sans titre – Bloc-notes"),
            0.94,
        )

    @patch("jarvis_agent.windows_perception._send_keys")
    def test_press_key_allows_safe_browser_back_navigation(self, send_mock):
        from jarvis_agent.windows_perception import press_key

        result = press_key("Alt+Left")

        self.assertTrue(result.success)
        send_mock.assert_called_once_with("%{LEFT}")

    @patch("jarvis_agent.windows_perception._snapshot_element")
    def test_append_write_preserves_existing_document_text(
        self,
        snapshot_mock,
    ):
        document = _FakeDocument("Bonjour Jarvis")
        snapshot_mock.return_value = document

        result = write_ui_element(
            "",
            " Test après texte existant",
            ref="e7",
            mode="append",
        )

        self.assertTrue(result.success)
        self.assertEqual(
            document.value,
            "Bonjour Jarvis Test après texte existant",
        )
        self.assertIn('"verified":true', result.detail)
        self.assertIn('"mode":"append"', result.detail)

    def test_title_app_hint_handles_windows_en_dash(self):
        self.assertEqual(
            _title_app_hint("*Bonjour Jarvis – Bloc-notes"),
            "Bloc-notes",
        )

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
