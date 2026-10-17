"""Transactional native onboarding, not a fake live Gmail acceptance test."""
import copy
import json
import threading
from unittest.mock import patch

import httpx2

from jarvis_agent.google_workspace_api import GoogleWorkspaceAPI
from jarvis_agent.google_workspace_catalog import CLIENT_SLOT, GOOGLE_TARGET, SCOPES, SERVICES, admitted_operations
from jarvis_agent.google_workspace_connection import perform_google_command, snapshot
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.mcp_credentials import CredentialUnavailable
from jarvis_agent.mcp_read_policy import is_conservative_read_tool
from tests.google_workspace_fixtures import CLIENT, PROFILE, GoogleFixture, bundle


class OAuthFixture:
    def __init__(self, data=None, during=None):
        self.data = data or bundle()
        self.during, self.calls = during, 0
    def authorize(self, *args, **kwargs):
        self.calls += 1
        if self.during:
            self.during()
        return copy.deepcopy(self.data)


class GoogleConnectionTests(GoogleFixture):
    def command(self, operation, server=CLIENT_SLOT, value="", **kwargs):
        return perform_google_command(self.registry, operation, server, value, vault=self.vault, workspace=self.workspace, **kwargs)

    def connect(self, **kwargs):
        self.seed_client()
        return self.command("google_connect", value=self.packet(), oauth=kwargs.pop("oauth", OAuthFixture()), **kwargs)

    def test_snapshot_is_passive_without_creating_files_or_using_vault(self):
        state = snapshot(self.registry, self.workspace)
        self.assertFalse(state["client_configured"])
        self.assertFalse(self.registry.path.exists())
        self.assertFalse(self.workspace.path.exists())
        self.assertEqual(set(state["binding_revisions"]), set(SERVICES))

    def test_import_protects_client_and_public_config_has_no_secret(self):
        path = self.root / "desktop-client.json"
        path.write_text(json.dumps({"installed": CLIENT}), encoding="utf-8")
        result = self.command("google_import_client", value=json.dumps({"path": str(path)}))
        self.assertTrue(result["success"])
        self.assertIn(CLIENT["client_secret"], self.vault.read(CLIENT_SLOT, GOOGLE_TARGET, "oauth_client"))
        public = self.registry.path.read_text() + json.dumps(result) + json.dumps(snapshot(self.registry, self.workspace))
        self.assertNotIn(CLIENT["client_secret"], public)
        self.assertNotIn(CLIENT["client_id"], public)
        self.assertFalse(self.registry.runtime_enabled())
        self.assertEqual(self.registry.list_servers(), [])

    def test_unavailable_os_vault_cannot_fall_back_to_plaintext(self):
        path = self.root / "desktop-client.json"
        path.write_text(json.dumps({"installed": CLIENT}), encoding="utf-8")
        with patch.object(self.vault, "write", side_effect=CredentialUnavailable("fixture")), self.assertRaises(CredentialUnavailable):
            self.command("google_import_client", value=json.dumps({"path": str(path)}))
        self.assertFalse(self.registry.path.exists())

    def test_connect_activates_five_services_reads_pinned_writes_confirmed(self):
        result = self.connect()
        sid = result["server_id"]
        self.assertTrue(result["success"])
        self.assertTrue(self.registry.runtime_enabled())
        self.assertEqual(self.registry.get_server(sid)["kind"], "google")
        self.assertEqual({b["server"] for b in self.workspace.bindings().values()}, {sid})
        for op in admitted_operations(bundle()["token"]["scopes"]):
            with self.subTest(tool=op.name):
                self.assertEqual(is_conservative_read_tool(op.name, op.definition()["input_schema"]), op.read_only)
                self.assertTrue(self.registry.is_allowed(sid, op.name))
                self.assertEqual(self.registry.is_auto_read_allowed(sid, op.name), op.read_only)
        public = self.registry.path.read_text() + json.dumps(self.registry.list_servers()) + json.dumps(result)
        for secret in ("PRIVATE_ACCESS_FIXTURE", "PRIVATE_REFRESH_FIXTURE", "PRIVATE_CLIENT_FIXTURE"):
            self.assertNotIn(secret, public)

    def test_partial_scope_grants_never_invent_other_services_or_writes(self):
        result = self.connect(oauth=OAuthFixture(bundle(scopes=["https://www.googleapis.com/auth/gmail.readonly"])))
        entry = self.registry.get_server(result["server_id"])
        self.assertEqual(entry["google_services"], ["gmail"])
        self.assertIn("gmail_get_profile", entry["tools"])
        self.assertNotIn("gmail_send_message", entry["tools"])
        self.assertNotIn("get_document", entry["tools"])
        self.assertEqual(set(self.workspace.bindings()), {"gmail"})

    def test_cancel_preserves_existing_connector_and_never_activates_new_account(self):
        self.registry.add_http("old", "https://example.test/mcp")
        self.registry.set_enabled("old", True)
        self.workspace.bind_service("gmail", "old", expected_revision=0)
        stop = threading.Event()
        with self.assertRaisesRegex(ValueError, "oauth_cancelled"):
            self.connect(oauth=OAuthFixture(during=stop.set), stop_event=stop)
        self.assertTrue(self.registry.get_server("old")["enabled"])
        self.assertEqual(self.workspace.bindings()["gmail"]["server"], "old")
        self.assertEqual(len(self.registry.list_servers()), 1)

    def test_binding_changed_during_browser_fails_closed(self):
        change = lambda: self.workspace.bind_service("gmail", "other", expected_revision=0)
        with self.assertRaisesRegex(ValueError, "google_bindings_changed"):
            self.connect(oauth=OAuthFixture(during=change))
        self.assertEqual(self.workspace.bindings()["gmail"]["server"], "other")
        self.assertFalse(self.registry.runtime_enabled())

    def test_client_changed_during_browser_preserves_existing_bindings(self):
        change = lambda: self.registry.note_google_client(project_id="other", fingerprint="f" * 64)
        with self.assertRaisesRegex(ValueError, "google_client_changed"):
            self.connect(oauth=OAuthFixture(during=change))
        self.assertEqual(self.workspace.bindings(), {})

    def test_failed_activation_restores_bindings_and_removes_staged_tokens(self):
        self.registry.add_http("old", "https://example.test/mcp")
        self.registry.set_enabled("old", True)
        self.workspace.bind_service("gmail", "old", expected_revision=0)
        with patch.object(self.registry, "activate_google_account", side_effect=OSError("fixture")), self.assertRaises(OSError):
            self.connect()
        self.assertEqual(self.workspace.bindings()["gmail"]["server"], "old")
        self.assertTrue(self.registry.get_server("old")["enabled"])
        sid = "google_" + bundle()["account"]["subject_hash"][:16]
        self.assertFalse(self.registry.get_server(sid)["enabled"])
        self.assertIsNone(self.vault.read(sid, GOOGLE_TARGET, "oauth_tokens"))

    def test_superseded_google_only_provider_paused_but_unrelated_shared_provider_retained(self):
        for shared in (False, True):
            with self.subTest(shared=shared):
                name = "shared" if shared else "old"
                self.registry.add_http(name, "https://example.test/mcp")
                self.registry.set_enabled(name, True)
                revision = (self.workspace.bindings().get("gmail") or {}).get("revision", 0)
                self.workspace.bind_service("gmail", name, expected_revision=revision)
                if shared:
                    self.workspace.bind_service("github", name, expected_revision=0)
                self.connect()
                self.assertEqual(self.registry.get_server(name)["enabled"], shared)

    def test_different_accounts_have_distinct_journals_and_uncertain_calls_not_reassigned(self):
        first = self.connect()["server_id"]
        activity = self.registry.activity()
        attempt = activity.reserve(first, "call", self.registry.get_server(first), tool="gmail_send_message")
        activity.finish(attempt, "unknown")
        second = self.connect(oauth=OAuthFixture(bundle(profile={**PROFILE, "id": "second", "email": "second@example.test"})))["server_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(len(activity.summary(first)["unresolved"]), 1)
        self.assertEqual(activity.summary(second)["unresolved"], [])
        self.assertFalse(self.registry.get_server(first)["enabled"])

    def test_disconnect_removes_account_tokens_but_keeps_application_setup_and_files(self):
        result = self.connect()
        sid = result["server_id"]
        self.command("google_disconnect", sid)
        self.assertFalse(self.registry.get_server(sid)["enabled"])
        self.assertIsNone(self.vault.read(sid, GOOGLE_TARGET, "oauth_tokens"))
        self.assertIsNotNone(self.vault.read(CLIENT_SLOT, GOOGLE_TARGET, "oauth_client"))
        self.assertTrue(self.registry.google_client_metadata()["configured"])

    def test_direct_gmail_test_uses_only_three_gets_and_exact_label_counts(self):
        result = self.connect()
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/profile"):
                return httpx2.Response(200, json={"emailAddress": PROFILE["email"]})
            return httpx2.Response(200, json={"messagesUnread": 7, "messagesTotal": 22 if request.url.path.endswith("UNREAD") else 99})
        api = GoogleWorkspaceAPI(vault=self.vault, transport=httpx2.MockTransport(handler))
        result = self.command("google_test", result["server_id"], transport=api)
        self.assertTrue(result["success"])
        self.assertEqual(result["unread_messages"], 22)
        self.assertEqual(result["inbox_unread_messages"], 7)
        self.assertEqual([r.method for r in requests], ["GET", "GET", "GET"])

    def test_disabled_runtime_never_dispatches_direct_test(self):
        result = self.connect()
        self.registry.set_runtime_enabled(False)
        with patch.object(GoogleWorkspaceAPI, "call_tool", side_effect=AssertionError("no call")):
            result = self.command("google_test", result["server_id"], transport=GoogleWorkspaceAPI())
        self.assertFalse(result["success"])

    def test_inbox_requires_leased_options_and_cancels_same_authorization_slot(self):
        self.seed_client()
        inbox = MCPControlInbox()
        self.assertFalse(inbox.submit("google_connect", "wrong", self.packet()))
        self.assertFalse(inbox.submit("google_connect", CLIENT_SLOT, "{}"))
        self.assertTrue(inbox.submit("google_connect", CLIENT_SLOT, self.packet()))
        self.assertFalse(inbox.submit("discover", "other"))
        self.assertFalse(inbox.submit("cancel_oauth", "wrong"))
        self.assertTrue(inbox.submit("cancel_oauth", CLIENT_SLOT))
        self.assertTrue(inbox.stop_requested.is_set())
        inbox.finish_authorization()
        self.assertTrue(inbox.submit("google_test", "google_" + "a" * 16))

    def test_multi_binding_cas_is_atomic(self):
        self.workspace.bind_service("google_drive", "other", expected_revision=0)
        with self.assertRaises(ValueError):
            self.workspace.bind_services(SERVICES, "google_fixture", expected_revisions={s: 0 for s in SERVICES})
        self.assertEqual(set(self.workspace.bindings()), {"google_drive"})
