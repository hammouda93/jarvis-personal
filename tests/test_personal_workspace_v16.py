"""Daily and business organisation across SQLite, UI, model payloads and MCP.

Fixtures contain no personal data. No network, credential access or UI action
outside Qt is performed. MCP transport and model responses are simulated.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from jarvis_agent.agent_runtime import CerebrasResponsesAgent, GroqResponsesAgent
from jarvis_agent.config import settings
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.personal_workspace import (
    ACTIVITY_DEFAULTS, PROFILE_DEFAULTS, PersonalWorkspaceStore, service_snapshot, wire_size,
)
from jarvis_agent.request_context import ContextToolRegistry, RequestContext
from jarvis_agent.workspace_panel import PersonalWorkspacePanel
from jarvis_agent.workspace_tools import WorkspaceToolRegistry
from tests.test_agent_runtime import FakeTools
from tests.test_daily_use_regressions_v15 import response


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = PersonalWorkspaceStore(self.root / "workspace.sqlite3")

    def activity(self, title="Préparer la vidéo", **fields):
        return self.store.save_activity({**ACTIVITY_DEFAULTS, "title": title, **fields})


class WorkspaceStoreTests(WorkspaceFixture):
    def test_first_reads_do_not_create_or_import_any_personal_data(self):
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.store.profile()["revision"], 0)
        self.assertEqual(self.store.activities(), [])
        self.assertEqual(self.store.preparations(), [])
        self.assertEqual(self.store.bindings(), {})
        self.assertEqual(self.store.context(), "")
        self.assertEqual(self.store.brief()["active_total"], 0)
        self.assertFalse(self.store.path.exists())

    def test_profile_survives_reopening_and_stale_edits_cannot_overwrite(self):
        profile = {**PROFILE_DEFAULTS, "display_name": "Fixture عربي", "timezone": "Africa/Abidjan",
                   "roles": ["Football", "Développement"], "about": "Fixture project", "use_for_agent": True}
        saved = self.store.save_profile(profile, expected_revision=0)
        reopened = PersonalWorkspaceStore(self.store.path)
        self.assertEqual(reopened.profile(), saved)
        with self.assertRaisesRegex(ValueError, "changed_reload"):
            reopened.save_profile({**profile, "about": "Stale overwrite"}, expected_revision=0)
        self.assertEqual(reopened.profile(), saved)

    def test_profile_consent_and_bounded_fields_control_automatic_context(self):
        data = {**PROFILE_DEFAULTS, "display_name": "PRIVATE_FIXTURE", "about": "Notes " * 400}
        self.store.save_profile(data, expected_revision=0)
        self.assertEqual(self.store.context(), "")
        self.store.save_profile({**data, "use_for_agent": True}, expected_revision=1)
        context = self.store.context()
        self.assertIn("PRIVATE_FIXTURE", context)
        self.assertIn('"partial_fields":["about"]', context)
        self.assertLess(len(context), 4500)
        self.assertEqual(self.store.profile()["data"]["about"], data["about"].strip())
        self.store.save_profile({**data, "use_for_agent": False}, expected_revision=2)
        self.assertEqual(self.store.context(), "")

    def test_invalid_profile_and_dates_are_rejected_before_creating_a_store(self):
        for change in ({"timezone": "Mars/Unknown"}, {"use_for_agent": "true"},
                       {"api_key": "never-store"}, {"roles": [42]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.save_profile({**PROFILE_DEFAULTS, **change}, expected_revision=0)
        for change in ({"due_on": "2026-02-30"}, {"due_on": "20261020"},
                       {"at_time": "18:00"}, {"due_on": "2026-10-20", "at_time": "25:00"},
                       {"status": "sent_to_customer"}, {"domain": "invented"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.activity(**change)
        self.assertFalse(self.store.path.exists())

    def test_concurrent_first_writers_have_one_winner_without_corrupting_schema(self):
        barrier = Barrier(2)
        def save(name):
            barrier.wait()
            try:
                self.store.save_profile({**PROFILE_DEFAULTS, "display_name": name}, expected_revision=0)
                return "saved"
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ("A", "B")))
        self.assertEqual(results.count("saved"), 1)
        self.assertIn("workspace_changed_reload_required", results)
        self.assertEqual(self.store.profile()["revision"], 1)

    def test_activities_have_revisions_unicode_search_and_exact_date_filters(self):
        row = self.activity("Vidéo العربية", domain="ms_football", subject="Joueur fixture", due_on="2026-10-20")
        other = self.activity("Vidéo application", domain="software", due_on="2026-10-21")
        self.assertEqual([r["id"] for r in self.store.activities(query="VIDÉO", domain="ms_football", due_on="2026-10-20")], [row["id"]])
        changed = self.store.save_activity({**row["data"], "status": "done"}, identity=row["id"], expected_revision=1)
        self.assertEqual(changed["revision"], 2)
        with self.assertRaisesRegex(ValueError, "changed_reload"):
            self.store.save_activity({**row["data"], "details": "stale"}, identity=row["id"], expected_revision=1)
        self.assertEqual(self.store.activity(row["id"]), changed)
        self.assertEqual(self.store.activity(other["id"]), other)

    def test_brief_uses_saved_timezone_and_excludes_terminal_local_records(self):
        self.store.save_profile({**PROFILE_DEFAULTS, "timezone": "America/Los_Angeles"}, expected_revision=0)
        self.activity("Yesterday", due_on="2026-10-14")
        today = self.activity("Evening session", kind="event", due_on="2026-10-15", at_time="19:00")
        self.activity("Tomorrow", due_on="2026-10-16", status="waiting")
        self.activity("Without date")
        self.activity("Already closed", due_on="2026-10-14", status="done")
        brief = self.store.brief(now=datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(brief["date"], "2026-10-15")
        self.assertEqual(brief["counts"], {"overdue": 1, "today": 1, "upcoming": 1, "waiting": 1, "unscheduled": 1})
        self.assertEqual(brief["groups"]["today"][0]["id"], today["id"])
        self.assertFalse(brief["external_calendars_consulted"])
        self.assertFalse(brief["background_notifications"])
        with self.assertRaises(ValueError):
            self.store.brief(now=datetime(2026, 10, 16))

    def test_idempotent_creates_do_not_duplicate_and_key_reuse_cannot_change_payload(self):
        data = {**ACTIVITY_DEFAULTS, "title": "One local request"}
        first = self.store.save_activity(data, request_key="request-1")
        second = self.store.save_activity(data, request_key="request-1")
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["reused_receipt"])
        self.assertEqual(len(self.store.activities()), 1)
        with self.assertRaisesRegex(ValueError, "key_reused"):
            self.store.save_activity({**data, "title": "Changed"}, request_key="request-1")

    def test_preparation_never_claims_external_dispatch_or_calendar_creation(self):
        row = self.activity("Client follow-up", domain="ms_football", status="waiting")
        draft = self.store.prepare_action(service="gmail", operation="Send follow-up",
            instructions="Ask fixture client for the missing match; do not attach files.", activity_id=row["id"], request_key="draft")
        self.assertFalse(draft["data"]["external_dispatched"])
        self.assertFalse(draft["data"]["external_verified"])
        self.assertEqual(draft["data"]["state"], "prepared_local")
        again = self.store.prepare_action(service="gmail", operation="Send follow-up",
            instructions=draft["data"]["instructions"], activity_id=row["id"], request_key="draft")
        self.assertEqual(draft["id"], again["id"])
        self.assertEqual(len(self.store.preparations()), 1)

    def test_foreign_or_future_database_is_never_migrated_or_replaced(self):
        with sqlite3.connect(self.store.path) as con:
            con.execute("CREATE TABLE unrelated(value TEXT)")
            con.execute("INSERT INTO unrelated VALUES ('preserve')")
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            self.store.save_profile(PROFILE_DEFAULTS, expected_revision=0)
        self.assertEqual(self.store.path.read_bytes(), before)
        with sqlite3.connect(self.store.path) as con:
            con.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.store.profile()


class WorkspaceMCPTests(WorkspaceFixture):
    def gateway(self):
        registry = MCPRegistry(self.root / "mcp.json")
        registry.add_http("workspace", "https://fixture.invalid/mcp")
        registry.set_enabled("workspace", True)
        registry.discover("workspace", [{"name": "list_events", "input_schema": {"type": "object", "properties": {}}}])
        registry.note_successful_discovery("workspace")
        registry.set_runtime_enabled(False)
        transport = Mock()
        gateway = MCPToolRegistry(FakeTools(), registry=registry, transport=transport, runtime_gate=registry.runtime_enabled)
        return registry, transport, gateway

    def test_one_service_can_bind_to_custom_mcp_without_granting_access(self):
        registry, transport, gateway = self.gateway()
        tools = WorkspaceToolRegistry(gateway, store=self.store, mcp_tools=gateway)
        self.store.bind_service("google_calendar", "workspace", expected_revision=0)
        self.assertEqual(next(s for s in tools.services() if s["id"] == "google_calendar")["state"], "runtime_off")
        registry.set_runtime_enabled(True)
        self.assertEqual(next(s for s in tools.services() if s["id"] == "google_calendar")["state"], "no_allowed_tools")
        registry.set_auto_readonly("workspace", True)
        service = next(s for s in tools.services() if s["id"] == "google_calendar")
        self.assertEqual(service["state"], "tools_available")
        self.assertEqual(service["tools"], ["mcp__workspace__list_events"])
        self.assertFalse(service["connected_now"])
        transport.call_tool.assert_not_called()

    def test_prepared_action_becomes_actionable_after_connection_without_automatic_replay(self):
        registry, transport, gateway = self.gateway()
        tools = WorkspaceToolRegistry(gateway, store=self.store, mcp_tools=gateway)
        prep = self.store.prepare_action(service="google_calendar", operation="Read next events", instructions="Read only the next events.")
        before = tools.execute("workspace_read", {"view": "preparation", "id": prep["id"]})
        self.assertEqual(json.loads(before.detail)["result"]["service_readiness"]["state"], "not_configured")
        self.store.bind_service("google_calendar", "workspace", expected_revision=0)
        registry.set_runtime_enabled(True)
        registry.set_auto_readonly("workspace", True)
        after = tools.execute("workspace_read", {"view": "preparation", "id": prep["id"]})
        self.assertIn("mcp__workspace__list_events", json.loads(after.detail)["result"]["service_readiness"]["tools"])
        transport.call_tool.assert_not_called()
        self.assertEqual(self.store.preparation(prep["id"]), prep)

    def test_local_read_cannot_enable_mcp_or_override_original_confirmations(self):
        registry, transport, gateway = self.gateway()
        tools = WorkspaceToolRegistry(gateway, store=self.store, mcp_tools=gateway)
        original = registry.path.read_bytes()
        tools.execute("workspace_read", {"view": "services"})
        self.assertEqual(registry.path.read_bytes(), original)
        self.assertTrue(tools.requires_confirmation("mcp__workspace__list_events"))
        denied = tools.execute("mcp__workspace__list_events", {})
        self.assertFalse(denied.success)
        transport.call_tool.assert_not_called()

    def test_connected_service_execution_still_uses_original_gateway_and_receipt(self):
        registry, transport, gateway = self.gateway()
        self.store.bind_service("google_calendar", "workspace", expected_revision=0)
        registry.set_runtime_enabled(True)
        registry.set_auto_readonly("workspace", True)
        transport.call_tool.return_value = {"success": True, "message": "Fixture events", "data": {"events": []}}
        tools = WorkspaceToolRegistry(gateway, store=self.store, mcp_tools=gateway)
        result = tools.execute("mcp__workspace__list_events", {})
        self.assertTrue(result.success)
        transport.call_tool.assert_called_once()
        self.assertEqual(json.loads(result.detail)["source"]["server_id"], "workspace")
        self.assertFalse(json.loads(result.detail)["verified"])


class WorkspaceRuntimeTests(WorkspaceFixture):
    def agent(self, cls=CerebrasResponsesAgent):
        tools = WorkspaceToolRegistry(FakeTools(), store=self.store)
        context = RequestContext(enabled=True)
        agent = cls(ContextToolRegistry(tools, context))
        agent.request_context = context
        agent.api_key = "offline-fixture"
        client = Mock()
        agent._client = client
        return agent, client, tools

    def test_profile_reaches_actual_primary_request_and_optout_removes_projection(self):
        self.store.save_profile({**PROFILE_DEFAULTS, "display_name": "PROFILE_FIXTURE", "use_for_agent": True}, expected_revision=0)
        agent, client, _ = self.agent()
        client.chat.completions.create.return_value = response("Bonjour.")
        with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
            agent.run("Présente brièvement mon espace de travail.")
            sent = client.chat.completions.create.call_args.kwargs
            self.assertIn("PROFILE_FIXTURE", sent["messages"][0]["content"])
            self.store.save_profile({**self.store.profile()["data"], "use_for_agent": False}, expected_revision=1)
            agent.start_request_scope()
            agent.run("Explique la notion d’activité locale.")
        self.assertNotIn("PROFILE_FIXTURE", client.chat.completions.create.call_args.kwargs["messages"][0]["content"])

    def test_profile_is_preserved_through_primary_quota_and_groq_fallback(self):
        self.store.save_profile({**PROFILE_DEFAULTS, "display_name": "CASCADE_FIXTURE", "use_for_agent": True}, expected_revision=0)
        agent, primary, _ = self.agent()
        primary.chat.completions.create.side_effect = RuntimeError("429 request_quota_exceeded")
        fallback = Mock()
        fallback.chat.completions.create.return_value = response("Contexte reçu.")
        configured = replace(settings, cerebras_secondary_api_key="", groq_api_key="offline", cerebras_fallback_to_groq=True)
        with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}), \
             patch("jarvis_agent.agent_runtime.settings", configured), patch("openai.OpenAI", return_value=fallback):
            agent.run("Présente brièvement mon espace.")
        self.assertIn("CASCADE_FIXTURE", json.dumps(fallback.chat.completions.create.call_args.kwargs["messages"]))

    def test_actual_tool_round_creates_one_task_then_reads_the_saved_daily_brief(self):
        agent, client, tools = self.agent(GroqResponsesAgent)
        client.chat.completions.create.side_effect = [
            response(name="request_tool_capabilities", arguments={"names": ["workspace_save_activity"]}, identity="select"),
            response(name="workspace_save_activity", arguments={"data": {"title": "Relancer le client fixture", "domain": "ms_football", "status": "waiting"}}, identity="save"),
            response(name="workspace_read", arguments={"view": "overview"}, identity="brief"),
            response("Le suivi local est enregistré, le message n’a pas été envoyé.")]
        with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
            turn = agent.run("Ajoute un suivi local pour relancer le client fixture ; garde-le en attente sans envoyer de message.")
        self.assertEqual(len(self.store.activities()), 1)
        self.assertEqual([a.name for a in turn.actions], ["request_tool_capabilities", "workspace_save_activity", "workspace_read"])
        self.assertFalse(tools.delegate.calls)
        requests = client.chat.completions.create.call_args_list
        self.assertIn("workspace_save_activity", {s.get("function", {}).get("name") for s in requests[1].kwargs["tools"]})
        self.assertTrue(json.loads(turn.actions[1].detail)["verified"])
        self.assertFalse(json.loads(turn.actions[1].detail)["external_dispatched"])

    def test_profile_changes_require_confirmation_and_no_scope_widening(self):
        _, _, tools = self.agent()
        args = {"data": {"display_name": "Fixture"}, "expected_revision": 0}
        denied = tools.execute("workspace_update_profile", args)
        self.assertFalse(denied.success)
        self.assertFalse(self.store.path.exists())
        allowed = tools.execute("workspace_update_profile", args, approved=True)
        self.assertTrue(allowed.success)
        self.assertFalse(self.store.profile()["data"]["use_for_agent"])

    def test_repeated_activity_call_within_turn_has_one_local_effect(self):
        _, _, tools = self.agent()
        tools.workspace_begin_turn("Ajoute un suivi")
        args = {"data": {"title": "Un seul suivi"}}
        first, second = tools.execute("workspace_save_activity", args), tools.execute("workspace_save_activity", args)
        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertEqual(len(self.store.activities()), 1)
        tools.workspace_begin_turn("Ajoute un autre suivi identique")
        self.assertTrue(tools.execute("workspace_save_activity", args).success)
        self.assertEqual(len(self.store.activities()), 2)

    def test_many_long_records_are_paginated_and_full_record_can_be_reassembled(self):
        expected = set()
        for index in range(18):
            row = self.activity("متابعة " * 28 + str(index), details="🧠" * 2800, subject="نادي " * 25)
            expected.add(row["id"])
        _, _, tools = self.agent()
        offset, found = 0, set()
        while True:
            action = tools.execute("workspace_read", {"view": "activities", "limit": 50, "offset": offset})
            self.assertTrue(action.success)
            page = json.loads(action.detail)["result"]
            self.assertLess(wire_size(page), 6500)
            self.assertTrue(page["items"])
            found.update(row["id"] for row in page["items"])
            if page["next_offset"] is None:
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        self.assertEqual(found, expected)
        identity = next(iter(expected))
        record, offset, chunks = None, 0, []
        while True:
            args = {"view": "activity", "id": identity, "offset": offset}
            if offset:
                args["expected_revision"] = record["revision"]
            action = tools.execute("workspace_read", args)
            self.assertTrue(action.success)
            record = json.loads(action.detail)["result"]
            self.assertLess(wire_size(record), 6500)
            chunks.append(record["content_json_fragment"])
            offset = record["next_offset"]
            if offset is None:
                break
        self.assertEqual(json.loads("".join(chunks)), self.store.activity(identity))

    def test_partial_reads_refuse_changed_revisions_instead_of_mixing_versions(self):
        row = self.activity(details="🧠" * 2500)
        _, _, tools = self.agent()
        first = json.loads(tools.execute("workspace_read", {"view": "activity", "id": row["id"]}).detail)["result"]
        self.assertIsNotNone(first["next_offset"])
        self.store.save_activity({**row["data"], "details": "New source"}, identity=row["id"], expected_revision=1)
        refused = tools.execute("workspace_read", {"view": "activity", "id": row["id"], "offset": first["next_offset"], "expected_revision": 1})
        self.assertFalse(refused.success)
        self.assertFalse(json.loads(refused.detail)["dispatched"])

    def test_large_arabic_profile_fits_actual_request_budget_without_changing_saved_source(self):
        data = {**PROFILE_DEFAULTS, "display_name": "مالك", "about": "ع" * 3000,
                "objectives": "🧠" * 2000, "preferences": "ر" * 2000,
                "roles": ["دور" * 30 + str(i) for i in range(12)], "use_for_agent": True}
        self.store.save_profile(data, expected_revision=0)
        agent, client, _ = self.agent(GroqResponsesAgent)
        client.chat.completions.create.return_value = response("Contexte reçu.")
        with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1", "JARVIS_REQUEST_ESTIMATED_TOKEN_BUDGET": "7000"}):
            agent.run("Présente mon espace local en une phrase.")
        self.assertLessEqual(agent.request_context.last_request["estimated_tokens"], 7000)
        self.assertEqual(self.store.profile()["data"], data)
        self.assertIn("partial_fields", client.chat.completions.create.call_args.kwargs["messages"][0]["content"])

    def test_local_commit_then_lost_ack_is_reported_uncertain_and_same_request_has_no_duplicate(self):
        _, _, tools = self.agent()
        tools.workspace_begin_turn("Ajoute une activité")
        original = self.store.save_activity
        def commit_then_error(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("lost local acknowledgement")
        args = {"data": {"title": "One committed activity"}}
        with patch.object(self.store, "save_activity", side_effect=commit_then_error):
            first = tools.execute("workspace_save_activity", args)
        self.assertFalse(first.success)
        self.assertTrue(json.loads(first.detail)["outcome_unknown"])
        self.assertTrue(tools.execute("workspace_save_activity", args).success)
        self.assertEqual(len(self.store.activities()), 1)

    def test_personal_tool_data_is_kept_for_reasoning_and_omitted_from_diagnostic_logs(self):
        row = self.activity(details="PRIVATE_SOURCE_DO_NOT_LOG")
        agent, client, _ = self.agent()
        client.chat.completions.create.side_effect = [
            response(name="workspace_read", arguments={"view": "activity", "id": row["id"]}, identity="read"),
            response("Dossier consulté.")]
        lines = []
        with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
            agent.run("Consulte ce dossier local.", log=lines.append)
        self.assertNotIn("PRIVATE_SOURCE_DO_NOT_LOG", "\n".join(lines))
        self.assertIn("PRIVATE_SOURCE_DO_NOT_LOG", json.dumps(agent._messages))


class WorkspacePanelTests(WorkspaceFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self):
        panel = PersonalWorkspacePanel(store=self.store)
        self.addCleanup(panel.close)
        return panel

    def test_gui_saves_profile_activity_and_preparation_without_network_or_model(self):
        panel = self.panel()
        panel.profile_fields["display_name"].setText("GUI fixture")
        panel.use_profile.setChecked(True)
        panel.save_profile_button.click()
        panel.new_activity()
        panel.activity_fields["title"].setText("Livrer la vidéo fixture")
        panel.activity_fields["domain"].setCurrentIndex(panel.activity_fields["domain"].findData("ms_football"))
        panel.save_activity_button.click()
        self.assertEqual(self.store.profile()["data"]["display_name"], "GUI fixture")
        self.assertEqual(self.store.activities()[0]["data"]["domain"], "ms_football")
        panel.preparation_service.setCurrentIndex(panel.preparation_service.findData("gmail"))
        panel.preparation_operation.setText("Relance fixture")
        panel.preparation_instructions.setPlainText("Préparer un brouillon de relance, sans envoi.")
        panel.save_preparation()
        self.assertEqual(len(self.store.preparations()), 1)
        self.assertIn("aucune action externe", panel.preparation_detail.toPlainText())

    def test_refresh_keeps_unsaved_profile_and_activity_and_detects_conflicting_save(self):
        row = self.activity("Original")
        panel = self.panel()
        panel.open_activity(row["id"])
        panel.activity_fields["title"].setText("Unsaved local edit")
        panel.profile_fields["display_name"].setText("Unsaved profile")
        changed = self.store.save_activity({**row["data"], "title": "Other writer"}, identity=row["id"], expected_revision=1)
        panel.refresh()
        self.assertEqual(panel.activity_fields["title"].text(), "Unsaved local edit")
        self.assertEqual(panel.profile_fields["display_name"].text(), "Unsaved profile")
        panel.save_activity()
        self.assertIn("changé ailleurs", panel.status_line.text())
        self.assertEqual(self.store.activity(row["id"]), changed)

    def test_delegation_emits_only_a_request_and_never_marks_preparation_executed(self):
        row = self.store.prepare_action(service="gmail", operation="Mail", instructions="Fixture content")
        panel = self.panel()
        emitted = []
        panel.request_ready.connect(emitted.append)
        panel.preparation_tree.setCurrentItem(panel.preparation_tree.topLevelItem(0))
        panel.delegate_preparation_button.click()
        self.assertIn(row["id"], emitted[0])
        self.assertIn("workspace_read", emitted[0])
        self.assertEqual(self.store.preparation(row["id"]), row)

    def test_widgets_escape_user_content_and_fit_narrow_logical_panels(self):
        row = self.activity("<script>Fixture</script>", due_on="2026-10-16")
        panel = self.panel()
        self.assertNotIn("<script>", panel.overview.toHtml())
        self.assertIn("&lt;script&gt;", panel.overview.toHtml())
        panel.setFont(QFont("Segoe UI", 13))
        panel.resize(300, 700)
        panel.show()
        for index in range(panel.tabs.count()):
            panel.tabs.setCurrentIndex(index)
            self.app.processEvents()
            page = panel.tabs.widget(index)
            self.assertEqual(page.horizontalScrollBar().maximum(), 0, panel.tabs.tabText(index))
        panel.open_activity(row["id"])
        panel.activities_page.ensureWidgetVisible(panel.save_activity_button)
        self.app.processEvents()
        self.assertTrue(panel.save_activity_button.isVisible())

    def test_overview_navigation_cannot_discard_an_unsaved_activity(self):
        row = self.activity("Stored activity")
        panel = self.panel()
        panel.new_activity()
        panel.activity_fields["title"].setText("Unsaved new activity")
        panel.open_activity(row["id"])
        self.assertEqual(panel.activity_fields["title"].text(), "Unsaved new activity")
        self.assertIn("Enregistre ou recharge", panel.status_line.text())
        panel.reload_activity()
        self.assertEqual(panel.activity_fields["title"].text(), "")


if __name__ == "__main__":
    unittest.main()
