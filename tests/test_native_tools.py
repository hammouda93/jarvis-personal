import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.agent_knowledge import AgentKnowledgeStore
from jarvis_agent.config import settings as real_settings
from jarvis_agent.native_tools import NativeToolRegistry
from jarvis_agent.tools import ToolResult


class NativeToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.knowledge = AgentKnowledgeStore(
            Path(self.tmp.name) / "agent_knowledge.sqlite3"
        )
        self.registry = NativeToolRegistry(knowledge=self.knowledge)

    def tearDown(self):
        self.tmp.cleanup()

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

    @patch(
        "jarvis_agent.native_tools.settings",
        replace(real_settings, operational_learning_enabled=True),
    )
    @patch("jarvis_agent.native_tools.os.startfile")
    @patch("jarvis_agent.native_tools.execute")
    def test_learned_app_profile_is_reused_before_rescanning_windows(
        self,
        execute_mock,
        startfile_mock,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            shortcut = Path(tmp) / "WhatsApp.lnk"
            shortcut.write_bytes(b"")
            self.knowledge.upsert_app_profile(
                display_name="WhatsApp",
                aliases=["WhatsApp Desktop"],
                launch_hint=str(shortcut),
                success=True,
            )

            result = self.registry.execute(
                "open_application",
                {"name": "WhatsApp"},
            )

        self.assertTrue(result.success)
        startfile_mock.assert_called_once_with(str(shortcut))
        execute_mock.assert_not_called()

    @patch("jarvis_agent.native_tools.execute")
    def test_known_app_failure_falls_back_to_generic_discovery(
        self,
        execute_mock,
    ):
        execute_mock.side_effect = [
            ToolResult(False, "missing", "cursor missing"),
            ToolResult(True, "ok", "Cursor.lnk"),
        ]

        result = self.registry.execute(
            "open_application",
            {"name": "Cursor"},
        )

        self.assertTrue(result.success)
        self.assertEqual(execute_mock.call_count, 2)
        first = execute_mock.call_args_list[0].args[0]
        second = execute_mock.call_args_list[1].args[0]
        self.assertEqual(first.name, "app.open")
        self.assertEqual(first.args["app"], "cursor")
        self.assertEqual(second.name, "app.open_named")
        self.assertEqual(second.args["query"], "Cursor")

    @patch("jarvis_agent.native_tools.execute")
    def test_notepad_alias_uses_known_app_path(self, execute_mock):
        execute_mock.return_value = ToolResult(True, "ok", "notepad")

        result = self.registry.execute(
            "open_application",
            {"name": "Bloc-notes"},
        )

        self.assertTrue(result.success)
        intent = execute_mock.call_args.args[0]
        self.assertEqual(intent.name, "app.open")
        self.assertEqual(intent.args["app"], "notepad")

    @patch("jarvis_agent.native_tools.execute")
    def test_open_file_uses_generic_file_discovery(self, execute_mock):
        execute_mock.return_value = ToolResult(
            True,
            "ok",
            r"C:\Users\test\Downloads\CursorUserSetup-x64.exe",
        )

        result = self.registry.execute(
            "open_file",
            {"name": "Cursor Setup", "within": "Téléchargements"},
        )

        self.assertTrue(result.success)
        intent = execute_mock.call_args.args[0]
        self.assertEqual(intent.name, "file.open_named")
        self.assertEqual(intent.args["query"], "Cursor Setup")
        self.assertEqual(intent.args["within"], "Téléchargements")

    @patch("jarvis_agent.native_tools.execute")
    def test_executable_name_is_not_fuzzy_opened_as_application(
        self,
        execute_mock,
    ):
        execute_mock.return_value = ToolResult(
            True,
            "ok",
            r"C:\Users\test\Downloads\CursorUserSetup.exe",
        )

        result = self.registry.execute(
            "open_application",
            {"name": "cursor-setup.exe"},
        )

        self.assertTrue(result.success)
        intent = execute_mock.call_args.args[0]
        self.assertEqual(intent.name, "file.open_named")
        self.assertEqual(intent.args["within"], "Téléchargements")

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

    def test_registry_exposes_generic_file_open_and_write_modes(self):
        tools = {
            item["function"]["name"]: item["function"]["parameters"]
            for item in self.registry.ollama_tools()
        }
        self.assertIn("open_file", tools)
        self.assertIn("close_tab", tools)
        self.assertIn("type_text_active_window", tools)
        self.assertIn("mode", tools["write_ui_element"]["properties"])
        self.assertEqual(
            set(
                tools["write_ui_element"]["properties"]["mode"]["enum"]
            ),
            {"replace", "append", "insert"},
        )

    def test_registry_exposes_operational_knowledge_tools(self):
        names = {
            item["function"]["name"]
            for item in self.registry.ollama_tools()
        }
        self.assertIn("observe_screen", names)
        self.assertIn("click_visual_target", names)
        self.assertIn("write_visual_target", names)
        self.assertIn("search_agent_knowledge", names)
        self.assertIn("save_verified_skill", names)
        self.assertIn("save_feedback_lesson", names)
        self.assertIn("agent_knowledge_stats", names)

    @patch("jarvis_agent.native_tools.close_tab")
    def test_close_tab_routes_separately_from_close_window(self, close_tab_mock):
        close_tab_mock.return_value = SimpleNamespace(
            success=True,
            message="closed",
            detail='{"verified":true}',
        )

        result = self.registry.execute(
            "close_tab",
            {"name": "YouTube"},
        )

        self.assertTrue(result.success)
        close_tab_mock.assert_called_once_with("YouTube")

    @patch("jarvis_agent.native_tools.observe_screen")
    def test_observe_screen_routes_to_local_visual_sensor(self, observe_mock):
        observe_mock.return_value = SimpleNamespace(
            success=True,
            message="observed",
            detail='{"observation":"three results visible"}',
        )

        result = self.registry.execute(
            "observe_screen",
            {
                "title": "YouTube",
                "focus": "Identify the first three regular videos.",
            },
        )

        self.assertTrue(result.success)
        observe_mock.assert_called_once_with(
            title="YouTube",
            focus="Identify the first three regular videos.",
        )

    @patch("jarvis_agent.native_tools.click_visual_target")
    def test_click_visual_target_routes_to_local_visual_action(
        self,
        click_mock,
    ):
        click_mock.return_value = SimpleNamespace(
            success=True,
            message="clicked",
            detail='{"verified":false,"confidence":0.91}',
        )

        result = self.registry.execute(
            "click_visual_target",
            {
                "title": "Enregistrer sous",
                "target": "bouton Enregistrer",
            },
        )

        self.assertTrue(result.success)
        click_mock.assert_called_once_with(
            target="bouton Enregistrer",
            title="Enregistrer sous",
        )

    @patch("jarvis_agent.native_tools.write_visual_target")
    def test_write_visual_target_routes_to_local_visual_action(
        self,
        write_mock,
    ):
        write_mock.return_value = SimpleNamespace(
            success=True,
            message="written",
            detail='{"verified":false,"confidence":0.94}',
        )

        result = self.registry.execute(
            "write_visual_target",
            {
                "title": "Enregistrer sous",
                "target": "champ Nom du fichier",
                "text": "jarvis_test.txt",
                "mode": "replace",
            },
        )

        self.assertTrue(result.success)
        write_mock.assert_called_once_with(
            target="champ Nom du fichier",
            text="jarvis_test.txt",
            title="Enregistrer sous",
            mode="replace",
        )

    def test_verified_skill_tool_writes_to_injected_local_store(self):
        result = self.registry.execute(
            "save_verified_skill",
            {
                "name": "generic_edit_document",
                "goal": "Edit an existing document without losing content.",
                "app_scope": "text editor",
                "procedure": [
                    "Inspect the editable document.",
                    "Use append when content must be preserved.",
                ],
                "success_checks": [
                    "The resulting document value contains old and new text."
                ],
            },
        )

        self.assertTrue(result.success)
        self.assertEqual(self.knowledge.stats()["skills"], 1)
        self.assertEqual(
            self.knowledge.get_skill("generic_edit_document").version,
            1,
        )

    def test_feedback_lesson_tool_writes_to_injected_local_store(self):
        result = self.registry.execute(
            "save_feedback_lesson",
            {
                "scope": "ui",
                "pattern": "user confirms previous mutation",
                "rule": "Do not repeat the mutation after a confirmation.",
            },
        )

        self.assertTrue(result.success)
        self.assertEqual(self.knowledge.stats()["lessons"], 1)

    def test_search_agent_knowledge_returns_matching_context(self):
        self.knowledge.record_lesson(
            scope="messaging",
            pattern="contact search versus message composer",
            rule="Do not type a message into the contact search field.",
        )

        result = self.registry.execute(
            "search_agent_knowledge",
            {"query": "messaging contact search", "limit": 3},
        )

        self.assertTrue(result.success)
        payload = json.loads(result.detail)
        self.assertTrue(payload["lessons"])

    @patch(
        "jarvis_agent.native_tools.settings",
        replace(real_settings, operational_learning_enabled=True),
    )
    def test_successful_app_open_updates_local_app_profile(self):
        with patch("jarvis_agent.native_tools.execute") as execute_mock:
            execute_mock.return_value = ToolResult(
                True,
                "ok",
                r"C:\\Users\\test\\AppData\\Local\\Programs\\Cursor\\Cursor.exe",
            )

            result = self.registry.execute(
                "open_application",
                {"name": "Cursor"},
            )

        self.assertTrue(result.success)
        context = self.knowledge.relevant_context("Cursor", limit=3)
        self.assertTrue(context["app_profiles"])

    def test_tool_result_is_json_for_model_observation(self):
        result = self.registry._error("open_application", "introuvable")
        payload = json.loads(result.as_json())
        self.assertEqual(payload["tool"], "open_application")
        self.assertFalse(payload["success"])


if __name__ == "__main__":
    unittest.main()
