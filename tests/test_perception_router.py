import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis_agent.perception_router import (
    _uia_needs_visual_fallback,
    PerceptionManager,
    inspect_window_hybrid,
)
from jarvis_agent.screen_vision import ScreenObservation
from jarvis_agent.windows_perception import UIActionResult


class HybridPerceptionTests(unittest.TestCase):
    def test_usable_uia_does_not_trigger_vision(self):
        payload = {
            "window": {"title": "Editor"},
            "controls": [
                {"ref": "e1", "type": "Edit", "name": "Document"},
            ],
            "capabilities": {
                "writable": [{"ref": "e1", "label": "Document"}],
                "actionable": [],
            },
            "snapshot": {
                "semantic_coverage": "usable",
                "vision_recommended": True,
            },
        }

        with patch(
            "jarvis_agent.perception_router.inspect_uia_window",
            return_value=UIActionResult(
                True,
                "ok",
                json.dumps(payload),
            ),
        ), patch(
            "jarvis_agent.perception_router.observe_screen"
        ) as vision_mock, patch(
            "jarvis_agent.perception_router.settings",
            SimpleNamespace(vision_enabled=True),
        ):
            result = inspect_window_hybrid(title="Editor")

        self.assertTrue(result.success)
        vision_mock.assert_not_called()
        detail = json.loads(result.detail)
        self.assertEqual(detail["perception"]["mode"], "uia")

    def test_insufficient_uia_uses_local_vision(self):
        uia_payload = {
            "window": {"title": "Custom App", "bounds": [10, 20, 1010, 820]},
            "controls": [
                {"ref": "e1", "type": "Button", "name": "Close"},
            ],
            "capabilities": {
                "writable": [],
                "actionable": [{"ref": "e1", "label": "Close"}],
            },
            "snapshot": {
                "semantic_coverage": "insufficient",
                "vision_recommended": True,
            },
        }
        vision_payload = {
            "title": "Custom App",
            "bounds": [10, 20, 1010, 820],
            "captured_width": 1000,
            "captured_height": 800,
            "model": "gemma3:latest",
            "seconds": 0.8,
            "observation_json": {
                "summary": "Application de messagerie",
                "visible_text": ["Recherche", "Conversation"],
                "targets": [
                    {
                        "label": "Recherche",
                        "role": "textbox",
                        "box_1000": [40, 80, 320, 130],
                        "confidence": 0.94,
                    }
                ],
            },
        }

        with patch(
            "jarvis_agent.perception_router.inspect_uia_window",
            return_value=UIActionResult(
                True,
                "uia shallow",
                json.dumps(uia_payload),
            ),
        ), patch(
            "jarvis_agent.perception_router.observe_screen",
            return_value=ScreenObservation(
                True,
                "vision ok",
                json.dumps(vision_payload),
            ),
        ) as vision_mock, patch(
            "jarvis_agent.perception_router.settings",
            SimpleNamespace(vision_enabled=True),
        ):
            result = inspect_window_hybrid(title="Custom App")

        self.assertTrue(result.success)
        vision_mock.assert_called_once()
        detail = json.loads(result.detail)
        self.assertEqual(detail["perception"]["mode"], "hybrid")
        self.assertTrue(detail["perception"]["vision_success"])
        self.assertEqual(
            detail["visual_observation"]["observation"]["targets"][0]["label"],
            "Recherche",
        )

    def test_visual_fallback_pins_and_activates_exact_structured_hwnd(self):
        uia_payload = {
            "window": {
                "title": "Opaque App",
                "hwnd": 4242,
                "pid": 99,
                "process_start": "stable-process",
                "bounds": [100, 100, 1100, 800],
            },
            "controls": [
                {"ref": "e1", "type": "Button", "name": "Close"},
            ],
            "snapshot": {"semantic_coverage": "insufficient"},
        }
        vision_payload = {
            "title": "Opaque App",
            "hwnd": 4242,
            "pid": 99,
            "process_start": "stable-process",
            "bounds": [100, 100, 1100, 800],
            "captured_width": 1000,
            "captured_height": 700,
            "observation_json": {
                "targets": [
                    {
                        "role": "search_input",
                        "label": "Search",
                        "box_1000": [20, 40, 400, 100],
                        "confidence": 0.96,
                    }
                ]
            },
        }
        manager = PerceptionManager(
            structured=Mock(
                return_value=UIActionResult(
                    True,
                    "structured",
                    json.dumps(uia_payload),
                )
            ),
            vision=Mock(
                return_value=ScreenObservation(
                    True,
                    "vision",
                    json.dumps(vision_payload),
                )
            ),
        )

        with patch(
            "jarvis_agent.windows_perception.activate_bound_window",
            return_value=UIActionResult(
                True,
                "focused",
                '{"verified":true}',
            ),
        ) as activate_mock, patch(
            "jarvis_agent.perception_router.settings",
            SimpleNamespace(vision_enabled=True),
        ):
            result = manager.perceive(title="Opaque App")

        self.assertTrue(result.success)
        activate_mock.assert_called_once()
        self.assertEqual(
            manager.vision.call_args.kwargs["window_id"],
            "4242",
        )

    def test_failed_visual_capture_is_not_repeated_without_new_generation(self):
        uia_payload = {
            "window": {
                "title": "Opaque App",
                "hwnd": 4242,
                "pid": 99,
                "process_start": "stable-process",
                "bounds": [100, 100, 1100, 800],
            },
            "controls": [
                {"ref": "e1", "type": "Button", "name": "Close"},
            ],
            "snapshot": {"semantic_coverage": "insufficient"},
        }
        vision = Mock(
            return_value=ScreenObservation(
                False,
                "timeout",
                "timed out",
            )
        )
        manager = PerceptionManager(
            structured=Mock(
                return_value=UIActionResult(
                    True,
                    "structured",
                    json.dumps(uia_payload),
                )
            ),
            vision=vision,
        )

        with patch(
            "jarvis_agent.windows_perception.activate_bound_window",
            return_value=UIActionResult(
                True,
                "focused",
                '{"verified":true}',
            ),
        ), patch(
            "jarvis_agent.perception_router.settings",
            SimpleNamespace(vision_enabled=True),
        ):
            first = manager.perceive(title="Opaque App")
            second = manager.perceive(title="Opaque App")

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(vision.call_count, 1)
        detail = json.loads(second.detail)
        self.assertEqual(
            detail["perception"]["vision_error"],
            "VISION_RETRY_BUDGET_EXHAUSTED",
        )

    def test_baseline_or_disabled_vision_preserves_uia_only(self):
        payload = {
            "window": {"title": "Custom App"},
            "controls": [],
            "capabilities": {"writable": [], "actionable": []},
            "snapshot": {
                "semantic_coverage": "insufficient",
                "vision_recommended": True,
            },
        }

        with patch(
            "jarvis_agent.perception_router.inspect_uia_window",
            return_value=UIActionResult(
                True,
                "ok",
                json.dumps(payload),
            ),
        ), patch(
            "jarvis_agent.perception_router.observe_screen"
        ) as vision_mock, patch(
            "jarvis_agent.perception_router.settings",
            SimpleNamespace(vision_enabled=False),
        ):
            result = inspect_window_hybrid(title="Custom App")

        self.assertTrue(result.success)
        vision_mock.assert_not_called()
        detail = json.loads(result.detail)
        self.assertEqual(detail["perception"]["mode"], "uia")

    def test_empty_structured_snapshot_requests_visual_fallback(self):
        self.assertTrue(
            _uia_needs_visual_fallback(
                {
                    "controls": [],
                    "capabilities": {
                        "writable": [],
                        "actionable": [],
                    },
                    "snapshot": {},
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
