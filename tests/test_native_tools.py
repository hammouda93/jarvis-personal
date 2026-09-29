import json
import unittest
from unittest.mock import patch

from jarvis_agent.native_tools import NativeToolRegistry
from jarvis_agent.tools import ToolResult


class NativeToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = NativeToolRegistry()

    @patch("jarvis_agent.native_tools.execute")
    def test_unknown_named_app_uses_generic_discovery(self, execute_mock):
        execute_mock.return_value = ToolResult(True, "ok", "vlc")
        result = self.registry.execute(
            "open_application",
            {"name": "VLC Media Player"},
        )

        self.assertTrue(result.success)
        intent = execute_mock.call_args.args[0]
        self.assertEqual(intent.name, "app.open_named")
        self.assertEqual(intent.args["query"], "VLC Media Player")

    @patch("jarvis_agent.native_tools.execute")
    def test_named_folder_keeps_parent_scope(self, execute_mock):
        execute_mock.return_value = ToolResult(True, "ok", "media")
        result = self.registry.execute(
            "open_folder",
            {"name": "media", "within": "baristas"},
        )

        self.assertTrue(result.success)
        intent = execute_mock.call_args.args[0]
        self.assertEqual(intent.name, "folder.open_named")
        self.assertEqual(intent.args["query"], "media")
        self.assertEqual(intent.args["within"], "baristas")

    @patch("jarvis_agent.native_tools.execute")
    def test_vague_application_is_blocked(self, execute_mock):
        result = self.registry.execute(
            "open_application",
            {"name": "com"},
        )

        self.assertFalse(result.success)
        execute_mock.assert_not_called()

    def test_tool_result_is_json_for_model_observation(self):
        result = self.registry._error("open_application", "introuvable")
        payload = json.loads(result.as_json())
        self.assertEqual(payload["tool"], "open_application")
        self.assertFalse(payload["success"])


if __name__ == "__main__":
    unittest.main()
