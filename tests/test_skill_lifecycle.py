"""Persistent Skill controls with synthetic knowledge only, no app execution."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from jarvis_agent.agent_knowledge import AgentKnowledgeStore
from jarvis_agent.assistant_v3 import AssistantWorker
from jarvis_agent.skill_control import SkillCommand, SkillControlInbox, perform_skill_command
from jarvis_agent.skills_operator_panel import SkillsPanel
from jarvis_agent.sqlite_utils import ClosingConnection


class SkillLifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "knowledge.sqlite3"
        self.store = AgentKnowledgeStore(self.path)
        self.first = self.store.upsert_skill(name="generic_document", goal="Edit a generic document",
            procedure=["Observe the current document", "Ask before replacing content"],
            success_checks=["Observe the saved document"], source="verified_agent")

    def edit(self, version=1):
        return self.store.revise_skill(self.first.name, expected_version=version,
            goal="Edit a generic document safely", app_scope="documents",
            procedure=["Observe a fresh target", "Save the intended change"],
            success_checks=["Observe final content"], failure_patterns=["Do not reuse stale targets"])

    def test_old_revision_is_immutable_and_survives_reopen(self):
        self.edit()
        reopened = AgentKnowledgeStore(self.path)
        history = reopened.skill_history(self.first.name)
        self.assertEqual([x["version"] for x in history], [2, 1])
        self.assertEqual(history[1]["snapshot"]["procedure"], list(self.first.procedure))
        self.assertEqual(history[0]["change_kind"], "operator_edit")

    def test_disabled_skill_is_excluded_from_context_and_learning_cannot_enable_it(self):
        disabled = self.store.set_skill_active(self.first.name, False, expected_version=1)
        self.assertEqual(disabled.version, 2)
        self.assertEqual(self.store.relevant_context("generic document")["skills"], [])
        with self.assertRaises(KeyError):
            self.store.get_skill(self.first.name)
        updated = self.store.upsert_skill(name=self.first.name, goal=self.first.goal, procedure=["Fresh observation"])
        self.assertFalse(updated.active)
        reopened = AgentKnowledgeStore(self.path)
        self.assertEqual(reopened.list_skills(), [])
        self.assertEqual(len(reopened.list_skills(include_inactive=True)), 1)
        enabled = reopened.set_skill_active(self.first.name, True, expected_version=3)
        self.assertTrue(enabled.active)
        self.assertTrue(reopened.relevant_context("generic document")["skills"])

    def test_restore_creates_new_revision_preserves_disable_and_usage(self):
        self.edit()
        self.store.record_run(status="verified", actions=["observe"], skill_name=self.first.name,
                              proof={"verified": True})
        self.store.set_skill_active(self.first.name, False, expected_version=2)
        restored = self.store.restore_skill(self.first.name, 1, expected_version=3)
        self.assertEqual(restored.version, 4)
        self.assertFalse(restored.active)
        self.assertEqual(restored.success_count, 1)
        self.assertEqual(restored.procedure, self.first.procedure)
        self.assertEqual(restored.source, "operator_restore")
        self.assertEqual(self.store.skill_history(self.first.name)[0]["restored_from"], 1)

    def test_stale_operator_write_cannot_overwrite_another_writer(self):
        other = AgentKnowledgeStore(self.path)
        self.edit()
        with self.assertRaisesRegex(ValueError, "skill_version_changed"):
            other.set_skill_active(self.first.name, False, expected_version=1)
        with self.assertRaisesRegex(ValueError, "skill_version_changed"):
            other.restore_skill(self.first.name, 1, expected_version=1)
        self.assertEqual(other.get_skill(self.first.name).version, 2)

    def test_bad_restore_rolls_back_transaction(self):
        with self.assertRaises(KeyError):
            self.store.restore_skill(self.first.name, 999, expected_version=1)
        self.assertEqual(self.store.get_skill(self.first.name), self.first)
        self.assertEqual(len(self.store.skill_history(self.first.name)), 1)

    def test_invalid_versions_and_enable_values_are_refused(self):
        for invalid in (True, 0, -1, "1"):
            with self.assertRaises(ValueError):
                self.store.restore_skill(self.first.name, invalid, expected_version=1)
            with self.assertRaises(ValueError):
                self.store.set_skill_active(self.first.name, False, expected_version=invalid)
        with self.assertRaises(ValueError):
            self.store.set_skill_active(self.first.name, "false", expected_version=1)

    def test_history_failure_does_not_leave_an_unversioned_edit(self):
        with sqlite3.connect(self.path, factory=ClosingConnection) as conn:
            conn.execute("CREATE TRIGGER stop_revision BEFORE INSERT ON skill_revisions BEGIN SELECT RAISE(ABORT, 'no_history'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.edit()
        self.assertEqual(self.store.get_skill(self.first.name), self.first)

    def test_existing_database_migration_only_keeps_known_legacy_version(self):
        with sqlite3.connect(self.path, factory=ClosingConnection) as conn:
            conn.execute("DROP TABLE skill_revisions")
            conn.execute("UPDATE skills SET version = 7, active = 0")
        reopened = AgentKnowledgeStore(self.path)
        history = reopened.skill_history(self.first.name)
        self.assertEqual([x["version"] for x in history], [7])
        self.assertEqual(history[0]["change_kind"], "legacy_baseline")
        self.assertFalse(reopened.get_skill(self.first.name, include_inactive=True).active)
        AgentKnowledgeStore(self.path)
        self.assertEqual(len(reopened.skill_history(self.first.name)), 1)

    def test_reset_clears_revisions_and_remains_usable(self):
        self.store.clear_operational_knowledge()
        with sqlite3.connect(self.path, factory=ClosingConnection) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM skill_revisions").fetchone()[0], 0)
        self.store.upsert_skill(name="new_document", goal="A fresh generic document", procedure=["Observe"])
        self.assertEqual(len(self.store.skill_history("new_document")), 1)

    def test_commands_have_no_dispatch_path_or_permission_overrides(self):
        inbox = SkillControlInbox()
        self.assertFalse(inbox.submit("execute", json.dumps({"name": self.first.name})))
        self.assertFalse(inbox.submit("enable", json.dumps({"name": self.first.name, "expected_version": 1, "approved": True})))
        self.assertFalse(inbox.submit("restore", json.dumps({"name": self.first.name, "expected_version": True, "version": 1})))
        self.assertTrue(inbox.submit("disable", json.dumps({"name": self.first.name, "expected_version": 1})))
        result = perform_skill_command(self.store, inbox.pop_nowait())
        self.assertFalse(result["skill"]["active"])
        self.assertFalse(result["external_action_dispatched"])
        self.assertEqual(self.store.stats()["skill_runs"], 0)

    def test_inbox_is_bounded_and_does_not_silently_drop_a_write(self):
        inbox = SkillControlInbox()
        for _ in range(16):
            self.assertTrue(inbox.submit("list"))
        self.assertFalse(inbox.submit("list"))

    def test_worker_reports_stale_revision_without_crashing_or_tts(self):
        worker = AssistantWorker.__new__(AssistantWorker)
        QObject.__init__(worker)
        worker._skill_store = self.store
        replies = []
        worker.skill_control_result.connect(replies.append)
        worker._run_skill_control(SkillCommand("disable", {"name": self.first.name, "expected_version": 99}))
        self.assertFalse(replies[0]["success"])
        self.assertEqual(replies[0]["reason"], "skill_version_changed")


class SkillsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = SkillsPanel()
        self.addCleanup(self.panel.close)
        self.calls = []
        self.panel.requested.connect(lambda op, value: self.calls.append((op, json.loads(value))))
        self.skill = {"name": "generic_document", "goal": "Edit document", "app_scope": "documents",
                      "version": 2, "source": "operator_edit", "active": True,
                      "procedure": ["Observe", "Save"], "success_checks": ["Read final content"], "failure_patterns": []}

    def load(self):
        self.panel.show_result({"success": True, "operation": "view", "skills": [self.skill],
            "skill": self.skill, "history": [{"version": 1, "change_kind": "created"}]})

    def test_rendering_does_not_issue_a_mutation(self):
        self.load()
        self.assertEqual(self.calls, [])
        self.assertTrue(self.panel.enabled.isChecked())
        self.assertEqual(self.panel.procedure.toPlainText(), "Observe\nSave")

    def test_edit_and_restore_bound_to_reviewed_version(self):
        self.load()
        self.panel.procedure.setPlainText("Observe a new target")
        self.panel.save_button.click()
        op, args = self.calls[-1]
        self.assertEqual(op, "edit")
        self.assertEqual(args["expected_version"], 2)
        self.assertEqual(args["procedure"], ["Observe a new target"])
        self.assertFalse(self.panel.save_button.isEnabled())
        self.load()
        self.panel.restore_button.click()
        self.assertEqual(self.calls[-1], ("restore", {"name": "generic_document", "expected_version": 2, "version": 1}))

    def test_failed_toggle_restores_actual_enablement_without_retry(self):
        self.load()
        self.panel.enabled.setChecked(False)
        self.assertEqual(self.calls[-1][0], "disable")
        self.panel.show_result({"success": False, "reason": "skill_version_changed"})
        self.assertTrue(self.panel.enabled.isChecked())
        self.assertEqual(len(self.calls), 1)
        self.assertIn("refusee", self.panel.feedback.text())


if __name__ == "__main__":
    unittest.main()
