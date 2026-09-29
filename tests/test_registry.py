import unittest

from jarvis_agent.registry import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    def test_unknown_app_becomes_generic_app_discovery(self):
        prepared = self.registry.prepare("app.open", {"app": "VLC Media Player"})
        self.assertIsNotNone(prepared.intent)
        self.assertEqual(prepared.intent.name, "app.open_named")
        self.assertEqual(prepared.intent.args["query"], "VLC Media Player")

    def test_vague_app_target_is_rejected(self):
        prepared = self.registry.prepare("app.open_named", {"query": "com"})
        self.assertIsNone(prepared.intent)

    def test_named_folder_keeps_parent_scope(self):
        prepared = self.registry.prepare(
            "folder.open_named",
            {"query": "media", "within": "baristas"},
        )
        self.assertIsNotNone(prepared.intent)
        self.assertEqual(prepared.intent.args["query"], "media")
        self.assertEqual(prepared.intent.args["within"], "baristas")

    def test_internal_folder_prompt_is_registered(self):
        prepared = self.registry.prepare("folder.open_prompt", {})
        self.assertIsNotNone(prepared.intent)
        self.assertEqual(prepared.intent.name, "folder.open_prompt")


if __name__ == "__main__":
    unittest.main()
