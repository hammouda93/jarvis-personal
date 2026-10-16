"""Bounded, typed Google REST adapter behind Jarvis' existing tool gateway.

No arbitrary HTTP, file paths, shell, discovery execution or automatic retry.
Only compiled GETs can recover from a lost response; uncertain writes stay
locked by the existing persistent MCP activity journal.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
import json
import re
import threading
import time
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from .google_workspace_catalog import BY_NAME, GOOGLE_TARGET, admitted_operations
from .google_workspace_oauth import TOKEN_URL, USERINFO_URL, account_record, token_record
from .mcp_credentials import CredentialVault
from .mcp_sdk_transport import MCPUnavailable

_LOCK = threading.RLock()
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR = GOOGLE_TARGET + "/calendar/v3"
DRIVE = GOOGLE_TARGET + "/drive/v3/files"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DOCS = "https://docs.googleapis.com/v1/documents"
FILE_FIELDS = "id,name,mimeType,modifiedTime,size,webViewLink,parents,trashed,description"


class _MailText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _message_data(message, offset=0):
    """Decode MIME bodies locally. Never fetch attachments or HTML URLs."""
    plain, html, attachments = [], [], []
    def part(item):
        body = item.get("body") or {}
        if item.get("filename"):
            attachments.append({"name": item["filename"], "mime_type": item.get("mimeType"), "size": body.get("size")})
        elif body.get("data") and item.get("mimeType") in {"text/plain", "text/html"}:
            encoded = body["data"]
            decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8", errors="replace")
            (plain if item["mimeType"] == "text/plain" else html).append(decoded)
        for child in item.get("parts", []):
            part(child)
    payload = message.get("payload") or {}
    part(payload)
    if plain:
        text = "\n".join(plain)
    else:
        parser = _MailText()
        parser.feed("\n".join(html))
        text = "".join(parser.parts)
    return {"message_id": message.get("id"), "thread_id": message.get("threadId"),
        "label_ids": message.get("labelIds", []), "snippet": message.get("snippet", ""),
        "headers": [h for h in payload.get("headers", []) if h.get("name", "").lower() in {
            "from", "to", "cc", "subject", "date", "message-id", "in-reply-to", "references"}],
        "text": text[offset:offset + 12000], "offset": offset, "total_chars": len(text),
        "next_offset": offset + 12000 if offset + 12000 < len(text) else None,
        "attachments": attachments, "attachments_downloaded": False}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_@.:-]{1,240}", value) or value in {".", ".."}:
        raise MCPUnavailable("google_invalid_identifier")
    return quote(value, safe="")


def _mail(args, *, reply=None):
    message = EmailMessage()
    for field in ("to", "cc", "bcc"):
        values = args.get(field) or []
        if field == "to" and not values or any(not re.fullmatch(r"[^\s@<>]{1,200}@[^\s@<>]{1,120}", v) for v in values):
            raise MCPUnavailable("google_invalid_recipients")
        if values:
            message[field.title()] = ", ".join(values)
    subject = args.get("subject", "")
    if reply:
        headers = {h["name"].lower(): h["value"] for h in (reply.get("payload") or {}).get("headers", [])}
        subject = subject or headers.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        if headers.get("message-id"):
            message["In-Reply-To"] = headers["message-id"]
            message["References"] = (headers.get("references", "") + " " + headers["message-id"]).strip()
    message["Subject"] = subject
    message.set_content(args["body"])
    result = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
    if reply:
        result["threadId"] = reply["threadId"]
    return result


def _event_time(value, zone):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        datetime.fromisoformat(value)
        return {"date": value}
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None and not zone:
        raise MCPUnavailable("google_event_timezone_required")
    if zone:
        ZoneInfo(zone)
    return {"dateTime": value, **({"timeZone": zone} if zone else {})}


def _event_instant(value, zone):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(zone or "UTC"))
    return parsed.astimezone(timezone.utc)


def _document_text(document):
    def content(items):
        result = []
        for item in items or []:
            for element in (item.get("paragraph") or {}).get("elements", []):
                result.append((element.get("textRun") or {}).get("content", ""))
            for row in (item.get("table") or {}).get("tableRows", []):
                result.append("\t".join(content(cell.get("content")) for cell in row.get("tableCells", [])) + "\n")
            result.append(content((item.get("tableOfContents") or {}).get("content")))
        return "".join(result)
    def tabs(items):
        result = []
        for tab in items:
            result.append(content((tab.get("documentTab") or {}).get("body", {}).get("content")))
            result.append(tabs(tab.get("childTabs", [])))
        return "".join(result)
    return tabs(document["tabs"]) if document.get("tabs") else content((document.get("body") or {}).get("content"))


class GoogleWorkspaceAPI:
    def __init__(self, *, vault=None, transport=None, clock=time.time):
        self.vault, self.transport, self.clock = vault, transport, clock

    def inventory(self, entry):
        return self.invoke(entry, "inventory")

    def call_tool(self, entry, tool, arguments):
        return self.invoke(entry, "call", tool=tool, arguments=arguments)

    def invoke(self, entry, operation, *, tool="", arguments=None):
        if entry.get("kind") != "google" or entry.get("url") != GOOGLE_TARGET:
            raise MCPUnavailable("invalid_google_api_origin")
        dispatch = {"read": False, "write": False}
        with _LOCK:
            try:
                return asyncio.run(asyncio.wait_for(self._invoke(entry, operation, tool, arguments or {}, dispatch), timeout=45))
            except Exception as exc:
                if (operation == "call" and tool in BY_NAME and BY_NAME[tool].read_only
                        and dispatch["read"] and not dispatch["write"]):
                    # These are Jarvis-owned GET implementations, not a
                    # remote tool claiming to be read-only by its title.
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    diagnostic = {"error_type": type(exc).__name__}
                    if type(status) is int:
                        diagnostic["http_status"] = status
                    return {"success": False, "outcome_unknown": False,
                        "message": "Lecture Google non aboutie" + (f" (HTTP {status})" if status else "")
                            + ". Aucun changement de données. Vérifier la connexion ou reconnecter Google.",
                        "diagnostic": diagnostic}
                if isinstance(exc, MCPUnavailable):
                    if dispatch["write"] and not exc.outcome_unknown:
                        raise MCPUnavailable("google_request_failed", outcome_unknown=True) from exc
                    raise
                raise MCPUnavailable("google_request_failed", outcome_unknown=dispatch["write"]) from exc

    async def _invoke(self, entry, operation, tool, args, dispatch):
        import httpx2
        vault = self.vault or CredentialVault(entry.get("_credential_root"))
        raw = vault.read(entry["id"], GOOGLE_TARGET, "oauth_tokens")
        if raw is None:
            raise MCPUnavailable("google_authorization_required")
        try:
            bundle = json.loads(raw)
            token, client, account = bundle["token"], bundle["client"], bundle["account"]
            if entry["id"] != "google_" + account["subject_hash"][:16]:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise MCPUnavailable("google_credentials_invalid") from None
        async with httpx2.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                timeout=httpx2.Timeout(25, connect=8)) as http:
            if token["expires_at"] <= self.clock() + 60:
                response = await http.post(TOKEN_URL, data={"client_id": client["client_id"],
                    "client_secret": client.get("client_secret", ""), "refresh_token": token["refresh_token"],
                    "grant_type": "refresh_token"})
                response.raise_for_status()
                token = token_record(response.json(), token["scopes"], refresh_token=token["refresh_token"], clock=self.clock)
                bundle["token"] = token
                vault.write(entry["id"], GOOGLE_TARGET, json.dumps(bundle), "oauth_tokens")
            operations = {op.name: op for op in admitted_operations(token["scopes"])}
            headers = {"Authorization": "Bearer " + token["access_token"]}

            async def request(method, url, *, params=None, body=None, text=False):
                if urlsplit(url).hostname not in {"www.googleapis.com", "gmail.googleapis.com", "docs.googleapis.com", "sheets.googleapis.com"}:
                    raise MCPUnavailable("invalid_google_api_origin")
                dispatch["read" if method == "GET" else "write"] = True
                async with http.stream(method, url, params=params, json=body, headers=headers) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 2_000_000:
                            raise ValueError("google_response_too_large")
                        chunks.append(chunk)
                    payload = b"".join(chunks)
                    if not payload:
                        return {}
                    return payload.decode("utf-8") if text else json.loads(payload)

            if operation in {"inventory", "discover"}:
                actual = account_record(await request("GET", USERINFO_URL))
                if actual != account:
                    raise MCPUnavailable("google_account_changed")
                inventory = {"tools": [op.definition() for op in operations.values()], "capabilities": {"tools": True}}
                return inventory["tools"] if operation == "discover" else inventory
            if operation != "call" or tool not in operations:
                raise MCPUnavailable("google_operation_not_authorized")
            from jsonschema import Draft202012Validator
            if not Draft202012Validator(operations[tool].definition()["input_schema"]).is_valid(args):
                raise MCPUnavailable("google_invalid_arguments")
            data = await self._dispatch(tool, args, request)
            encoded = json.dumps(data, ensure_ascii=False)
            if len(encoded) > 60000:
                if operations[tool].read_only:
                    return {"success": False, "outcome_unknown": False,
                        "message": "Réponse Google trop volumineuse. Réduire la plage, le nombre de résultats ou lire le document par fragments."}
                # The mutation completed, but oversized output is not a
                # license to repeat it. Keep a bounded, explicit receipt.
                return {"success": True, "data": {"output_truncated": True}, "output_truncated": True,
                        "message": "Google a répondu à l’action ; sortie trop volumineuse, ne pas rejouer l’action."}
            return {"success": True, "data": data, "message": encoded, "output_truncated": False}

    async def _dispatch(self, tool, a, req):
        page = {"maxResults": a.get("max_results", 25), **({"pageToken": a["page_token"]} if a.get("page_token") else {})}
        if tool == "get_google_account":
            return await req("GET", USERINFO_URL)
        if tool == "gmail_get_profile":
            return await req("GET", GMAIL + "/profile")
        if tool == "gmail_get_mailbox_counts":
            return {name.lower(): await req("GET", GMAIL + "/labels/" + name) for name in ("INBOX", "UNREAD")}
        if tool == "gmail_search_messages":
            return await req("GET", GMAIL + "/messages", params={**page, "q": a["query"]})
        if tool in {"gmail_get_message", "gmail_get_thread", "gmail_get_draft"}:
            resource, field = {"gmail_get_message": ("messages", "message_id"), "gmail_get_thread": ("threads", "thread_id"),
                               "gmail_get_draft": ("drafts", "draft_id")}[tool]
            data = await req("GET", GMAIL + "/" + resource + "/" + identifier(a[field]), params={"format": a.get("format", "full")})
            if tool == "gmail_get_message":
                return _message_data(data, a.get("offset", 0))
            if tool == "gmail_get_draft":
                return {"draft_id": data.get("id"), "message": _message_data(data.get("message") or {})}
            return {"thread_id": data.get("id"), "messages": [_message_data(m) for m in data.get("messages", [])]}
        if tool in {"gmail_list_labels", "gmail_list_drafts"}:
            return await req("GET", GMAIL + ("/labels" if tool == "gmail_list_labels" else "/drafts"), params={} if tool == "gmail_list_labels" else page)
        if tool in {"gmail_create_draft", "gmail_send_message", "gmail_reply_message"}:
            original = await req("GET", GMAIL + "/messages/" + identifier(a["message_id"]), params={"format": "metadata"}) if tool == "gmail_reply_message" else None
            mail = _mail(a, reply=original)
            return await req("POST", GMAIL + ("/drafts" if tool == "gmail_create_draft" else "/messages/send"), body={"message": mail} if tool == "gmail_create_draft" else mail)
        if tool == "gmail_send_draft":
            return await req("POST", GMAIL + "/drafts/send", body={"id": a["draft_id"]})
        if tool == "gmail_update_draft":
            return await req("PUT", GMAIL + "/drafts/" + identifier(a["draft_id"]), body={"message": _mail(a)})
        if tool == "gmail_modify_message_labels":
            return await req("POST", GMAIL + "/messages/" + identifier(a["message_id"]) + "/modify",
                body={"addLabelIds": a.get("add_label_ids", []), "removeLabelIds": a.get("remove_label_ids", [])})
        if tool in {"gmail_trash_message", "gmail_untrash_message"}:
            return await req("POST", GMAIL + "/messages/" + identifier(a["message_id"]) + ("/trash" if tool == "gmail_trash_message" else "/untrash"), body={})
        if tool == "list_calendars":
            return await req("GET", CALENDAR + "/users/me/calendarList", params=page)
        if "calendar_event" in tool:
            url = CALENDAR + "/calendars/" + identifier(a.get("calendar_id", "primary")) + "/events"
            if tool == "list_calendar_events":
                return await req("GET", url, params={**page, "singleEvents": "true", "orderBy": "startTime",
                    **{remote: a[local] for local, remote in (("time_min", "timeMin"), ("time_max", "timeMax"), ("query", "q")) if a.get(local)}})
            if tool == "get_calendar_event":
                return await req("GET", url + "/" + identifier(a["event_id"]))
            notify = {"sendUpdates": a.get("send_updates", "none")}
            if tool == "create_calendar_event":
                start, end = _event_time(a["start"], a.get("time_zone")), _event_time(a["end"], a.get("time_zone"))
                if set(start) != set(end) or _event_instant(a["end"], a.get("time_zone")) <= _event_instant(a["start"], a.get("time_zone")):
                    raise MCPUnavailable("google_event_dates_invalid")
                if any(not re.fullmatch(r"[^\s@<>]{1,200}@[^\s@<>]{1,120}", email) for email in a.get("attendees", [])):
                    raise MCPUnavailable("google_invalid_recipients")
                body = {"summary": a["summary"], "start": start, "end": end,
                    **{k: a[k] for k in ("location", "description") if k in a},
                    **({"attendees": [{"email": e} for e in a["attendees"]]} if a.get("attendees") else {})}
                return await req("POST", url, params=notify, body=body)
            url += "/" + identifier(a["event_id"])
            if tool == "delete_calendar_event":
                return await req("DELETE", url, params=notify)
            if tool == "update_calendar_event":
                return await req("PATCH", url, params=notify, body=a["changes"])
        if tool == "search_drive_files":
            return await req("GET", DRIVE, params={"pageSize": a.get("max_results", 25), "q": a.get("query", "trashed = false"),
                "fields": "nextPageToken,files(" + FILE_FIELDS + ")", **({"pageToken": a["page_token"]} if a.get("page_token") else {})})
        if tool == "get_drive_file":
            return await req("GET", DRIVE + "/" + identifier(a["file_id"]), params={"fields": FILE_FIELDS})
        if tool == "get_drive_export":
            return {"text": await req("GET", DRIVE + "/" + identifier(a["file_id"]) + "/export", params={"mimeType": a["mime_type"]}, text=True), "mime_type": a["mime_type"]}
        if tool == "create_drive_folder":
            return await req("POST", DRIVE, body={"name": a["name"], "mimeType": "application/vnd.google-apps.folder",
                **({"parents": [a["parent_id"]]} if a.get("parent_id") else {})})
        if tool == "copy_drive_file":
            return await req("POST", DRIVE + "/" + identifier(a["file_id"]) + "/copy", body={"name": a["name"], **({"parents": [a["parent_id"]]} if a.get("parent_id") else {})})
        if tool in {"update_drive_file", "trash_drive_file"}:
            body = {"trashed": True} if tool == "trash_drive_file" else {k: a[k] for k in ("name", "description") if k in a}
            if not body:
                raise MCPUnavailable("google_no_changes")
            return await req("PATCH", DRIVE + "/" + identifier(a["file_id"]), body=body)
        if tool == "get_spreadsheet":
            return await req("GET", SHEETS + "/" + identifier(a["spreadsheet_id"]), params={"fields": "spreadsheetId,spreadsheetUrl,properties,sheets.properties"})
        if tool == "create_spreadsheet":
            return await req("POST", SHEETS, body={"properties": {"title": a["title"]}})
        if tool in {"get_sheet_values", "update_sheet_values", "append_sheet_values", "clear_sheet_values"}:
            if a["range"] in {".", ".."} or any(c in a["range"] for c in "\r\n\x00"):
                raise MCPUnavailable("google_invalid_range")
            url = SHEETS + "/" + identifier(a["spreadsheet_id"]) + "/values/" + quote(a["range"], safe="")
            if tool == "get_sheet_values":
                return await req("GET", url)
            if tool == "clear_sheet_values":
                return await req("POST", url + ":clear", body={})
            return await req("PUT" if tool == "update_sheet_values" else "POST", url + (":append" if tool == "append_sheet_values" else ""),
                params={"valueInputOption": a.get("value_input_option", "RAW")}, body={"majorDimension": "ROWS", "values": a["values"]})
        if tool == "create_document":
            return await req("POST", DOCS, body={"title": a["title"]})
        if tool == "get_document":
            doc = await req("GET", DOCS + "/" + identifier(a["document_id"]), params={"includeTabsContent": "true"})
            offset = a.get("offset", 0)
            if offset and (not a.get("revision_id") or a["revision_id"] != doc.get("revisionId")):
                raise MCPUnavailable("google_document_changed_read_again")
            text = _document_text(doc)
            return {"document_id": a["document_id"], "title": doc.get("title"), "revision_id": doc.get("revisionId"),
                "text": text[offset:offset + 12000], "offset": offset, "total_chars": len(text),
                "next_offset": offset + 12000 if offset + 12000 < len(text) else None}
        if tool in {"append_document_text", "replace_document_text"}:
            if tool == "append_document_text":
                change = {"insertText": {"text": a["text"], "endOfSegmentLocation": {**({"tabId": a["tab_id"]} if a.get("tab_id") else {})}}}
            else:
                change = {"replaceAllText": {"containsText": {"text": a["old_text"], "matchCase": a.get("case_sensitive", True)}, "replaceText": a["new_text"]}}
            return await req("POST", DOCS + "/" + identifier(a["document_id"]) + ":batchUpdate",
                body={"requests": [change], **({"writeControl": {"requiredRevisionId": a["revision_id"]}} if a.get("revision_id") else {})})
        raise MCPUnavailable("google_operation_not_implemented")
