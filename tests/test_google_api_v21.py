"""Every native operation, using fake Google endpoints; no live mutations."""
import base64
from email import message_from_bytes
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx2

from jarvis_agent.google_workspace_api import GoogleWorkspaceAPI, _message_data
from jarvis_agent.google_workspace_catalog import BY_NAME, GOOGLE_TARGET, SCOPES
from jarvis_agent.mcp_activity import MCPActivityStore
from jarvis_agent.mcp_sdk_transport import MCPUnavailable, OfficialMCPTransport
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from tests.google_workspace_fixtures import CLIENT, PROFILE, GoogleFixture, bundle

MAIL = {"to": ["recipient@example.test"], "subject": "Bonjour", "body": "Texte UTF-8 é"}
RANGE = {"spreadsheet_id": "sheet123", "range": "'Suivi 1'!A1:B3"}
EVENT = {"summary": "Réunion", "start": "2026-10-17T10:00:00+02:00", "end": "2026-10-17T11:00:00+02:00"}
CASES = {
    "get_google_account": ({}, "GET", "/oauth2/v2/userinfo"),
    "gmail_get_profile": ({}, "GET", "/gmail/v1/users/me/profile"),
    "gmail_get_mailbox_counts": ({}, "GET", "/gmail/v1/users/me/labels/UNREAD"),
    "gmail_search_messages": ({"query": "is:unread"}, "GET", "/gmail/v1/users/me/messages"),
    "gmail_get_message": ({"message_id": "message123"}, "GET", "/gmail/v1/users/me/messages/message123"),
    "gmail_get_thread": ({"thread_id": "thread123"}, "GET", "/gmail/v1/users/me/threads/thread123"),
    "gmail_list_labels": ({}, "GET", "/gmail/v1/users/me/labels"),
    "gmail_list_drafts": ({}, "GET", "/gmail/v1/users/me/drafts"),
    "gmail_get_draft": ({"draft_id": "draft123"}, "GET", "/gmail/v1/users/me/drafts/draft123"),
    "gmail_create_draft": (MAIL, "POST", "/gmail/v1/users/me/drafts"),
    "gmail_update_draft": ({**MAIL, "draft_id": "draft123"}, "PUT", "/gmail/v1/users/me/drafts/draft123"),
    "gmail_send_message": (MAIL, "POST", "/gmail/v1/users/me/messages/send"),
    "gmail_reply_message": ({**MAIL, "message_id": "message123"}, "POST", "/gmail/v1/users/me/messages/send"),
    "gmail_send_draft": ({"draft_id": "draft123"}, "POST", "/gmail/v1/users/me/drafts/send"),
    "gmail_modify_message_labels": ({"message_id": "message123", "remove_label_ids": ["UNREAD"]}, "POST", "/gmail/v1/users/me/messages/message123/modify"),
    "gmail_trash_message": ({"message_id": "message123"}, "POST", "/gmail/v1/users/me/messages/message123/trash"),
    "gmail_untrash_message": ({"message_id": "message123"}, "POST", "/gmail/v1/users/me/messages/message123/untrash"),
    "list_calendars": ({}, "GET", "/calendar/v3/users/me/calendarList"),
    "list_calendar_events": ({"time_min": "2026-10-17T00:00:00Z"}, "GET", "/calendar/v3/calendars/primary/events"),
    "get_calendar_event": ({"event_id": "event123"}, "GET", "/calendar/v3/calendars/primary/events/event123"),
    "create_calendar_event": (EVENT, "POST", "/calendar/v3/calendars/primary/events"),
    "update_calendar_event": ({"event_id": "event123", "changes": {"summary": "Modifié"}}, "PATCH", "/calendar/v3/calendars/primary/events/event123"),
    "delete_calendar_event": ({"event_id": "event123"}, "DELETE", "/calendar/v3/calendars/primary/events/event123"),
    "search_drive_files": ({"query": "trashed = false"}, "GET", "/drive/v3/files"),
    "get_drive_file": ({"file_id": "file123"}, "GET", "/drive/v3/files/file123"),
    "get_drive_export": ({"file_id": "file123", "mime_type": "text/plain"}, "GET", "/drive/v3/files/file123/export"),
    "create_drive_folder": ({"name": "Dossier"}, "POST", "/drive/v3/files"),
    "copy_drive_file": ({"file_id": "file123", "name": "Copie"}, "POST", "/drive/v3/files/file123/copy"),
    "update_drive_file": ({"file_id": "file123", "name": "Renommé"}, "PATCH", "/drive/v3/files/file123"),
    "trash_drive_file": ({"file_id": "file123"}, "PATCH", "/drive/v3/files/file123"),
    "get_spreadsheet": ({"spreadsheet_id": "sheet123"}, "GET", "/v4/spreadsheets/sheet123"),
    "get_sheet_values": (RANGE, "GET", "/v4/spreadsheets/sheet123/values/'Suivi 1'!A1:B3"),
    "create_spreadsheet": ({"title": "Suivi"}, "POST", "/v4/spreadsheets"),
    "update_sheet_values": ({**RANGE, "values": [["=1+1", 2]]}, "PUT", "/v4/spreadsheets/sheet123/values/'Suivi 1'!A1:B3"),
    "append_sheet_values": ({**RANGE, "values": [["a", 2]]}, "POST", "/v4/spreadsheets/sheet123/values/'Suivi 1'!A1:B3:append"),
    "clear_sheet_values": (RANGE, "POST", "/v4/spreadsheets/sheet123/values/'Suivi 1'!A1:B3:clear"),
    "get_document": ({"document_id": "doc123"}, "GET", "/v1/documents/doc123"),
    "create_document": ({"title": "Suivi"}, "POST", "/v1/documents"),
    "append_document_text": ({"document_id": "doc123", "text": "Ajout"}, "POST", "/v1/documents/doc123:batchUpdate"),
    "replace_document_text": ({"document_id": "doc123", "old_text": "A", "new_text": "B", "revision_id": "rev123"}, "POST", "/v1/documents/doc123:batchUpdate"),
}


class GoogleAPITests(GoogleFixture):
    def setUp(self):
        super().setUp()
        self.entry = self.seed_account()
        self.requests = []
        self.response = None
        self.api = GoogleWorkspaceAPI(vault=self.vault, transport=httpx2.MockTransport(self.handler))

    def handler(self, request):
        self.requests.append(request)
        if self.response:
            return self.response(request)
        if request.url.path.endswith("/export"):
            return httpx2.Response(200, text="Document texte")
        if request.url.path.endswith("/userinfo"):
            return httpx2.Response(200, json=PROFILE)
        return httpx2.Response(200, json={"id": "message123", "threadId": "thread123", "revisionId": "rev123",
            "payload": {"headers": [{"name": "Message-ID", "value": "<original@example.test>"}],
                "mimeType": "text/plain", "body": {"data": "Qm9uam91cg"}}})

    def call(self, name, arguments=None):
        return self.api.call_tool(self.entry, name, arguments or {})

    def test_every_catalog_operation_has_an_actual_bounded_google_route(self):
        self.assertEqual(set(CASES), set(BY_NAME))
        for name, (args, method, path) in CASES.items():
            with self.subTest(tool=name):
                self.requests.clear()
                result = self.call(name, args)
                self.assertTrue(result["success"])
                self.assertEqual(self.requests[-1].method, method)
                self.assertEqual(self.requests[-1].url.path, path)
                self.assertTrue(all(r.url.scheme == "https" and r.url.host.endswith("googleapis.com") for r in self.requests))
                if BY_NAME[name].read_only:
                    self.assertTrue(all(r.method == "GET" for r in self.requests))

    def test_send_is_real_rfc_email_and_draft_does_not_send(self):
        self.call("gmail_create_draft", MAIL)
        payload = json.loads(self.requests[-1].content)["message"]
        decoded = message_from_bytes(base64.urlsafe_b64decode(payload["raw"]))
        self.assertEqual(decoded["To"], MAIL["to"][0])
        self.assertEqual(decoded["Subject"], MAIL["subject"])
        self.assertTrue(self.requests[-1].url.path.endswith("/drafts"))
        self.call("gmail_reply_message", {**MAIL, "message_id": "message123"})
        payload = json.loads(self.requests[-1].content)
        decoded = message_from_bytes(base64.urlsafe_b64decode(payload["raw"]))
        self.assertEqual(decoded["In-Reply-To"], "<original@example.test>")
        self.assertEqual(payload["threadId"], "thread123")

    def test_mail_decodes_html_without_loading_urls_and_paginates_text(self):
        html = base64.urlsafe_b64encode(b'<p>Hello &amp; world</p><script>PRIVATE_SCRIPT</script><img src="https://evil.test">').decode()
        message = {"id": "m", "payload": {"mimeType": "text/html", "body": {"data": html}}}
        result = _message_data(message)
        self.assertIn("Hello & world", result["text"])
        self.assertNotIn("PRIVATE_SCRIPT", result["text"])
        message["payload"] = {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"a" * 13000).decode()}}
        first, second = _message_data(message), _message_data(message, 12000)
        self.assertEqual(first["next_offset"], 12000)
        self.assertEqual(len(second["text"]), 1000)
        self.assertIsNone(second["next_offset"])

    def test_sheet_writes_default_to_raw_and_docs_replacement_requires_revision(self):
        self.call("update_sheet_values", {**RANGE, "values": [["=IMPORTXML(\"https://evil.test\")"]]})
        self.assertEqual(self.requests[-1].url.params["valueInputOption"], "RAW")
        with self.assertRaises(MCPUnavailable):
            self.call("replace_document_text", {"document_id": "doc123", "old_text": "a", "new_text": "b"})
        self.call("replace_document_text", CASES["replace_document_text"][0])
        self.assertEqual(json.loads(self.requests[-1].content)["writeControl"], {"requiredRevisionId": "rev123"})

    def test_calendar_offsets_are_compared_as_instants_and_notifications_default_off(self):
        # 10:00+02 -> 09:30Z is 90 minutes, despite the lexical end < start.
        result = self.call("create_calendar_event", {**EVENT, "end": "2026-10-17T09:30:00Z"})
        self.assertTrue(result["success"])
        self.assertEqual(self.requests[-1].url.params["sendUpdates"], "none")
        self.call("create_calendar_event", {**EVENT, "start": "2026-10-17", "end": "2026-10-18"})
        self.assertEqual(json.loads(self.requests[-1].content)["end"], {"date": "2026-10-18"})
        for changes in ({"end": "2026-10-17T07:00:00Z"}, {"start": "2026-10-17T10:00:00", "end": "2026-10-17T11:00:00"},
                {"start": "2026-10-17", "end": "2026-10-17"}):
            before = len(self.requests)
            with self.assertRaises(MCPUnavailable):
                self.call("create_calendar_event", {**EVENT, **changes})
            self.assertEqual(len(self.requests), before)

    def test_schema_path_and_email_header_injections_never_dispatch(self):
        for name, args in (("gmail_get_message", {"message_id": "../profile"}),
                ("gmail_get_message", {"message_id": "https://evil.test/a"}),
                ("gmail_get_profile", {"url": "https://evil.test"}),
                ("gmail_send_message", {**MAIL, "subject": "x\nBcc: evil@example.test"}),
                ("gmail_send_message", {**MAIL, "to": ["bad\n@example.test"]}),
                ("get_sheet_values", {**RANGE, "range": ".."})):
            with self.subTest(tool=name), self.assertRaises(MCPUnavailable):
                self.call(name, args)
        self.assertEqual(self.requests, [])

    def test_no_arbitrary_tool_or_origin_and_scope_revocation_is_effective(self):
        for name in ("raw_http", "gmail_delete_everything"):
            with self.assertRaises(MCPUnavailable):
                self.call(name)
        with self.assertRaises(MCPUnavailable):
            self.api.call_tool({**self.entry, "url": "https://evil.test"}, "gmail_get_profile", {})
        data = bundle(scopes=[SCOPES["gmail"]])
        self.vault.write(self.entry["id"], GOOGLE_TARGET, json.dumps(data), "oauth_tokens")
        with self.assertRaises(MCPUnavailable):
            self.call("get_document", {"document_id": "doc123"})
        self.assertEqual(self.requests, [])

    def test_refresh_is_absolute_persisted_and_never_opens_browser(self):
        data = bundle(expires=100)
        self.vault.write(self.entry["id"], GOOGLE_TARGET, json.dumps(data), "oauth_tokens")
        self.api.clock = lambda: 200
        def handler(request):
            if request.url.host == "oauth2.googleapis.com":
                self.assertIn(b"refresh_token=PRIVATE_REFRESH_FIXTURE", request.content)
                return httpx2.Response(200, json={"access_token": "NEW_FIXTURE", "expires_in": 3600})
            self.assertEqual(request.headers["Authorization"], "Bearer NEW_FIXTURE")
            return httpx2.Response(200, json={"emailAddress": PROFILE["email"]})
        self.response = handler
        with patch("webbrowser.open") as browser:
            self.call("gmail_get_profile")
        browser.assert_not_called()
        stored = json.loads(self.vault.read(self.entry["id"], GOOGLE_TARGET, "oauth_tokens"))
        self.assertEqual(stored["token"]["expires_at"], 3800)
        self.assertEqual(stored["token"]["refresh_token"], "PRIVATE_REFRESH_FIXTURE")

    def test_known_read_timeout_is_recoverable_but_unknown_write_is_locked_no_retry(self):
        def timeout(request):
            raise httpx2.ReadTimeout("PRIVATE_TOKEN_REMOTE_BODY", request=request)
        self.response = timeout
        native = SimpleNamespace(ollama_tools=lambda: [])
        gateway = MCPToolRegistry(native, registry=self.registry, transport=self.api)
        prefix = "mcp__" + self.entry["id"] + "__"
        failed_read = gateway.execute(prefix + "gmail_get_profile", {})
        detail = json.loads(failed_read.detail)
        self.assertFalse(detail["outcome_unknown"])
        self.assertTrue(detail["safe_read_failure"])
        self.assertNotIn("PRIVATE_TOKEN_REMOTE_BODY", failed_read.detail)
        self.assertEqual(self.registry.activity().summary(self.entry["id"])["unresolved"], [])
        # Separate clock advances only the ordinary cooldown, not permissions.
        store = MCPActivityStore(self.registry.activity().path, clock=lambda: 100000000000)
        with patch.object(self.registry, "activity", return_value=store):
            no_approval = gateway.execute(prefix + "gmail_send_message", MAIL)
            self.assertFalse(json.loads(no_approval.detail)["dispatched"])
            failed_write = gateway.execute(prefix + "gmail_send_message", MAIL, approved=True)
            self.assertTrue(json.loads(failed_write.detail)["outcome_unknown"])
            count = len(self.requests)
            blocked = gateway.execute(prefix + "gmail_send_message", MAIL, approved=True)
            self.assertEqual(json.loads(blocked.detail)["guard"], "mcp_unknown_requires_review")
            self.assertEqual(len(self.requests), count)

    def test_api_error_read_known_failure_and_remote_body_not_leaked(self):
        self.response = lambda _: httpx2.Response(403, json={"error": {"message": "PRIVATE_ACCESS_FIXTURE"}})
        result = self.call("gmail_get_profile")
        self.assertFalse(result["success"])
        self.assertFalse(result["outcome_unknown"])
        self.assertEqual(result["diagnostic"]["http_status"], 403)
        self.assertNotIn("PRIVATE_ACCESS_FIXTURE", json.dumps(result))

    def test_docs_tabs_tables_chunks_and_revision_change(self):
        text = "long " * 3000
        document = {"revisionId": "rev123", "tabs": [{"documentTab": {"body": {"content": [
            {"paragraph": {"elements": [{"textRun": {"content": text}}]}},
            {"table": {"tableRows": [{"tableCells": [{"content": [{"paragraph": {"elements": [{"textRun": {"content": "cell"}}]}}]}]}]}}]}}}]}
        self.response = lambda _: httpx2.Response(200, json=document)
        first = self.call("get_document", {"document_id": "doc123"})["data"]
        self.assertEqual(first["next_offset"], 12000)
        second = self.call("get_document", {"document_id": "doc123", "offset": 12000, "revision_id": "rev123"})["data"]
        self.assertIn("cell", second["text"])
        self.assertIsNone(second["next_offset"])
        changed = self.call("get_document", {"document_id": "doc123", "offset": 12000, "revision_id": "old"})
        self.assertFalse(changed["success"])
        self.assertFalse(changed["outcome_unknown"])

    def test_official_transport_routes_google_to_adapter_without_mcp_session(self):
        with patch("jarvis_agent.google_workspace_api.GoogleWorkspaceAPI", return_value=self.api), patch.object(OfficialMCPTransport, "_with_session", side_effect=AssertionError("not MCP")):
            self.assertTrue(OfficialMCPTransport().call_tool(self.entry, "gmail_get_profile", {})["success"])
