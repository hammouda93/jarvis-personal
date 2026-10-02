import json
import tempfile
import unittest
from pathlib import Path

from jarvis_agent.agent_knowledge import AgentKnowledgeStore


class AgentKnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "agent_knowledge.sqlite3"
        self.store = AgentKnowledgeStore(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_is_separate_and_persistent(self):
        self.store.record_lesson(
            scope="ui",
            pattern="user confirms previous action",
            rule="Do not repeat the previous mutation.",
        )
        reopened = AgentKnowledgeStore(self.db_path)

        self.assertEqual(reopened.stats()["lessons"], 1)

    def test_skill_upsert_versions_and_merges(self):
        first = self.store.upsert_skill(
            name="messaging send message",
            goal="Send a message in a messaging application.",
            app_scope="messaging",
            procedure=[
                "Locate the contact search.",
                "Open the exact conversation.",
            ],
            success_checks=["Conversation header matches the selected contact."],
            failure_patterns=["Do not confuse contact search with composer."],
        )
        second = self.store.upsert_skill(
            name="messaging send message",
            goal="Send a message in a messaging application.",
            app_scope="messaging",
            procedure=[
                "Locate the message composer.",
                "Verify the sent message in conversation.",
            ],
            success_checks=["Message is visible after sending."],
        )

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 2)
        self.assertIn("Open the exact conversation.", second.procedure)
        self.assertIn("Locate the message composer.", second.procedure)
        self.assertEqual(self.store.stats()["skills"], 1)

    def test_lesson_repeated_feedback_increases_evidence(self):
        first = self.store.record_lesson(
            scope="ui",
            pattern="confirmation after successful mutation",
            rule="Treat confirmation as feedback, not a repeat command.",
        )
        second = self.store.record_lesson(
            scope="ui",
            pattern="confirmation after successful mutation",
            rule="Treat confirmation as feedback, not a repeat command.",
        )

        self.assertEqual(first.evidence_count, 1)
        self.assertEqual(second.evidence_count, 2)
        self.assertGreaterEqual(second.confidence, first.confidence)

    def test_app_profile_merges_observations(self):
        self.store.upsert_app_profile(
            display_name="WhatsApp",
            aliases=["WhatsApp Desktop"],
            window_title_patterns=["WhatsApp"],
            observed_capabilities=["Edit", "Button"],
            success=True,
        )
        profile = self.store.upsert_app_profile(
            display_name="WhatsApp",
            aliases=["WhatsApp"],
            observed_capabilities=["DataItem"],
            success=True,
        )

        self.assertIn("WhatsApp Desktop", profile.aliases)
        self.assertIn("DataItem", profile.observed_capabilities)
        self.assertEqual(profile.success_count, 2)

    def test_relevant_context_returns_matching_skill_lesson_and_app(self):
        self.store.upsert_skill(
            name="messaging_send_message",
            goal="Send a message in a messaging application.",
            app_scope="WhatsApp messaging",
            procedure=["Open conversation", "Use message composer", "Verify send"],
            success_checks=["Message visible"],
        )
        self.store.record_lesson(
            scope="messaging",
            pattern="contact search vs composer",
            rule="Do not type message text into contact search.",
        )
        self.store.upsert_app_profile(
            display_name="WhatsApp",
            observed_capabilities=["search contacts", "message composer"],
            success=True,
        )

        context = self.store.relevant_context(
            "Envoie un message avec WhatsApp",
            limit=3,
        )

        self.assertTrue(context["skills"])
        self.assertTrue(context["lessons"])
        self.assertTrue(context["app_profiles"])

    def test_record_run_keeps_structured_proof(self):
        run = self.store.record_run(
            status="verified",
            goal="Edit a document",
            actions=["inspect_active_window", "write_ui_element"],
            proof={"verified": True, "tools": [{"name": "write_ui_element"}]},
        )

        self.assertEqual(run.status, "verified")
        self.assertTrue(run.proof["verified"])
        self.assertEqual(self.store.stats()["skill_runs"], 1)

    def test_export_redacts_common_secrets_and_user_path(self):
        self.store.record_lesson(
            scope="global",
            pattern="debug local app",
            rule=(
                "Never store password=secret123 or admin@example.com "
                "from C:\\Users\\salah\\Downloads."
            ),
        )
        path = Path(self.tmp.name) / "export.json"
        self.store.export_snapshot(path, anonymize=True)

        payload = path.read_text(encoding="utf-8")
        self.assertNotIn("secret123", payload)
        self.assertNotIn("admin@example.com", payload)
        self.assertNotIn(r"C:\Users\salah", payload)
        self.assertIn("<secret>", payload)
        self.assertIn("<email>", payload)
        self.assertIn("%USERPROFILE%", payload)

    def test_clear_operational_knowledge_keeps_store_usable(self):
        self.store.upsert_skill(
            name="temporary_skill",
            goal="Temporary generic workflow",
            procedure=["do one thing"],
            success_checks=["result visible"],
        )
        self.store.record_lesson(
            scope="ui",
            pattern="temporary correction",
            rule="Use the corrected behavior next time.",
        )
        self.store.upsert_app_profile(
            display_name="Temporary App",
            success=True,
        )
        self.store.record_run(
            status="verified",
            goal="workflow:test",
            actions=["write_ui_element"],
            proof={"verified": True},
        )

        before = self.store.clear_operational_knowledge()

        self.assertEqual(before["skills"], 1)
        self.assertEqual(
            self.store.stats(),
            {
                "skills": 0,
                "lessons": 0,
                "app_profiles": 0,
                "skill_runs": 0,
            },
        )
        self.store.record_lesson(
            scope="ui",
            pattern="new correction",
            rule="The store remains usable after reset.",
        )
        self.assertEqual(self.store.stats()["lessons"], 1)

    def test_export_is_valid_json(self):
        path = Path(self.tmp.name) / "export.json"
        self.store.export_snapshot(path)
        payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema"], 1)
        self.assertIn("stats", payload)
        self.assertIn("skills", payload)
        self.assertIn("recent_runs", payload)


if __name__ == "__main__":
    unittest.main()
