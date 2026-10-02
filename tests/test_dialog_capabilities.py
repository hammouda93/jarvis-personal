import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.windows_perception import inspect_active_window


class _FakeControl:
    def __init__(
        self,
        name,
        control_type,
        *,
        left,
        top,
        right,
        bottom,
        automation_id="",
        enabled=True,
        value="",
    ):
        self._name = name
        self._value = value
        self._rect = SimpleNamespace(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )
        self._enabled = enabled
        self.element_info = SimpleNamespace(
            name=name,
            control_type=control_type,
            automation_id=automation_id,
        )

    def window_text(self):
        return self._name

    def rectangle(self):
        return self._rect

    def is_visible(self):
        return True

    def is_enabled(self):
        return self._enabled

    def get_value(self):
        return self._value


class _FakeDialogWindow:
    handle = 999
    element_info = SimpleNamespace(
        name="Enregistrer sous",
        control_type="Window",
        automation_id="",
    )

    def __init__(self, controls):
        self.controls = controls

    def window_text(self):
        return "Enregistrer sous"

    def rectangle(self):
        return SimpleNamespace(left=300, top=200, right=1300, bottom=850)

    def descendants(self):
        return list(self.controls)


class DialogCapabilityTests(unittest.TestCase):
    @patch("jarvis_agent.windows_perception._active_window")
    def test_dialog_snapshot_keeps_lower_writable_field_and_save_button(
        self,
        active_mock,
    ):
        controls = [
            _FakeControl(
                f"Top {index}",
                "Button",
                left=350 + index * 40,
                top=300,
                right=385 + index * 40,
                bottom=335,
            )
            for index in range(8)
        ]
        controls.extend(
            [
                _FakeControl(
                    "Nom du fichier :",
                    "Text",
                    left=360,
                    top=706,
                    right=505,
                    bottom=734,
                ),
                _FakeControl(
                    "",
                    "Edit",
                    left=515,
                    top=704,
                    right=1120,
                    bottom=736,
                    automation_id="FileNameControlHost",
                    value="Bonjour Jarvis",
                ),
                _FakeControl(
                    "Enregistrer",
                    "Button",
                    left=1130,
                    top=785,
                    right=1230,
                    bottom=820,
                ),
            ]
        )
        active_mock.return_value = _FakeDialogWindow(controls)

        result = inspect_active_window(limit=36)

        self.assertTrue(result.success)
        payload = json.loads(result.detail)
        writable = payload["capabilities"]["writable"]
        actionable = payload["capabilities"]["actionable"]
        self.assertTrue(
            any(
                item["label"] == "Nom du fichier :"
                for item in writable
            )
        )
        self.assertTrue(
            any(
                item["label"] == "Enregistrer"
                for item in actionable
            )
        )
        self.assertFalse(payload["snapshot"]["has_document_region"])


if __name__ == "__main__":
    unittest.main()
