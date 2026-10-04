import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.cua_driver_bridge import (
    CuaDriverBridge,
    CuaDriverToolError,
    CuaElement,
    CuaWindowSnapshot,
)


class CuaDriverBridgeTests(unittest.TestCase):
    def test_titlebar_only_snapshot_is_not_meaningful_content(self):
        snapshot = CuaWindowSnapshot(
            pid=7,
            window_id=9,
            title="Hybrid App",
            app_name="Hybrid App",
            bounds=(100, 100, 1100, 900),
            elements=(
                CuaElement(
                    token="s1:0",
                    pid=7,
                    window_id=9,
                    role="Button",
                    label="Minimize",
                    actions=("invoke",),
                    frame=(920, 100, 970, 140),
                ),
                CuaElement(
                    token="s1:1",
                    pid=7,
                    window_id=9,
                    role="Button",
                    label="Close",
                    actions=("invoke",),
                    frame=(1020, 100, 1080, 140),
                ),
            ),
            truncated=False,
            total_element_count=2,
            returned_element_count=2,
        )

        self.assertFalse(snapshot.has_meaningful_content)

    def test_writable_content_makes_snapshot_meaningful(self):
        snapshot = CuaWindowSnapshot(
            pid=7,
            window_id=9,
            title="Hybrid App",
            app_name="Hybrid App",
            bounds=(100, 100, 1100, 900),
            elements=(
                CuaElement(
                    token="s1:4",
                    pid=7,
                    window_id=9,
                    role="text field",
                    label="Search",
                    actions=("set_value",),
                    frame=(150, 200, 450, 245),
                ),
            ),
            truncated=False,
            total_element_count=1,
            returned_element_count=1,
        )

        self.assertTrue(snapshot.has_meaningful_content)

    def test_sparse_window_is_reobserved_once_before_returning(self):
        bridge = CuaDriverBridge()
        window = {
            "pid": 777,
            "window_id": 888,
            "title": "Hybrid App",
            "app_name": "Hybrid App",
            "bounds": {
                "x": 100,
                "y": 100,
                "width": 1000,
                "height": 800,
            },
            "is_on_screen": True,
            "z_index": 2,
        }
        chrome_state = {
            "snapshot_id": "s1",
            "window_title": "Hybrid App",
            "elements": [
                {
                    "element_token": "s1:0",
                    "role": "Button",
                    "label": "Close",
                    "actions": ["invoke"],
                    "frame": {"x": 1020, "y": 100, "w": 60, "h": 40},
                },
            ],
        }
        content_state = {
            "snapshot_id": "s2",
            "window_title": "Hybrid App",
            "elements": [
                {
                    "element_token": "s2:0",
                    "role": "text field",
                    "label": "Search",
                    "actions": ["set_value"],
                    "frame": {"x": 150, "y": 210, "w": 300, "h": 40},
                },
            ],
        }

        with patch.object(
            bridge,
            "call",
            side_effect=[
                {"windows": [window]},
                chrome_state,
                content_state,
            ],
        ) as call_mock:
            snapshot = bridge.inspect_window(
                "Hybrid App",
                timeout_ms=900,
            )

        self.assertEqual(call_mock.call_count, 3)
        self.assertEqual(snapshot.snapshot_id, "s2")
        self.assertTrue(snapshot.has_meaningful_content)
        self.assertEqual(snapshot.elements[0].label, "Search")

    @patch(
        "jarvis_agent.cua_driver_bridge.settings",
        SimpleNamespace(
            cua_driver_enabled=False,
            cua_driver_binary="",
            cua_driver_timeout_s=6.0,
        ),
    )
    def test_driver_is_opt_in(self):
        bridge = CuaDriverBridge()

        self.assertFalse(bridge.available())

    def test_text_field_with_set_value_is_writable(self):
        element = CuaElement(
            token="s0000002a:1",
            pid=10,
            window_id=20,
            role="text field",
            actions=("set_value",),
        )
        self.assertTrue(element.writable)

    def test_button_is_actionable(self):
        element = CuaElement(
            token="s0000002a:2",
            pid=10,
            window_id=20,
            role="button",
            label="New chat",
        )
        self.assertTrue(element.actionable)

    def test_replace_prefers_set_value_when_advertised(self):
        bridge = CuaDriverBridge()
        bridge._tools = {"set_value", "type_text"}
        element = CuaElement(
            token="s0000002a:3",
            pid=10,
            window_id=20,
            role="text field",
            label="Search",
            actions=("set_value",),
        )

        with patch.object(
            bridge,
            "call",
            return_value={
                "effect": "confirmed",
                "route": "accessibility",
            },
        ) as call_mock:
            result = bridge.write_element(
                element,
                "Bouguera",
                mode="replace",
            )

        self.assertTrue(result.success)
        call_mock.assert_called_once_with(
            "set_value",
            {
                "pid": 10,
                "element_token": "s0000002a:3",
                "value": "Bouguera",
            },
        )

    def test_append_uses_observed_value_with_set_value(self):
        bridge = CuaDriverBridge()
        bridge._tools = {"set_value", "type_text"}
        element = CuaElement(
            token="s0000002a:4",
            pid=10,
            window_id=20,
            role="text field",
            value="Bon",
            actions=("set_value",),
        )

        with patch.object(
            bridge,
            "call",
            return_value={
                "effect": "confirmed",
                "route": "accessibility",
            },
        ) as call_mock:
            result = bridge.write_element(
                element,
                "jour",
                mode="append",
            )

        self.assertTrue(result.success)
        call_mock.assert_called_once_with(
            "set_value",
            {
                "pid": 10,
                "element_token": "s0000002a:4",
                "value": "Bonjour",
            },
        )

    def test_transport_success_without_effect_is_not_action_success(self):
        bridge = CuaDriverBridge()
        element = CuaElement(
            token="s0000002a:5",
            pid=10,
            window_id=20,
            role="button",
        )

        with patch.object(
            bridge,
            "call",
            return_value={"route": "accessibility"},
        ):
            result = bridge.click_element(element)

        self.assertFalse(result.success)

    def test_background_refusal_preserves_escalation_evidence(self):
        bridge = CuaDriverBridge()
        element = CuaElement(
            token="s0000002a:6",
            pid=10,
            window_id=20,
            role="button",
        )

        with patch.object(
            bridge,
            "call",
            side_effect=CuaDriverToolError(
                "background unavailable",
                code="background_unavailable",
                recommended_delivery="foreground",
                detail={
                    "refusal": {
                        "code": "background_unavailable",
                    },
                    "escalation": {
                        "recommended": "foreground",
                    },
                },
            ),
        ):
            result = bridge.click_element(
                element,
                delivery_mode="background",
            )

        self.assertFalse(result.success)
        self.assertEqual(
            result.detail["code"],
            "background_unavailable",
        )
        self.assertEqual(
            result.detail["recommended_delivery"],
            "foreground",
        )


if __name__ == "__main__":
    unittest.main()
