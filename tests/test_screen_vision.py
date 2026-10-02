import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.screen_vision import observe_screen


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ScreenVisionTests(unittest.TestCase):
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
        self.assertEqual(
            base64.b64decode(image),
            b"fake-image",
        )

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
