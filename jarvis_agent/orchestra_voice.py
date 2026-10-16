"""Owner-only voice channel over explicitly configured MCP tools.

The connector supplies telephony/STT/TTS. Jarvis implements the bounded task
handoff, report reply and call dispatch. No phone session is simulated here.
"""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time

from .mcp_tool_registry import _alias
from .native_tools import AgentActionResult
from .orchestra_store import OrchestraStore, VOICE_DEFAULTS, WATCH_DEFAULTS, instant
from .personal_workspace import PersonalWorkspaceStore, _text, _zone


def _failure(reason):
    return AgentActionResult("orchestra_call_owner", False, "Le canal d’appel n’a pas envoyé cette demande.",
        json.dumps({"guard": reason, "dispatched": False, "outcome_unknown": False, "verified": False}))


class VoiceChannel:
    def __init__(self, gateway, *, store=None, workspace=None):
        self.gateway = gateway
        self.store = store or OrchestraStore()
        self.workspace = workspace or PersonalWorkspaceStore()

    def fingerprint(self, config):
        entry = self.gateway.registry.get_server(config["server"])
        if not entry:
            return ""
        tools = entry.get("tools") or {}
        selected = [config[k] for k in ("dial_tool", "inbox_tool", "reply_tool") if config[k]]
        if not selected or any(name not in tools for name in selected):
            return ""
        record = {"server": config["server"], "connection": {k: entry.get(k) for k in (
            "kind", "url", "command", "args", "env_refs", "trusted_stdio", "credential_source")},
            "tools": {name: tools[name].get("fingerprint") for name in selected},
            "mapping": {k: config[k] for k in ("contact_id", "contact_field", "message_field", "dial_tool", "inbox_tool", "reply_tool")}}
        return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()

    def validate(self, value):
        if not isinstance(value, dict) or set(value) != set(VOICE_DEFAULTS):
            raise ValueError("orchestra_invalid_voice")
        clean = dict(value)
        for key in ("enabled", "automatic_calls", "automatic_replies", "allow_night_calls"):
            if type(clean[key]) is not bool:
                raise ValueError("orchestra_invalid_voice_flag")
        for key in ("server", "contact_id", "dial_tool", "inbox_tool", "reply_tool", "contact_field", "message_field", "fingerprint"):
            clean[key] = _text(clean[key], key, 180)
        if type(clean["max_calls_day"]) is not int or not 1 <= clean["max_calls_day"] <= 12:
            raise ValueError("orchestra_invalid_call_quota")
        clean["fingerprint"] = ""
        if not clean["enabled"]:
            return clean
        if not clean["contact_id"] or not clean["server"] or not (clean["dial_tool"] or clean["inbox_tool"]):
            raise ValueError("orchestra_voice_contact_and_tools_required")
        entry = self.gateway.registry.get_server(clean["server"])
        if not entry:
            raise ValueError("orchestra_voice_server_not_found")
        for key in ("dial_tool", "inbox_tool", "reply_tool"):
            name = clean[key]
            if not name:
                continue
            record = (entry.get("tools") or {}).get(name)
            if not record:
                raise ValueError("orchestra_voice_tool_not_discovered")
            schema = record.get("input_schema") or {}
            allowed = {clean["contact_field"], clean["message_field"], "idempotency_key"} if key == "dial_tool" else set() if key == "inbox_tool" else {"event_id", "message", "idempotency_key"}
            if schema.get("type", "object") != "object" or set(schema.get("required") or []) - allowed:
                raise ValueError("orchestra_voice_schema_needs_adapter")
            properties = schema.get("properties") or {}
            needed = {clean["contact_field"], clean["message_field"]} if key == "dial_tool" else {"event_id", "message"} if key == "reply_tool" else set()
            if needed - set(properties) or any(properties[k].get("type") != "string" for k in needed):
                raise ValueError("orchestra_voice_schema_needs_adapter")
            if "cursor" in properties and key == "inbox_tool" and properties["cursor"].get("type") != "string":
                raise ValueError("orchestra_voice_schema_needs_adapter")
        if clean["contact_field"] == clean["message_field"]:
            raise ValueError("orchestra_voice_distinct_fields_required")
        if clean["automatic_calls"] and not clean["dial_tool"] or clean["automatic_replies"] and not clean["reply_tool"]:
            raise ValueError("orchestra_voice_tool_required")
        clean["fingerprint"] = self.fingerprint(clean)
        if not clean["fingerprint"]:
            raise ValueError("orchestra_voice_binding_unavailable")
        return clean

    def save(self, data, *, expected_revision):
        return self.store.save_config("voice", self.validate(data), expected_revision=expected_revision)

    def readiness(self):
        config = self.store.config("voice", VOICE_DEFAULTS)["data"]
        state = "not_configured"
        available = self.gateway._available()
        if config["enabled"]:
            if not self.gateway.registry.runtime_enabled():
                state = "runtime_off"
            elif not config["fingerprint"] or config["fingerprint"] != self.fingerprint(config):
                state = "binding_changed"
            else:
                state = "ready"
                for key in ("dial_tool", "inbox_tool", "reply_tool"):
                    if config[key] and _alias(config["server"], config[key]) not in available:
                        state = "tools_not_allowed"
                if state == "ready" and config["inbox_tool"] and not self.gateway._automatic_read(_alias(config["server"], config["inbox_tool"])):
                    state = "inbox_read_consent_required"
        return {"state": state, "contact_configured": bool(config["contact_id"]),
                "automatic_calls": config["automatic_calls"], "automatic_replies": config["automatic_replies"],
                "phone_session_verified": False, "provider_required": True}

    def _night(self):
        zone = self.workspace.profile()["data"]["timezone"]
        hour = datetime.now(timezone.utc).astimezone(_zone(zone)).hour
        return hour >= 22 or hour < 7

    def automatic_calls_allowed(self):
        config = self.store.config("voice", VOICE_DEFAULTS)["data"]
        return bool(config["automatic_calls"] and self.readiness()["state"] == "ready"
                    and (config["allow_night_calls"] or not self._night()))

    def _dispatch(self, config, tool, arguments, *, key, message, maximum, kind="call"):
        try:
            record, fresh = self.store.reserve_call(key, message, maximum=maximum, kind=kind)
        except ValueError as exc:
            return _failure(str(exc))
        if not fresh:
            receipt = record.get("receipt") or {"dispatched": True, "outcome_unknown": True, "verified": False}
            return AgentActionResult("orchestra_call_owner", record["state"] == "submitted", "Demande déjà enregistrée ; aucun nouvel appel.", json.dumps(receipt))
        # The operator's saved policy authorises this exact owner binding, not
        # arbitrary MCP mutations. The original gateway still checks grants,
        # schemas, credentials, quotas and previous uncertain attempts.
        try:
            result = self.gateway.execute(_alias(config["server"], tool), arguments, approved=True)
            detail = json.loads(result.detail or "{}")
            if not isinstance(detail, dict):
                raise ValueError("invalid_voice_receipt")
        except Exception as exc:
            detail = {"dispatched": True, "outcome_unknown": True, "verified": False,
                      "reason": type(exc).__name__}
            result = AgentActionResult("orchestra_call_owner", False, "Envoi vocal incertain ; vérifier le journal du service avant toute nouvelle demande.", "")
        detail.update(scope="owner_voice_channel", phone_answered_verified=False)
        state = "unknown" if detail.get("outcome_unknown") else "submitted" if result.success else "failed"
        try:
            self.store.finish_call(record["id"], state, detail)
        except (OSError, sqlite3.Error, ValueError):
            # The pre-dispatch reservation survives. Its next read blocks replay.
            detail.update(dispatched=True, outcome_unknown=True, verified=False, receipt_persisted=False)
            result = AgentActionResult("orchestra_call_owner", False, "Reçu vocal non enregistré ; aucun nouvel envoi automatique.", "")
        return AgentActionResult("orchestra_call_owner", bool(result.success and not detail.get("outcome_unknown")),
            "Demande transmise au service vocal ; réponse du téléphone non certifiée." if result.success else result.message,
            json.dumps(detail, ensure_ascii=False))

    def call_owner(self, message, *, request_key, approved=False, automatic=False):
        message = _text(message, "message", 1800, required=True)
        config = self.store.config("voice", VOICE_DEFAULTS)["data"]
        if self.readiness()["state"] != "ready" or not config["dial_tool"]:
            return _failure("orchestra_voice_not_ready")
        if not approved and not self.automatic_calls_allowed() or automatic and not self.automatic_calls_allowed():
            return _failure("orchestra_owner_call_consent_required")
        arguments = {config["contact_field"]: config["contact_id"], config["message_field"]: message}
        entry = self.gateway.registry.get_server(config["server"])
        if "idempotency_key" in entry["tools"][config["dial_tool"]]["input_schema"].get("properties", {}):
            arguments["idempotency_key"] = request_key
        return self._dispatch(config, config["dial_tool"], arguments, key=request_key, message=message, maximum=config["max_calls_day"])

    @staticmethod
    def _envelope(detail):
        value = detail.get("mcp_structured")
        if not isinstance(value, dict) or value.get("truncated"):
            try:
                value = json.loads(detail.get("mcp_output") or "{}")
            except ValueError:
                return None
        if isinstance(value, dict) and isinstance(value.get("structuredContent"), dict):
            value = value["structuredContent"]
        return value if isinstance(value, dict) and value.get("protocol") == "jarvis.voice.v1" else None

    def _full_observation(self, detail):
        if not detail.get("output_truncated"):
            return detail
        archive = detail.get("observation_evidence") or {}
        context = getattr(self.gateway, "observation_context", None)
        if (context is None or archive.get("archive_truncated") or
                not 0 < archive.get("retained_chars", 0) <= 131072):
            return None
        parts, offset = [], 0
        while True:
            page = context.read_evidence({"evidence_id": archive["evidence_id"], "offset": offset, "limit": 4000})
            if page.get("archive_truncated") or page.get("source") != detail.get("source"):
                return None
            parts.append(page["content"])
            following = page["next_offset"]
            if following is None:
                break
            if following <= offset or following > 131072:
                return None
            offset = following
        full = json.loads("".join(parts))
        return full if not full.get("output_truncated_at_source") and full.get("source") == detail.get("source") else None

    def poll(self):
        config = self.store.config("voice", VOICE_DEFAULTS)["data"]
        if self.readiness()["state"] != "ready" or not config["inbox_tool"]:
            return 0
        alias = _alias(config["server"], config["inbox_tool"])
        if not self.gateway._automatic_read(alias):
            return 0
        cursor = self.store.voice_cursor(config["fingerprint"])
        schema = self.gateway.registry.get_server(config["server"])["tools"][config["inbox_tool"]]["input_schema"]
        arguments = {"cursor": cursor} if cursor and "cursor" in schema.get("properties", {}) else {}
        result = self.gateway.execute(alias, arguments, approved=False)
        if not result.success:
            return 0
        detail = json.loads(result.detail or "{}")
        if not isinstance(detail, dict) or detail.get("outcome_unknown"):
            return 0
        detail = self._full_observation(detail)
        if detail is None:
            return 0
        envelope = self._envelope(detail)
        if not envelope or not isinstance(envelope.get("events"), list) or len(envelope["events"]) > 20:
            return 0
        following = envelope.get("next_cursor")
        if following is not None and (not isinstance(following, str) or len(following) > 2000):
            return 0
        count = 0
        for event in envelope["events"]:
            if not isinstance(event, dict) or event.get("authenticated") is not True or event.get("contact_id") != config["contact_id"]:
                continue
            identity, text, occurred = event.get("id"), event.get("text"), event.get("occurred_at")
            if not isinstance(identity, str) or not 1 <= len(identity) <= 120 or not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
                continue
            try:
                if not -30 <= time.time() - instant(occurred) <= 86400:
                    continue
            except (ValueError, TypeError):
                continue
            # A trusted, operator-bound transcription is a task request, not
            # approval for mail, payments, desktop use or external mutations.
            now = datetime.now(timezone.utc).isoformat()
            job = {"title": "Demande du canal vocal", "request": text, "role": "interaction", "first_at": now,
                   "daily_time": "", "timezone": "UTC", "enabled": True, "notify_owner": False}
            _, fresh = self.store.enqueue_inbound(config["fingerprint"] + ":" + identity, job,
                {"id": identity, "contact_id": event["contact_id"], "binding": config["fingerprint"]})
            count += int(fresh)
        if following is not None and "cursor" in schema.get("properties", {}):
            self.store.voice_cursor(config["fingerprint"], following)
        return count

    def reply(self, event, text, *, request_key):
        config = self.store.config("voice", VOICE_DEFAULTS)["data"]
        if (not config["automatic_replies"] or not config["reply_tool"] or self.readiness()["state"] != "ready"
                or event.get("binding") != config["fingerprint"] or event.get("contact_id") != config["contact_id"]):
            return _failure("orchestra_voice_reply_not_authorised")
        arguments = {"event_id": event["id"], "message": text[:1800]}
        entry = self.gateway.registry.get_server(config["server"])
        if "idempotency_key" in entry["tools"][config["reply_tool"]]["input_schema"].get("properties", {}):
            arguments["idempotency_key"] = request_key
        return self._dispatch(config, config["reply_tool"], arguments, key=request_key, message=text[:1800],
            maximum=self.store.config("watch", WATCH_DEFAULTS)["data"]["max_runs_day"], kind="reply")
