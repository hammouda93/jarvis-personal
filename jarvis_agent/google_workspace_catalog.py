"""First-party Google REST operations. No remote schema can add authority.

The existing MCP gateway also carries native provider adapters: allowlists,
fingerprint-pinned reads, confirmations, quotas and receipts remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

GOOGLE_TARGET = "https://www.googleapis.com"
CLIENT_SLOT = "google_workspace"
SERVICES = {"gmail": "Gmail", "google_calendar": "Calendar", "google_drive": "Drive",
            "google_sheets": "Sheets", "google_docs": "Docs"}
SCOPES = {"gmail": "https://www.googleapis.com/auth/gmail.modify",
          "google_calendar": "https://www.googleapis.com/auth/calendar",
          "google_drive": "https://www.googleapis.com/auth/drive",
          "google_sheets": "https://www.googleapis.com/auth/spreadsheets",
          "google_docs": "https://www.googleapis.com/auth/documents"}
IDENTITY_SCOPES = {"openid", "https://www.googleapis.com/auth/userinfo.email",
                   "https://www.googleapis.com/auth/userinfo.profile"}


def string(description="", *, maximum=2000, enum=None):
    result = {"type": "string", "maxLength": maximum}
    if description:
        result["description"] = description
    if enum:
        result["enum"] = enum
    return result


ID = string("Use an actual ID returned by Google, not a title or URL.", maximum=240)
PAGE = {"max_results": {"type": "integer", "minimum": 1, "maximum": 100},
        "page_token": string(maximum=4000)}
CALENDAR = string("Actual calendar ID, or primary for the connected account.", maximum=240)
BOOL = {"type": "boolean"}
EMAILS = {"type": "array", "items": string(maximum=320), "maxItems": 50}
RANGE = string("A1 range, e.g. 'Sheet 1'!A1:D20. Use actual sheet names.", maximum=500)
VALUES = {"type": "array", "maxItems": 1000, "items": {"type": "array", "maxItems": 100,
    "items": {"type": ["string", "number", "boolean", "null"]}}}
MAIL = {"to": EMAILS, "subject": string(maximum=998), "body": string(maximum=50000),
        "cc": EMAILS, "bcc": EMAILS}
EVENT = {"summary": string(), "start": string("ISO datetime with offset, or YYYY-MM-DD for all-day."),
         "end": string("ISO datetime; for all-day, exclusive end date."), "time_zone": string(maximum=100),
         "description": string(maximum=12000), "location": string(), "attendees": EMAILS}
NOTIFY = string("none unless the user asked to notify attendees.", enum=["none", "all", "externalOnly"])


@dataclass(frozen=True)
class GoogleOperation:
    name: str
    service: str
    description: str
    properties: dict
    required: tuple = ()
    read_only: bool = True

    def definition(self):
        return {"name": self.name, "description": self.description,
            "input_schema": {"type": "object", "properties": deepcopy(self.properties),
                "required": list(self.required), "additionalProperties": False}}


def operation(name, service, description, properties=None, required=(), *, write=False):
    return GoogleOperation(name, service, description, properties or {}, tuple(required), not write)


OPERATIONS = (
    operation("get_google_account", "identity", "Read the connected Google account identity. Never infer another account."),
    operation("gmail_get_profile", "gmail", "Read the actual connected Gmail profile and mailbox totals."),
    operation("gmail_get_mailbox_counts", "gmail", "Read exact unread MESSAGE and THREAD counts from INBOX and UNREAD labels. Do not estimate by paging search results."),
    operation("gmail_search_messages", "gmail", "Search Gmail. Results are IDs, not full messages. Follow nextPageToken; resultSizeEstimate is not an exact count.",
        {"query": string("Gmail search syntax, e.g. is:unread", maximum=1500), **PAGE}, ("query",)),
    operation("gmail_get_message", "gmail", "Read decoded message text without marking it read. Long bodies use offset/next_offset, not silent truncation.",
        {"message_id": ID, "format": string(enum=["full", "metadata"]), "offset": {"type": "integer", "minimum": 0}}, ("message_id",)),
    operation("gmail_get_thread", "gmail", "Read a Gmail thread without changing read status.", {"thread_id": ID}, ("thread_id",)),
    operation("gmail_list_labels", "gmail", "List real Gmail label IDs and names."),
    operation("gmail_list_drafts", "gmail", "List existing drafts, without sending them.", PAGE),
    operation("gmail_get_draft", "gmail", "Read an existing draft.", {"draft_id": ID}, ("draft_id",)),
    operation("gmail_create_draft", "gmail", "Create an unsent plain-text email draft. Confirm the exact recipients and content.", MAIL, ("to", "subject", "body"), write=True),
    operation("gmail_update_draft", "gmail", "Replace an existing draft with the reviewed recipients and plain-text content. Does not send.",
        {**MAIL, "draft_id": ID}, ("draft_id", "to", "subject", "body"), write=True),
    operation("gmail_send_message", "gmail", "Send a plain-text email NOW, after confirmation of recipients and content. Never use for a draft-only request.", MAIL, ("to", "subject", "body"), write=True),
    operation("gmail_reply_message", "gmail", "Reply to a real message in its existing thread. Reads its headers first; explicit to recipients avoid Reply-All surprises.",
        {**MAIL, "message_id": ID}, ("message_id", "to", "body"), write=True),
    operation("gmail_send_draft", "gmail", "Send a real existing draft NOW, after user review.", {"draft_id": ID}, ("draft_id",), write=True),
    operation("gmail_modify_message_labels", "gmail", "Add/remove real label IDs, including read/unread status. Changes Gmail, requires confirmation.",
        {"message_id": ID, "add_label_ids": {"type": "array", "items": ID, "maxItems": 30},
         "remove_label_ids": {"type": "array", "items": ID, "maxItems": 30}}, ("message_id",), write=True),
    operation("gmail_trash_message", "gmail", "Move a message to Trash, recoverably. No permanent deletion.", {"message_id": ID}, ("message_id",), write=True),
    operation("gmail_untrash_message", "gmail", "Restore a message from Trash.", {"message_id": ID}, ("message_id",), write=True),
    operation("list_calendars", "google_calendar", "List calendars accessible to the connected Google account.", PAGE),
    operation("list_calendar_events", "google_calendar", "Read events. Supply ISO time_min/time_max to bound dates; follow nextPageToken.",
        {"calendar_id": CALENDAR, "time_min": string(), "time_max": string(), "query": string(), **PAGE}),
    operation("get_calendar_event", "google_calendar", "Read a real calendar event.", {"calendar_id": CALENDAR, "event_id": ID}, ("event_id",)),
    operation("create_calendar_event", "google_calendar", "Create an event or meeting. Verify date, timezone and attendees; send_updates controls invitations.",
        {"calendar_id": CALENDAR, **EVENT, "send_updates": NOTIFY}, ("summary", "start", "end"), write=True),
    operation("update_calendar_event", "google_calendar", "Patch an existing event. Google date fields use dateTime/timeZone or date for all-day. Read the event first.",
        {"calendar_id": CALENDAR, "event_id": ID, "changes": {"type": "object", "minProperties": 1,
         "properties": {"summary": string(), "description": string(), "location": string(),
             "start": {"type": "object", "properties": {"dateTime": string(), "date": string(), "timeZone": string()}, "additionalProperties": False},
             "end": {"type": "object", "properties": {"dateTime": string(), "date": string(), "timeZone": string()}, "additionalProperties": False}},
         "additionalProperties": False}, "send_updates": NOTIFY}, ("event_id", "changes"), write=True),
    operation("delete_calendar_event", "google_calendar", "Delete one identified event; send_updates controls cancellation notices.",
        {"calendar_id": CALENDAR, "event_id": ID, "send_updates": NOTIFY}, ("event_id",), write=True),
    operation("search_drive_files", "google_drive", "Search Drive metadata with Google Drive q syntax, not free text. Follow nextPageToken.",
        {"query": string("Drive q expression, e.g. trashed = false and name contains 'report'", maximum=1500), **PAGE}),
    operation("get_drive_file", "google_drive", "Read file metadata, not its contents.", {"file_id": ID}, ("file_id",)),
    operation("get_drive_export", "google_drive", "Export an identified Google Workspace file as text. No local file or arbitrary URL is read.",
        {"file_id": ID, "mime_type": string(enum=["text/plain", "text/csv", "text/tab-separated-values"])}, ("file_id", "mime_type")),
    operation("create_drive_folder", "google_drive", "Create a folder in Drive.", {"name": string(), "parent_id": ID}, ("name",), write=True),
    operation("copy_drive_file", "google_drive", "Copy a real Drive file; does not overwrite its source.", {"file_id": ID, "name": string(), "parent_id": ID}, ("file_id", "name"), write=True),
    operation("update_drive_file", "google_drive", "Rename or describe a real Drive file.", {"file_id": ID, "name": string(), "description": string(maximum=10000)}, ("file_id",), write=True),
    operation("trash_drive_file", "google_drive", "Move a Drive file to Trash. No permanent deletion.", {"file_id": ID}, ("file_id",), write=True),
    operation("get_spreadsheet", "google_sheets", "Read spreadsheet metadata and real sheet titles/IDs.", {"spreadsheet_id": ID}, ("spreadsheet_id",)),
    operation("get_sheet_values", "google_sheets", "Read an actual A1 range from a spreadsheet.", {"spreadsheet_id": ID, "range": RANGE}, ("spreadsheet_id", "range")),
    operation("create_spreadsheet", "google_sheets", "Create a spreadsheet with a title.", {"title": string()}, ("title",), write=True),
    operation("update_sheet_values", "google_sheets", "Replace values in an exact A1 range. RAW is the safe default; USER_ENTERED interprets formulas.",
        {"spreadsheet_id": ID, "range": RANGE, "values": VALUES,
         "value_input_option": string(enum=["RAW", "USER_ENTERED"])}, ("spreadsheet_id", "range", "values"), write=True),
    operation("append_sheet_values", "google_sheets", "Append rows to a sheet table. Do not repeat after an uncertain outcome.",
        {"spreadsheet_id": ID, "range": RANGE, "values": VALUES,
         "value_input_option": string(enum=["RAW", "USER_ENTERED"])}, ("spreadsheet_id", "range", "values"), write=True),
    operation("clear_sheet_values", "google_sheets", "Clear cell values in an exact A1 range, preserving formatting.", {"spreadsheet_id": ID, "range": RANGE}, ("spreadsheet_id", "range"), write=True),
    operation("get_document", "google_docs", "Read document body text, including tables/tabs, in bounded chunks. With offset > 0 supply the revision_id returned by the first read.",
        {"document_id": ID, "offset": {"type": "integer", "minimum": 0}, "revision_id": string(maximum=500)}, ("document_id",)),
    operation("create_document", "google_docs", "Create a Google Docs document with a title.", {"title": string()}, ("title",), write=True),
    operation("append_document_text", "google_docs", "Append text to a real document. Optional tab_id selects a known tab.",
        {"document_id": ID, "text": string(maximum=40000), "tab_id": ID, "revision_id": string(maximum=500)}, ("document_id", "text"), write=True),
    operation("replace_document_text", "google_docs", "Replace exact text after reading the document. Requires its actual revision_id to avoid overwriting concurrent edits.",
        {"document_id": ID, "old_text": string(maximum=10000), "new_text": string(maximum=40000),
         "revision_id": string(maximum=500), "case_sensitive": BOOL}, ("document_id", "old_text", "new_text", "revision_id"), write=True),
)
BY_NAME = {op.name: op for op in OPERATIONS}


def admitted_operations(scopes):
    """Compile only granted service scopes; no name/description can grant one."""
    granted = set(scopes)
    result = []
    for op in OPERATIONS:
        if op.service == "identity":
            result.append(op)
            continue
        scope = SCOPES[op.service]
        readonly = "https://www.googleapis.com/auth/gmail.readonly" if op.service == "gmail" else scope + ".readonly"
        if scope in granted or op.read_only and readonly in granted:
            result.append(op)
    return result
