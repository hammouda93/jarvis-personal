import unittest
from unittest.mock import patch

from jarvis_agent.cua_driver_bridge import (
    CuaDriverBridge,
    CuaDriverToolError,
    CuaElement,
)


class CuaDriverBridgeTests(unittest.TestCase):
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
