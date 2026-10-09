"""Independent opt-in policy; synthetic stores and no external dispatch."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from jarvis_agent import operational_preferences as prefs
from jarvis_agent.agent_knowledge import AgentKnowledgeStore
from jarvis_agent.agent_runtime import (
    _filter_optional_ollama_tools, _filter_optional_openai_tools,
    _operational_knowledge_message, _record_operational_run, _effective_system_instructions,
)
from jarvis_agent.config import settings
from jarvis_agent.native_tools import NativeToolRegistry, AgentActionResult
from jarvis_agent.operational_preferences_panel import OperationalPreferencesPanel


class OperationalPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch.object(prefs, "_live_flags", None)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "operational_preferences.json"
        self.store = AgentKnowledgeStore(self.root / "knowledge.sqlite3")
        self.store.upsert_skill(name="generic_document", goal="Edit a generic document", procedure=["Fresh observation"])
        self.native = NativeToolRegistry(self.store)

    def test_missing_invalid_and_legacy_environment_all_default_off(self):
        self.assertEqual(prefs.load_preferences(self.path), {"skills_enabled": False, "learning_enabled": False})
        self.path.write_text('{"schema":1,"skills_enabled":"true","learning_enabled":true}', encoding="utf-8")
        self.assertEqual(prefs.load_preferences(self.path), {"skills_enabled": False, "learning_enabled": False})
        env = {**os.environ, "JARVIS_DATA_DIR": str(self.root), "JARVIS_OPERATIONAL_LEARNING_ENABLED": "1"}
        code = "from jarvis_agent.config import settings; print(settings.skills_enabled, settings.operational_learning_enabled)"
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "False False")

    def test_all_four_modes_separate_context_read_from_learning_write(self):
        for use_skills in (False, True):
            for learn in (False, True):
                with self.subTest(use_skills=use_skills, learn=learn):
                    config = replace(settings, skills_enabled=use_skills, operational_learning_enabled=learn)
                    with patch("jarvis_agent.agent_runtime.settings", config), patch("jarvis_agent.native_tools.settings", config):
                        context = _operational_knowledge_message("generic document", self.store)
                        self.assertEqual(bool(context), use_skills)
                        result = self.native.execute("search_agent_knowledge", {"query": "generic document"}, approved=True)
                        self.assertEqual(result.success, use_skills)
                        result = self.native.execute("save_feedback_lesson", {"pattern": "repeat effect",
                            "rule": "Never repeat an unknown external effect"}, approved=True)
                        self.assertEqual(result.success, learn)
                        recorder = Mock()
                        _record_operational_run("synthetic", [AgentActionResult("observe", True, "ok")], recorder)
                        self.assertEqual(recorder.record_run.called, learn)
                    self.assertEqual(self.store.get_skill("generic_document").version, 1)

    def test_all_provider_schema_filters_follow_independent_flags(self):
        names = ["open_file", "search_agent_knowledge", "save_verified_skill", "save_feedback_lesson"]
        ollama = [{"type": "function", "function": {"name": n}} for n in names]
        openai = [{"type": "function", "name": n} for n in names]
        for read in (False, True):
            for write in (False, True):
                with patch("jarvis_agent.agent_runtime.settings", replace(settings, skills_enabled=read, operational_learning_enabled=write)):
                    a = {t["function"]["name"] for t in _filter_optional_ollama_tools(ollama)}
                    b = {t["name"] for t in _filter_optional_openai_tools(openai)}
                    self.assertEqual(a, b)
                    self.assertIn("open_file", a)
                    self.assertEqual("search_agent_knowledge" in a, read)
                    self.assertEqual("save_verified_skill" in a, write)

    def test_off_does_not_query_knowledge_or_add_learning_instructions(self):
        store = Mock()
        config = replace(settings, skills_enabled=False, operational_learning_enabled=False, vision_enabled=True)
        with patch("jarvis_agent.agent_runtime.settings", config):
            self.assertEqual(_operational_knowledge_message("generic document", store), "")
            store.relevant_context.assert_not_called()
            self.assertNotIn("save_verified_skill", _effective_system_instructions())
            self.assertNotIn("save_feedback_lesson", _effective_system_instructions())

    def test_preferences_are_persistent_and_visible_after_fresh_process(self):
        flags = {"skills_enabled": True, "learning_enabled": False}
        prefs.save_preferences(flags, path=self.path)
        self.assertEqual(prefs.effective_flags(replace(settings, skills_enabled=False)), flags)
        self.assertEqual(prefs.load_preferences(self.path), flags)
        code = "from jarvis_agent.config import settings; print(settings.skills_enabled, settings.operational_learning_enabled)"
        result = subprocess.run([sys.executable, "-c", code], env={**os.environ, "JARVIS_DATA_DIR": str(self.root)},
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "True False")
        self.assertEqual(self.store.stats()["skills"], 1)

    def test_failed_persistence_does_not_publish_a_false_mode(self):
        with patch.object(prefs.os, "replace", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                prefs.save_preferences({"skills_enabled": True, "learning_enabled": True}, path=self.path)
        config = replace(settings, skills_enabled=False, operational_learning_enabled=False)
        self.assertEqual(prefs.effective_flags(config), {"skills_enabled": False, "learning_enabled": False})
        self.assertFalse(self.path.exists())
        self.assertFalse(list(self.root.glob(".operational_preferences_*")))

    def test_no_memory_mission_or_native_permission_fields_are_accepted(self):
        with self.assertRaises(ValueError):
            prefs.save_preferences({"skills_enabled": False, "learning_enabled": False, "memory_enabled": False}, path=self.path)
        with self.assertRaises(ValueError):
            prefs.save_preferences({"skills_enabled": "false", "learning_enabled": False}, path=self.path)


class OperationalSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_switches_are_independent_and_do_not_claim_queued_change_is_applied(self):
        config = replace(settings, skills_enabled=False, operational_learning_enabled=False)
        with patch.object(prefs, "_live_flags", None), patch("jarvis_agent.operational_preferences_panel.settings", config):
            panel = OperationalPreferencesPanel()
        self.addCleanup(panel.close)
        calls = []
        panel.requested.connect(lambda op, value: calls.append((op, json.loads(value))))
        self.assertFalse(panel.skills.isChecked())
        self.assertFalse(panel.learning.isChecked())
        panel.skills.setChecked(True)
        self.assertEqual(calls, [("policy_set", {"skills_enabled": True, "learning_enabled": False})])
        self.assertFalse(panel.skills.isEnabled())
        panel.show_result({"success": False, "reason": "storage_error"})
        self.assertFalse(panel.skills.isChecked())
        self.assertFalse(panel.learning.isChecked())
        self.assertTrue(panel.skills.isEnabled())
