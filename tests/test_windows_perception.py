import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.native_tools import NativeToolRegistry
from jarvis_agent.cua_driver_bridge import CuaActionResult, CuaElement, CuaWindowSnapshot
import jarvis_agent.windows_perception as wp
from jarvis_agent.windows_perception import (
    click_ui_element,
    close_tab,
    inspect_active_window,
    type_text_active_window,
    write_ui_element,
    _bounded_descendants,
    _try_cua_inspection,
    _control_value,
    _probe_child_uia_fragments,
    _system_chrome_only,
    _native_target_window,
    _uia_window_from_native_with_retry,
    _score_name,
    _title_app_hint,
    _window_identity_score,
    _window_query_score,
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


class _FakeBoundedWindow:
    def __init__(self, count=20):
        self.count = count
        self.calls = []

    def iter_descendants(self, **kwargs):
        self.calls.append(dict(kwargs))
        if "cache_enable" in kwargs:
            raise TypeError("cache_enable must not reach legacy pywinauto traversal")
        for index in range(self.count):
            yield f"node-{index}"


class _FakeLegacyDescWindow:
    def __init__(self):
        self.calls = []

    def descendants(self, **kwargs):
        self.calls.append(dict(kwargs))
        if "cache_enable" in kwargs:
            raise TypeError("cache_enable unsupported")
        return ["legacy-node-1", "legacy-node-2"]



class _FakeControl:
    def __init__(
        self,
        name,
        control_type,
        bounds,
        automation_id="",
        visible=True,
    ):
        self._name = name
        self._visible = visible
        self._bounds = bounds
        self.element_info = SimpleNamespace(
            name=name,
            control_type=control_type,
            automation_id=automation_id,
        )

    def window_text(self):
        return self._name

    def rectangle(self):
        left, top, right, bottom = self._bounds
        return SimpleNamespace(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return True


class _FakeSemanticWindow(_FakeControl):
    handle = 4242

    def __init__(self):
        super().__init__(
            "Hybrid App",
            "Window",
            (100, 100, 1100, 900),
        )


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
    def setUp(self):
        self._cua_available_patcher = patch(
            "jarvis_agent.windows_perception.CUA_DRIVER.available",
            return_value=False,
        )
        self._cua_available_patcher.start()

    def tearDown(self):
        self._cua_available_patcher.stop()

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
    @patch("jarvis_agent.windows_perception._send_keys")
    def test_control_append_pastes_exact_unicode_and_restores_clipboard(
        self,
        send_keys_mock,
        sleep_mock,
    ):
        document = _FakeDocument("bonjour Jarvis")
        _FakeClipboard.value = "previous"

        with patch.dict(sys.modules, {"win32clipboard": _FakeClipboard}):
            pasted = wp._paste_text_to_control(
                document,
                " heureux de vous revoir.",
                append=True,
            )

        self.assertTrue(pasted)
        self.assertTrue(document.focused)
        self.assertEqual(
            [call.args[0] for call in send_keys_mock.call_args_list],
            ["^{END}", "^v"],
        )
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


    def test_bounded_descendants_stops_before_full_tree(self):
        window = _FakeBoundedWindow(count=1000)

        items, meta = _bounded_descendants(
            window,
            max_depth=5,
            max_nodes=12,
            time_budget_s=10.0,
        )

        self.assertEqual(len(items), 12)
        self.assertTrue(meta["truncated"])
        self.assertEqual(meta["visited_nodes"], 12)
        self.assertEqual(window.calls[0]["depth"], 5)
        self.assertNotIn("cache_enable", window.calls[0])


    def test_bounded_descendants_legacy_fallback_avoids_cache_keyword(self):
        window = _FakeLegacyDescWindow()

        items, meta = _bounded_descendants(
            window,
            max_depth=4,
            max_nodes=12,
            time_budget_s=10.0,
        )

        self.assertEqual(items, ["legacy-node-1", "legacy-node-2"])
        self.assertEqual(meta["strategy"], "descendants_depth")
        self.assertEqual(window.calls[0]["depth"], 4)
        self.assertNotIn("cache_enable", window.calls[0])


    def test_system_chrome_only_detects_shallow_titlebar_snapshot(self):
        window = _FakeSemanticWindow()
        controls = [
            _FakeControl("Minimize", "Button", (910, 100, 960, 140)),
            _FakeControl("Maximize", "Button", (960, 100, 1010, 140)),
            _FakeControl("Close", "Button", (1010, 100, 1060, 140)),
            _FakeControl("Système", "MenuItem", (105, 105, 140, 138)),
        ]

        self.assertTrue(
            _system_chrome_only(
                window,
                controls,
                [],
                [],
            )
        )

    def test_system_chrome_only_rejects_real_content_control(self):
        window = _FakeSemanticWindow()
        controls = [
            _FakeControl("Minimize", "Button", (910, 100, 960, 140)),
            _FakeControl("Search", "Edit", (160, 220, 460, 260)),
        ]

        self.assertFalse(
            _system_chrome_only(
                window,
                controls,
                [],
                [],
            )
        )

    @patch("jarvis_agent.windows_perception._bounded_descendants")
    @patch("jarvis_agent.windows_perception._uia_window_from_handle")
    @patch("jarvis_agent.windows_perception._native_child_windows")
    def test_child_hwnd_fragment_probe_merges_structured_uia_roots(
        self,
        child_mock,
        attach_mock,
        bounded_mock,
    ):
        child_mock.return_value = [
            {
                "handle": 501,
                "title": "",
                "class_name": "WebViewHost",
                "bounds": (100, 150, 1100, 900),
                "area": 750000,
            },
            {
                "handle": 502,
                "title": "",
                "class_name": "ContentBridge",
                "bounds": (120, 180, 1080, 880),
                "area": 672000,
            },
        ]
        roots = [
            _FakeControl("", "Pane", (100, 150, 1100, 900)),
            _FakeControl("", "Pane", (120, 180, 1080, 880)),
        ]
        attach_mock.side_effect = roots
        bounded_mock.side_effect = [
            (
                [_FakeControl("Search", "Edit", (150, 210, 440, 250))],
                {"truncated": False, "elapsed_seconds": 0.02},
            ),
            (
                [_FakeControl("Conversation", "ListItem", (150, 300, 500, 350))],
                {"truncated": False, "elapsed_seconds": 0.03},
            ),
        ]

        nodes, meta = _probe_child_uia_fragments(
            {"handle": 4242},
            max_roots=4,
            max_nodes_per_root=20,
            time_budget_s=2.0,
        )

        self.assertEqual(meta["strategy"], "child_hwnd_fragments")
        self.assertEqual(meta["attempted_roots"], 2)
        self.assertEqual(len(nodes), 4)
        self.assertEqual(meta["returned_nodes"], 4)


    @patch("jarvis_agent.windows_perception._probe_child_uia_fragments")
    @patch("jarvis_agent.windows_perception._bounded_descendants")
    @patch("jarvis_agent.windows_perception._uia_window_from_handle")
    @patch("jarvis_agent.windows_perception._native_window_is_minimized")
    @patch("jarvis_agent.windows_perception._native_target_window")
    def test_inspection_recovers_content_from_child_uia_fragment(
        self,
        native_mock,
        minimized_mock,
        attach_mock,
        bounded_mock,
        fragment_mock,
    ):
        native_mock.return_value = {
            "handle": 4242,
            "title": "Hybrid App",
            "bounds": (100, 100, 1100, 900),
        }
        minimized_mock.return_value = False
        attach_mock.return_value = _FakeSemanticWindow()

        chrome_controls = [
            _FakeControl("Minimize", "Button", (910, 100, 960, 140)),
            _FakeControl("Maximize", "Button", (960, 100, 1010, 140)),
            _FakeControl("Close", "Button", (1010, 100, 1060, 140)),
            _FakeControl("Système", "MenuItem", (105, 105, 140, 138)),
        ]
        bounded_mock.return_value = (
            chrome_controls,
            {
                "strategy": "iter_descendants",
                "depth": 6,
                "visited_nodes": 4,
                "truncated": False,
                "elapsed_seconds": 0.02,
            },
        )
        fragment_mock.return_value = (
            [
                _FakeControl(
                    "Search contacts",
                    "Edit",
                    (150, 210, 430, 250),
                    automation_id="search",
                )
            ],
            {
                "strategy": "child_hwnd_fragments",
                "candidate_hwnds": 2,
                "attempted_roots": 1,
                "returned_nodes": 1,
                "elapsed_seconds": 0.03,
                "roots": [],
            },
        )

        result = inspect_active_window(title="Hybrid App")

        self.assertTrue(result.success)
        payload = json.loads(result.detail)
        self.assertEqual(
            payload["snapshot"]["semantic_coverage"],
            "usable",
        )
        self.assertEqual(
            payload["snapshot"]["fragment_probe"]["strategy"],
            "child_hwnd_fragments",
        )
        self.assertTrue(
            any(
                item.get("name") == "Search contacts"
                and item.get("type") == "Edit"
                for item in payload["controls"]
            )
        )
        self.assertFalse(payload["snapshot"]["vision_recommended"])
        self.assertTrue(payload["observation_id"].startswith("obs"))

    @patch("jarvis_agent.windows_perception._uia_window_from_handle")
    @patch("jarvis_agent.windows_perception._window_by_title")
    @patch("jarvis_agent.windows_perception._native_window_is_minimized")
    @patch("jarvis_agent.windows_perception._native_target_window")
    def test_named_inspection_returns_fast_native_snapshot_when_minimized(
        self,
        native_mock,
        minimized_mock,
        title_mock,
        attach_mock,
    ):
        native_mock.return_value = {
            "handle": 4242,
            "title": "WhatsApp",
            "bounds": (-32000, -32000, -31000, -31200),
        }
        minimized_mock.return_value = True

        result = inspect_active_window(title="WhatsApp")

        self.assertTrue(result.success)
        self.assertIn("win32_window_minimized", result.detail)
        self.assertIn('"minimized":true', result.detail)
        title_mock.assert_not_called()
        attach_mock.assert_not_called()

    @patch("jarvis_agent.windows_perception._uia_window_from_handle")
    @patch("jarvis_agent.windows_perception._window_by_title")
    @patch("jarvis_agent.windows_perception._native_window_is_minimized")
    @patch("jarvis_agent.windows_perception._native_target_window")
    def test_named_inspection_prefers_stable_native_handle(
        self,
        native_mock,
        minimized_mock,
        title_mock,
        attach_mock,
    ):
        native_mock.return_value = {
            "handle": 4242,
            "title": "WhatsApp",
            "bounds": (10, 20, 1210, 820),
        }
        minimized_mock.return_value = False
        attach_mock.return_value = _FakeWindow()

        result = inspect_active_window(title="WhatsApp")

        self.assertTrue(result.success)
        self.assertIn("win32_handle_to_uia", result.detail)
        title_mock.assert_not_called()
        attach_mock.assert_called_once_with(4242)

    @patch("jarvis_agent.windows_perception._try_cua_inspection")
    @patch("jarvis_agent.windows_perception._active_window")
    def test_inspection_uses_cua_when_no_active_uia_window(
        self,
        active_mock,
        cua_mock,
    ):
        active_mock.return_value = None
        cua_mock.return_value = wp.UIActionResult(
            True,
            "Fenêtre inspectée via Cua Driver: WhatsApp.",
            '{"observation_id":"obs9","window":{"title":"WhatsApp"},"controls":[]}',
        )

        result = inspect_active_window()

        self.assertTrue(result.success)
        self.assertIn("WhatsApp", result.detail)
        cua_mock.assert_called_once_with(title=None, limit=36)

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

    @patch("jarvis_agent.windows_perception.time.sleep")
    @patch("jarvis_agent.windows_perception._send_keys")
    @patch("jarvis_agent.windows_perception._snapshot_element")
    def test_write_ui_element_pastes_into_combobox_fallback(
        self,
        snapshot_mock,
        send_keys_mock,
        sleep_mock,
    ):
        combo = _FakeComboBox()
        snapshot_mock.return_value = combo
        _FakeClipboard.value = "previous"

        with patch.dict(sys.modules, {"win32clipboard": _FakeClipboard}):
            result = write_ui_element(
                "",
                "Sports et intelligence artificielle",
                ref="e3",
            )

        self.assertTrue(result.success)
        self.assertTrue(combo.focused)
        self.assertTrue(combo.clicked)
        self.assertEqual(combo.typed, [])
        self.assertEqual(
            [call.args[0] for call in send_keys_mock.call_args_list],
            ["^a", "^v"],
        )
        self.assertEqual(_FakeClipboard.value, "previous")

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

    def test_web_window_query_does_not_collapse_to_desktop_app(self):
        desktop_score = _window_query_score(
            "WhatsApp Web",
            "WhatsApp",
            process="WhatsApp.exe",
        )
        browser_score = _window_query_score(
            "WhatsApp Web",
            "(93) WhatsApp - Google Chrome",
            process="chrome.exe",
        )

        self.assertLess(desktop_score, 0.82)
        self.assertGreaterEqual(browser_score, 0.82)

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

    @patch("jarvis_agent.windows_perception._send_keys")
    def test_press_key_allows_safe_save_shortcut(self, send_mock):
        from jarvis_agent.windows_perception import press_key

        result = press_key("Ctrl+S")

        self.assertTrue(result.success)
        send_mock.assert_called_once_with("^s")

    @patch("jarvis_agent.windows_perception._native_window_candidates")
    def test_native_target_matches_localized_title_by_process_identity(
        self,
        candidates_mock,
    ):
        candidates_mock.return_value = [
            {
                "handle": 42,
                "title": "Sans titre – Bloc-notes",
                "process": "Notepad.exe",
                "bounds": (10, 10, 800, 600),
            }
        ]

        item = _native_target_window("Notepad")

        self.assertIsNotNone(item)
        self.assertEqual(item["handle"], 42)

    @patch("jarvis_agent.windows_perception._control_value")
    @patch("jarvis_agent.windows_perception._paste_text_to_control")
    @patch("jarvis_agent.windows_perception._snapshot_element")
    def test_append_fallback_pastes_exact_unicode_text(
        self,
        snapshot_mock,
        paste_mock,
        value_mock,
    ):
        document = _FakeControl(
            "Document",
            "Document",
            (10, 20, 810, 620),
        )
        snapshot_mock.return_value = document
        paste_mock.return_value = True
        value_mock.side_effect = [
            "bonjour Jarvis",
            "bonjour Jarvis heureux de vous revoir.",
        ]

        result = write_ui_element(
            "",
            " heureux de vous revoir.",
            ref="obs7:e4",
            mode="append",
        )

        self.assertTrue(result.success)
        paste_mock.assert_called_once_with(
            document,
            " heureux de vous revoir.",
            append=True,
        )
        self.assertIn('"verified":true', result.detail)

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
        self.assertNotIn(
            "observation_id",
            tools["click_ui_element"]["properties"],
        )
        self.assertIn(
            "delivery_mode",
            tools["click_ui_element"]["properties"],
        )
        self.assertIn(
            "ref",
            tools["write_ui_element"]["properties"],
        )
        self.assertNotIn(
            "observation_id",
            tools["write_ui_element"]["properties"],
        )
        self.assertIn(
            "delivery_mode",
            tools["write_ui_element"]["properties"],
        )

    def test_click_ref_expires_after_mutating_action(self):
        class Clickable:
            element_info = SimpleNamespace(
                name="Ajouter un nouvel onglet",
                control_type="Button",
                automation_id="AddButton",
            )

            def window_text(self):
                return "Ajouter un nouvel onglet"

            def set_focus(self):
                return None

            def invoke(self):
                return None

        wp._SNAPSHOT_ELEMENTS = {"obs42:e6": Clickable()}
        wp._SNAPSHOT_ID = "obs42"
        wp._SNAPSHOT_WINDOW_TITLE = "Bloc-notes"

        result = click_ui_element(
            ref="obs42:e6",
        )
        self.assertTrue(result.success)
        detail = json.loads(result.detail)
        self.assertTrue(detail["refs_invalidated"])

        stale = click_ui_element(
            ref="obs42:e6",
        )
        self.assertFalse(stale.success)
        stale_detail = json.loads(stale.detail)
        self.assertTrue(stale_detail["stale_ref"])

    def test_click_rejects_unbound_short_ref(self):
        class Clickable:
            element_info = SimpleNamespace(
                name="Button",
                control_type="Button",
                automation_id="",
            )

            def window_text(self):
                return "Button"

        wp._SNAPSHOT_ELEMENTS = {"obs100:e1": Clickable()}
        wp._SNAPSHOT_ID = "obs100"

        result = click_ui_element(ref="e1")

        self.assertFalse(result.success)
        payload = json.loads(result.detail)
        self.assertTrue(payload["stale_ref"])
        self.assertEqual(payload["current_observation_id"], "obs100")

    def test_click_rejects_ref_from_different_observation(self):
        class Clickable:
            element_info = SimpleNamespace(
                name="Button",
                control_type="Button",
                automation_id="",
            )

            def window_text(self):
                return "Button"

        wp._SNAPSHOT_ELEMENTS = {"obs100:e1": Clickable()}
        wp._SNAPSHOT_ID = "obs100"

        result = click_ui_element(
            ref="obs99:e1",
        )
        self.assertFalse(result.success)
        payload = json.loads(result.detail)
        self.assertTrue(payload["stale_ref"])
        self.assertEqual(payload["current_observation_id"], "obs100")

    @patch("jarvis_agent.windows_perception.CUA_DRIVER")
    def test_cua_fallback_projects_fresh_grounded_refs(self, cua_mock):
        cua_mock.available.return_value = True
        cua_mock.inspect_window.return_value = CuaWindowSnapshot(
            pid=777,
            window_id=888,
            title="WhatsApp",
            app_name="WhatsApp",
            bounds=(100, 100, 1200, 900),
            elements=(
                CuaElement(
                    token="s0000002a:14",
                    pid=777,
                    window_id=888,
                    role="text field",
                    label="Search",
                    actions=("set_value",),
                    frame=(130, 180, 500, 225),
                ),
                CuaElement(
                    token="s0000002a:15",
                    pid=777,
                    window_id=888,
                    role="button",
                    label="New chat",
                    actions=("invoke",),
                    frame=(1040, 170, 1100, 225),
                ),
            ),
            truncated=False,
            total_element_count=2,
            returned_element_count=2,
        )

        result = _try_cua_inspection(title="WhatsApp", limit=36)

        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        payload = json.loads(result.detail)
        self.assertEqual(payload["fallback"], "cua_driver")
        self.assertEqual(payload["snapshot"]["provider"], "cua_driver")
        self.assertTrue(payload["observation_id"].startswith("obs"))
        self.assertTrue(
            all(
                item["ref"].startswith(payload["observation_id"] + ":")
                for item in payload["controls"]
            )
        )
        self.assertTrue(payload["capabilities"]["writable"])
        self.assertTrue(payload["capabilities"]["actionable"])
        self.assertNotIn("element_token", result.detail)

    @patch("jarvis_agent.windows_perception.CUA_DRIVER")
    def test_cua_click_uses_snapshot_token_and_invalidates_ref(self, cua_mock):
        element = CuaElement(
            token="s0000002a:14",
            pid=777,
            window_id=888,
            role="button",
            label="Ajouter un nouvel onglet",
            actions=("invoke",),
        )
        wp._SNAPSHOT_ELEMENTS = {"obs77:e6": element}
        wp._SNAPSHOT_ID = "obs77"
        wp._SNAPSHOT_WINDOW_TITLE = "Bloc-notes"
        cua_mock.click_element.return_value = CuaActionResult(
            True,
            "ok",
            {
                "provider": "cua_driver",
                "effect": "confirmed",
                "route": "accessibility",
                "requires_fresh_inspection": True,
            },
        )

        result = click_ui_element(
            ref="obs77:e6",
            delivery_mode="background",
        )

        self.assertTrue(result.success)
        cua_mock.click_element.assert_called_once_with(
            element,
            delivery_mode="background",
        )
        detail = json.loads(result.detail)
        self.assertEqual(detail["source_observation_id"], "obs77")
        self.assertTrue(detail["refs_invalidated"])
        self.assertEqual(wp._SNAPSHOT_ELEMENTS, {})

    @patch("jarvis_agent.windows_perception.CUA_DRIVER")
    def test_cua_write_preserves_background_first_policy(self, cua_mock):
        element = CuaElement(
            token="s0000002a:20",
            pid=777,
            window_id=888,
            role="text field",
            label="Search contacts",
            value="",
            actions=("set_value",),
        )
        wp._SNAPSHOT_ELEMENTS = {"obs78:e1": element}
        wp._SNAPSHOT_ID = "obs78"
        wp._SNAPSHOT_WINDOW_TITLE = "WhatsApp"
        cua_mock.write_element.return_value = CuaActionResult(
            True,
            "ok",
            {
                "provider": "cua_driver",
                "effect": "confirmed",
                "route": "accessibility",
                "requires_fresh_inspection": True,
            },
        )

        result = write_ui_element(
            "",
            "Bouguera",
            ref="obs78:e1",
            mode="replace",
            delivery_mode="background",
        )

        self.assertTrue(result.success)
        cua_mock.write_element.assert_called_once_with(
            element,
            "Bouguera",
            mode="replace",
            delivery_mode="background",
        )
        self.assertEqual(wp._SNAPSHOT_ELEMENTS, {})

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
