from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QScrollArea

from jarvis_agent.agent_profiles import AgentProfileStore, defaults, validate
from jarvis_agent.agent_profile_panel import AgentProfilePanel
from jarvis_agent.controlled_delegation import role_tools
from jarvis_agent.active_mission_supervisor import SupervisorStopped
import test_active_mission_supervisor as fixtures


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "profiles.json"
        self.store = AgentProfileStore(self.path)
        env = patch.dict(os.environ, {"JARVIS_AGENT_PROFILES_PATH": str(self.path)})
        env.start()
        self.addCleanup(env.stop)

    def test_defaults_do_not_write_files_or_change_existing_scope(self):
        self.assertEqual(self.store.get("browser")["revision"], 0)
        available = frozenset({"browser_observe_dom", "browser_click", "open_application"})
        self.assertEqual(role_tools("browser", available), available - {"open_application"})
        self.assertFalse(self.path.exists())

    def test_unicode_profiles_survive_reopening_and_stale_revision_cannot_overwrite(self):
        settings = defaults("ms_football")
        settings.update(name="Dossiers joueurs العربية ✓", competencies=["Préparer un dossier joueur"])
        self.store.save("ms_football", settings, expected_revision=0)
        self.assertEqual(AgentProfileStore(self.path).get("ms_football")["settings"], settings)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "changed_reload"):
            self.store.save("ms_football", settings, expected_revision=0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_budgets_unknown_fields_and_roles_are_rejected_without_writes(self):
        for change in ({"api_key": "never-save"}, {"actions": True}, {"context_tokens": 1499},
                       {"tools": ["x", "x"]}, {"purpose": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.save("browser", {**defaults("browser"), **change}, expected_revision=0)
        with self.assertRaises(ValueError):
            self.store.get("invented_worker")
        self.assertFalse(self.path.exists())

    def test_custom_tools_only_intersect_role_assignment_and_current_grants(self):
        settings = defaults("browser")
        settings["tools"] = ["browser_observe_dom", "open_application", "mcp__github__list_issues"]
        self.store.save("browser", settings, expected_revision=0)
        available = frozenset({"browser_observe_dom", "browser_click", "open_application"})
        self.assertEqual(role_tools("browser", available), {"browser_observe_dom"})
        settings["tools"] = []
        self.store.save("browser", settings, expected_revision=1)
        self.assertFalse(role_tools("browser", available))

    def test_mcp_requires_available_tool_and_explicit_assignment_even_with_saved_profile(self):
        settings = defaults("developer")
        name = "mcp__github__list_issues"
        settings["tools"] = [name]
        self.store.save("developer", settings, expected_revision=0)
        self.assertFalse(role_tools("developer", frozenset({name})))
        self.assertEqual(role_tools("developer", frozenset({name}), [name]), {name})
        with self.assertRaises(ValueError):
            role_tools("developer", frozenset(), [name])

    def test_invalid_store_is_not_replaced_by_default_profile(self):
        self.path.write_text('{"version":99,"profiles":{}}', encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.save("browser", defaults("browser"), expected_revision=0)
        self.assertEqual(self.path.read_bytes(), before)


class SupervisedProfileTests(unittest.TestCase):
    setUp = fixtures.SupervisorTests.setUp
    approve = fixtures.SupervisorTests.approve
    state = fixtures.SupervisorTests.state

    def store(self):
        path = Path(self.temp.name) / "profiles.json"
        env = patch.dict(os.environ, {"JARVIS_AGENT_PROFILES_PATH": str(path)})
        env.start()
        self.addCleanup(env.stop)
        return AgentProfileStore(path)

    def test_profile_scope_blocks_dispatch_and_does_not_grant_missing_tools(self):
        store = self.store()
        settings = defaults("browser")
        settings["tools"] = ["browser_observe_dom"]
        store.save("browser", settings, expected_revision=0)
        self.approve(assignments={"navigate": {"agent": "browser"}})
        with self.assertRaisesRegex(SupervisorStopped, "outside_delegation_scope"):
            self.supervisor.advance()
        self.assertFalse(any(c[0] == "open_url" for c in self.registry.calls))

    def test_profile_action_budget_is_reserved_before_second_effect(self):
        store = self.store()
        settings = defaults("browser")
        settings["actions"] = 1
        store.save("browser", settings, expected_revision=0)
        self.approve(assignments={"navigate": {"agent": "browser"}}, rules={"destination": self.rule})
        with self.assertRaisesRegex(SupervisorStopped, "agent_profile_budget_exhausted:actions"):
            self.supervisor.advance()
        self.assertEqual([c[0] for c in self.registry.calls], ["open_url"])
        record = self.state().observed_state["active_supervisor"]
        self.assertEqual(record["profile_usage"]["navigate"]["actions"], 1)
        self.assertEqual(record["role_profiles"]["navigate"]["revision"], 1)

    def test_profile_is_frozen_across_reverification_and_later_ui_edits(self):
        store = self.store()
        settings = defaults("browser")
        settings["name"] = "Version validée"
        store.save("browser", settings, expected_revision=0)
        self.approve(assignments={"navigate": {"agent": "browser"}})
        self.supervisor.advance()
        settings["tools"] = []
        store.save("browser", settings, expected_revision=1)
        self.supervisor.advance()
        record = self.state().observed_state["active_supervisor"]
        self.assertEqual(record["role_profiles"]["navigate"]["revision"], 1)
        self.assertEqual(self.delegate.calls, 1)

    def test_second_model_reservation_is_stopped_before_any_tool(self):
        from jarvis_agent.active_mission_supervisor import reserve_model_request
        store = self.store()
        settings = defaults("browser")
        settings["model_calls"] = 1
        store.save("browser", settings, expected_revision=0)
        def excessive_model(text, context, **kwargs):
            reserve_model_request()
            reserve_model_request()
            self.fail("second model request must not start")
        self.delegate.run_with_context = excessive_model
        self.approve(assignments={"navigate": {"agent": "browser"}})
        with self.assertRaisesRegex(SupervisorStopped, "agent_profile_budget_exhausted:model_calls"):
            self.supervisor.advance()
        self.assertEqual(self.registry.calls, [])

    def test_supported_extra_brain_usage_does_not_block_the_next_tool(self):
        from jarvis_agent.active_mission_supervisor import record_model_usage
        self.store()
        original = self.delegate.run_with_context
        def extra_brain_reply(text, context, **kwargs):
            record_model_usage({"prompt_tokens": 31, "completion_tokens": 7}, "gemini")
            return original(text, context, **kwargs)
        self.delegate.run_with_context = extra_brain_reply
        self.approve(assignments={"navigate": {"agent": "browser"}})
        self.supervisor.advance()
        self.assertEqual([call[0] for call in self.registry.calls], ["open_url"])
        self.assertFalse(self.supervisor.model_usage_recording_failed)
        usage = self.state().observed_state["active_supervisor"]["model_usage"]["gemini"]
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (31, 7))
        with self.assertRaisesRegex(ValueError, "unknown_model_usage_provider"):
            self.supervisor.note_model_usage({}, "unregistered-provider")


class ProfileWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AgentProfileStore(Path(self.temp.name) / "profiles.json")
        self.panel = AgentProfilePanel(store=self.store)
        self.addCleanup(self.panel.close)
        self.panel.select_role("browser")

    def test_saves_real_profile_and_restores_it_after_reopening(self):
        self.panel.name.setText("Navigateur personnel ✓")
        self.panel.budgets["model_calls"].setValue(3)
        self.panel.save_button.click()
        self.assertEqual(self.store.get("browser")["settings"]["model_calls"], 3)
        self.assertEqual(self.store.get("browser")["settings"]["name"], "Navigateur personnel ✓")
        self.assertIn("aucun droit ajouté", self.panel.status.text())

    def test_telemetry_and_role_switch_preserve_drafts(self):
        self.panel.name.setText("Brouillon non enregistré")
        self.panel.apply_snapshot({"mcp": {"servers": [{"id": "github", "tools": [
            {"name": "list_issues", "allowed": True}]}]}})
        self.assertEqual(self.panel.name.text(), "Brouillon non enregistré")
        self.panel.select_role("windows")
        self.panel.select_role("browser")
        self.assertEqual(self.panel.name.text(), "Brouillon non enregistré")
        self.assertFalse(self.store.path.exists())

    def test_profile_editor_fits_narrow_hidpi_panel(self):
        self.panel.setFont(QFont("Segoe UI", 14))
        self.panel.resize(300, 700)
        self.panel.show()
        self.app.processEvents()
        scroll = self.panel.findChild(QScrollArea)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        scroll.ensureWidgetVisible(self.panel.save_button)
        self.app.processEvents()
        self.assertTrue(self.panel.save_button.isVisible())
