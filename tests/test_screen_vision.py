import base64
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis_agent.screen_vision import (
    _capture_window_bytes,
    click_visual_target,
    locate_visual_target,
    observe_screen,
    write_visual_target,
)


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


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


class _ResizableFakeImage:
    def __init__(self, width=1200, height=800):
        self.width = width
        self.height = height
        self.resize_calls = []

    def convert(self, _mode):
        return self

    def resize(self, size):
        self.resize_calls.append(size)
        self.width, self.height = size
        return self

    def save(self, buffer, **_kwargs):
        buffer.write(b"jpeg")


class ScreenVisionTests(unittest.TestCase):
    @patch("PIL.ImageGrab.grab")
    @patch("jarvis_agent.screen_vision._native_target_window")
    @patch("jarvis_agent.screen_vision.settings")
    def test_capture_uses_configured_target_width_when_optional_override_is_none(
        self,
        settings_mock,
        target_mock,
        grab_mock,
    ):
        settings_mock.vision_max_width = 800
        target_mock.return_value = {
            "title": "Opaque App",
            "bounds": [0, 0, 1200, 800],
        }
        image = _ResizableFakeImage()
        grab_mock.return_value = image

        payload, metadata = _capture_window_bytes("Opaque App")

        self.assertEqual(payload, b"jpeg")
        self.assertEqual(image.resize_calls, [(800, 533)])
        self.assertEqual(metadata["captured_width"], 800)
        self.assertEqual(metadata["captured_height"], 533)

    @patch("jarvis_agent.screen_vision.settings")
    def test_remote_vision_endpoint_is_blocked_in_local_only_mode(
        self,
        settings_mock,
    ):
        settings_mock.vision_enabled = True
        settings_mock.vision_local_only = True
        settings_mock.ollama_base_url = "https://example.com"
        settings_mock.vision_model = "gemma3:latest"

        result = observe_screen(focus="describe")

        self.assertFalse(result.success)
        self.assertEqual(result.detail, "vision_local_only")

    @patch("jarvis_agent.screen_vision.urllib.request.urlopen")
    @patch("jarvis_agent.screen_vision._capture_window_bytes")
    @patch("jarvis_agent.screen_vision.settings")
    def test_local_vision_returns_structured_observation(
        self,
        settings_mock,
        capture_mock,
        urlopen_mock,
    ):
        settings_mock.vision_enabled = True
        settings_mock.vision_local_only = True
        settings_mock.ollama_base_url = "http://127.0.0.1:11434"
        settings_mock.vision_model = "gemma3:latest"
        settings_mock.vision_num_predict = 300
        settings_mock.vision_timeout_s = 20
        settings_mock.vision_save_evidence = False
        capture_mock.return_value = (
            b"fake-image",
            {
                "title": "YouTube - Google Chrome",
                "bounds": [0, 0, 1000, 800],
                "captured_width": 1000,
                "captured_height": 800,
            },
        )
        urlopen_mock.return_value = _FakeHTTPResponse(
            {
                "message": {
                    "content": (
                        '{"summary":"Three video results are visible",'
                        '"visible_text":["video one","video two","video three"]}'
                    )
                }
            }
        )

        result = observe_screen(
            title="YouTube",
            focus="Identify the first three non-Short video results.",
        )

        self.assertTrue(result.success)
        detail = json.loads(result.detail)
        self.assertEqual(detail["model"], "gemma3:latest")
        self.assertIn("Three video results", detail["observation"])
        self.assertFalse(detail["evidence_saved"])

        request = urlopen_mock.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        image = body["messages"][0]["images"][0]
        self.assertEqual(body["format"], "json")
        self.assertEqual(
            base64.b64decode(image),
            b"fake-image",
        )

    @patch("jarvis_agent.screen_vision._call_local_vision")
    @patch("jarvis_agent.screen_vision._capture_window_bytes")
    @patch("jarvis_agent.screen_vision.settings")
    def test_compact_observation_uses_smaller_capture_and_output_budget(
        self,
        settings_mock,
        capture_mock,
        vision_mock,
    ):
        settings_mock.vision_enabled = True
        settings_mock.vision_local_only = True
        settings_mock.ollama_base_url = "http://127.0.0.1:11434"
        settings_mock.vision_model = "gemma3:latest"
        settings_mock.vision_num_predict = 420
        settings_mock.vision_max_width = 1600
        settings_mock.vision_save_evidence = False
        capture_mock.return_value = (
            b"fake-image",
            {
                "title": "Opaque App",
                "bounds": [0, 0, 1200, 800],
                "captured_width": 1024,
                "captured_height": 683,
            },
        )
        vision_mock.return_value = (
            '{"summary":"search visible","targets":[],"ambiguities":[]}',
            0.5,
        )

        result = observe_screen(
            title="Opaque App",
            focus="Find search",
            compact=True,
        )

        self.assertTrue(result.success)
        capture_mock.assert_called_once_with(
            "Opaque App",
            max_width=1024,
        )
        self.assertEqual(
            vision_mock.call_args.kwargs["num_predict"],
            180,
        )
        detail = json.loads(result.detail)
        self.assertTrue(detail["compact"])

    @patch("jarvis_agent.screen_vision._call_local_vision")
    @patch("jarvis_agent.screen_vision._capture_window_bytes")
    @patch("jarvis_agent.screen_vision.settings")
    def test_visual_target_localization_returns_normalized_box(
        self,
        settings_mock,
        capture_mock,
        vision_mock,
    ):
        settings_mock.vision_enabled = True
        settings_mock.vision_local_only = True
        settings_mock.ollama_base_url = "http://127.0.0.1:11434"
        settings_mock.vision_model = "gemma3:latest"
        capture_mock.return_value = (
            b"fake-image",
            {
                "title": "Enregistrer sous",
                "bounds": [500, 200, 1500, 1000],
                "captured_width": 1000,
                "captured_height": 800,
            },
        )
        vision_mock.return_value = (
            '{"found":true,"label":"Enregistrer","role":"button",'
            '"box_1000":[700,800,900,900],"confidence":0.93,'
            '"reason":"button visible"}',
            0.42,
        )

        result = locate_visual_target(
            target="bouton Enregistrer",
            title="Enregistrer sous",
        )

        self.assertTrue(result.success)
        detail = json.loads(result.detail)
        self.assertEqual(detail["box_1000"], [700, 800, 900, 900])
        self.assertEqual(detail["confidence"], 0.93)

    @patch("jarvis_agent.screen_vision.locate_visual_target")
    @patch("jarvis_agent.screen_vision.settings")
    def test_visual_click_maps_normalized_box_to_window_coordinates(
        self,
        settings_mock,
        locate_mock,
    ):
        settings_mock.vision_actions_enabled = True
        settings_mock.vision_min_confidence = 0.72
        locate_mock.return_value = SimpleNamespace(
            success=True,
            message="located",
            detail=json.dumps(
                {
                    "bounds": [500, 200, 1500, 1000],
                    "box_1000": [700, 800, 900, 900],
                    "confidence": 0.93,
                    "label": "Enregistrer",
                }
            ),
        )
        mouse_mock = SimpleNamespace()
        mouse_mock.click = Mock()

        with patch.dict(
            sys.modules,
            {"pywinauto": SimpleNamespace(mouse=mouse_mock)},
        ):
            result = click_visual_target(
                target="bouton Enregistrer",
                title="Enregistrer sous",
            )

        self.assertTrue(result.success)
        mouse_mock.click.assert_called_once_with(
            button="left",
            coords=(1300, 880),
        )
        detail = json.loads(result.detail)
        self.assertFalse(detail["verified"])

    @patch("jarvis_agent.screen_vision.locate_visual_target")
    @patch("jarvis_agent.screen_vision.settings")
    def test_visual_click_rejects_low_confidence_target(
        self,
        settings_mock,
        locate_mock,
    ):
        settings_mock.vision_actions_enabled = True
        settings_mock.vision_min_confidence = 0.80
        locate_mock.return_value = SimpleNamespace(
            success=True,
            message="located",
            detail=json.dumps(
                {
                    "bounds": [0, 0, 1000, 800],
                    "box_1000": [100, 100, 200, 200],
                    "confidence": 0.55,
                }
            ),
        )

        result = click_visual_target(target="ambiguous button")

        self.assertFalse(result.success)
        self.assertIn("confiance", result.message.lower())

    @patch("jarvis_agent.screen_vision.time.sleep")
    @patch("jarvis_agent.screen_vision._send_keys")
    @patch("jarvis_agent.screen_vision.click_visual_target")
    def test_visual_write_targets_field_then_pastes_text(
        self,
        click_mock,
        send_keys_mock,
        sleep_mock,
    ):
        click_mock.return_value = SimpleNamespace(
            success=True,
            message="clicked",
            detail=json.dumps(
                {
                    "target": "champ Nom du fichier",
                    "confidence": 0.94,
                    "verified": False,
                }
            ),
        )

        with patch.dict(
            sys.modules,
            {"win32clipboard": _FakeClipboard},
        ):
            result = write_visual_target(
                target="champ Nom du fichier",
                text="jarvis_test.txt",
                title="Enregistrer sous",
                mode="replace",
            )

        self.assertTrue(result.success)
        click_mock.assert_called_once_with(
            target="champ Nom du fichier",
            title="Enregistrer sous",
        )
        self.assertEqual(
            [call.args[0] for call in send_keys_mock.call_args_list],
            ["^a", "^v"],
        )
        self.assertEqual(_FakeClipboard.value, "previous")
        detail = json.loads(result.detail)
        self.assertFalse(detail["verified"])
        self.assertEqual(detail["text_length"], len("jarvis_test.txt"))
        sleep_mock.assert_called_once()

    @patch("jarvis_agent.screen_vision._call_local_vision")
    @patch("jarvis_agent.screen_vision._capture_window_bytes")
    @patch("jarvis_agent.screen_vision.settings")
    def test_visual_observation_fails_closed_on_non_json_response(
        self,
        settings_mock,
        capture_mock,
        vision_mock,
    ):
        settings_mock.vision_enabled = True
        settings_mock.vision_local_only = True
        settings_mock.ollama_base_url = "http://127.0.0.1:11434"
        settings_mock.vision_model = "gemma3:latest"
        settings_mock.vision_save_evidence = False
        capture_mock.return_value = (
            b"fake-image",
            {
                "title": "Opaque App",
                "bounds": [0, 0, 1000, 800],
                "captured_width": 1000,
                "captured_height": 800,
            },
        )
        vision_mock.return_value = ("I think there is a search field.", 0.1)

        result = observe_screen(title="Opaque App", focus="Find search")

        self.assertFalse(result.success)
        self.assertEqual(result.detail, "invalid_visual_observation")

    @patch("jarvis_agent.screen_vision._capture_window_bytes")
    @patch("jarvis_agent.screen_vision.settings")
    def test_capture_failure_is_returned_as_tool_failure(
        self,
        settings_mock,
        capture_mock,
    ):
        settings_mock.vision_enabled = True
        settings_mock.vision_local_only = True
        settings_mock.ollama_base_url = "http://localhost:11434"
        settings_mock.vision_model = "gemma3:latest"
        capture_mock.side_effect = RuntimeError("window missing")

        result = observe_screen(title="Missing")

        self.assertFalse(result.success)
        self.assertIn("window missing", result.detail)


if __name__ == "__main__":
    unittest.main()
