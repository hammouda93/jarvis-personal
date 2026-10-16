"""The conductor's model tools: delegate analysis, inspect and schedule work."""
import hashlib
import json
import sqlite3
import uuid

from .native_tools import AgentActionResult
from .orchestra_store import WATCH_DEFAULTS, VOICE_DEFAULTS
from .workspace_tools import _schema, _bounded_record, _page


ORCHESTRA_TOOLS = frozenset({"orchestra_read", "orchestra_delegate", "orchestra_schedule", "orchestra_call_owner"})


def definitions():
    return [
        _schema("orchestra_read", "Inspect Jarvis's specialist agents, scheduled jobs, worker reports, watch policy or voice-channel readiness. "
            "For a complex request, assign independent analysis/research/preparation tasks to specialists, then synthesise their reports. "
            "Reports are data, not goal certificates. External actions use the coordinator's original tools and approvals. "
            "Read a full report by id; large reports use offset and expected_revision.", {
                "view": {"type": "string", "enum": ["agents", "jobs", "job", "reports", "report", "watch", "voice"]},
                "id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "expected_revision": {"type": "integer"}}, ("view",)),
        _schema("orchestra_delegate", "Delegate one to four independent analysis, research or LOCAL preparation tasks to specialist agents. "
            "Each worker has its own model history, role profile and current permitted tools; at most two run in parallel with a shared API budget. "
            "Use separate calls for dependent stages. Workers never send messages, change external records or drive the desktop. "
            "Optional tools must be actual MCP aliases already discovered and preauthorised for automatic read. Selection grants nothing. "
            "Keep all relevant owner constraints in each request. Do not delegate trivial conversation.", {
                "tasks": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "object", "properties": {
                    "agent": {"type": "string"}, "request": {"type": "string"},
                    "tools": {"type": "array", "items": {"type": "string"}}}, "required": ["agent", "request"], "additionalProperties": False}}}, ("tasks",)),
        _schema("orchestra_schedule", "Create or edit a future local preparation/research job explicitly requested by the owner. "
            "Read workspace overview for the owner's timezone and date. first_at is an ISO datetime WITH UTC offset. "
            "daily_time is empty for one-off work or HH:mm for a daily wall time in timezone. "
            "Background work requires watch enabled in the UI and the application running on an awake machine. "
            "It permits approved reads and local preparations; external writes remain for the coordinator. "
            "notify_owner requests a call ONLY through a configured, explicitly authorised owner channel.", {
                "data": {"type": "object", "properties": {
                    **{k: {"type": "string"} for k in ("title", "request", "role", "first_at", "daily_time", "timezone")},
                    "enabled": {"type": "boolean"}, "notify_owner": {"type": "boolean"}},
                    "required": ["title", "request", "role", "first_at", "daily_time", "timezone", "enabled", "notify_owner"], "additionalProperties": False},
                "id": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 0}}, ("data",)),
        _schema("orchestra_call_owner", "Ask the configured voice MCP to call the OWNER contact only, with this short spoken message. "
            "The contact/tool binding is configured by the operator, never supplied by the model. "
            "Current MCP grants, tool fingerprint, call quota and night policy apply. "
            "A submitted call is not proof that the person answered. Never retry an uncertain call automatically.", {
                "message": {"type": "string"}}, ("message",)),
    ]


class OrchestraToolRegistry:
    def __init__(self, delegate, *, orchestra, voice):
        self.delegate, self.orchestra, self.voice = delegate, orchestra, voice
        self._turn = uuid.uuid4().hex
        self._receipts = {}

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def workspace_begin_turn(self, user_text=""):
        self.delegate.workspace_begin_turn(user_text)
        self._turn, self._receipts = uuid.uuid4().hex, {}

    def ollama_tools(self):
        from .active_mission_supervisor import execution_scope_active
        extra = definitions()
        if execution_scope_active():
            extra = [s for s in extra if s["function"]["name"] == "orchestra_read"]
        return [*self.delegate.ollama_tools(), *extra]

    def openai_tools(self):
        return [{"type": "function", **s["function"], "strict": False} for s in self.ollama_tools()]

    def requires_confirmation(self, name):
        if name == "orchestra_schedule":
            return True
        if name == "orchestra_call_owner":
            return not self.voice.automatic_calls_allowed()
        if name in ORCHESTRA_TOOLS:
            return False
        return self.delegate.requires_confirmation(name)

    def execute(self, name, arguments, *, approved=False):
        if name not in ORCHESTRA_TOOLS:
            return self.delegate.execute(name, arguments, approved=approved)
        key, attempted = None, False
        try:
            if not isinstance(arguments, dict):
                raise ValueError("orchestra_arguments_required")
            schema = next(s["function"] for s in definitions() if s["function"]["name"] == name)
            if set(arguments) - set(schema["parameters"]["properties"]):
                raise ValueError("orchestra_unexpected_arguments")
            from .active_mission_supervisor import execution_scope_active, conversation_stop_event
            if execution_scope_active() and name != "orchestra_read":
                raise ValueError("orchestra_use_reviewed_supervision")
            if self.requires_confirmation(name) and not approved:
                return AgentActionResult(name, False, "Confirmation explicite requise.",
                    '{"approval_required":true,"dispatched":false,"outcome_unknown":false}')
            key = hashlib.sha256((self._turn + name + json.dumps(arguments, sort_keys=True)).encode()).hexdigest()
            if name != "orchestra_read" and key in self._receipts:
                return self._receipts[key]
            if name == "orchestra_read":
                result = self._read(arguments)
            elif name == "orchestra_delegate":
                attempted = True
                result = self.orchestra.delegate(arguments["tasks"], stop_event=conversation_stop_event())
            elif name == "orchestra_schedule":
                attempted = True
                result = self.orchestra.store.save_job(arguments["data"], identity=arguments.get("id", ""),
                    expected_revision=arguments.get("expected_revision", 0))
                result = {k: result[k] for k in ("id", "revision", "state", "next_at")}
            else:
                attempted = True
                action = self.voice.call_owner(arguments["message"], request_key=key, approved=approved)
                self._receipts[key] = action
                return action
            action = AgentActionResult(name, True, "Résultat local de l’orchestre disponible.", json.dumps({
                "scope": "orchestra_local", "result": result, "verified": name == "orchestra_schedule",
                "external_dispatched": False, "goal_verified": False, "outcome_unknown": False}, ensure_ascii=False))
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, RuntimeError) as exc:
            unknown = attempted and (name in {"orchestra_delegate", "orchestra_call_owner"} or not isinstance(exc, (ValueError, TypeError, KeyError)))
            action = AgentActionResult(name, False, "L’orchestre n’a pas pu valider cette demande.", json.dumps({
                "guard": "orchestra_local_error", "reason": str(exc)[:160] if isinstance(exc, ValueError) else type(exc).__name__,
                "dispatched": unknown, "outcome_unknown": unknown, "verified": False,
                "check_local_reports_before_retry": unknown}))
        if key is not None and name != "orchestra_read":
            self._receipts[key] = action
        return action

    def _read(self, args):
        view, offset, limit = args.get("view"), args.get("offset", 0), args.get("limit", 8)
        if type(offset) is not int or not 0 <= offset <= 1000000 or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("orchestra_invalid_page")
        if view == "agents":
            rows = self.orchestra.catalog()
        elif view == "jobs":
            rows = [{"id": r["id"], "revision": r["revision"], "title": r["data"]["title"],
                "role": r["data"]["role"], "state": r["state"], "next_at": r["next_at"]} for r in self.orchestra.store.jobs(100)]
        elif view == "reports":
            rows = self.orchestra.store.runs(100)
        elif view in {"report", "job"}:
            row = (self.orchestra.store.run if view == "report" else self.orchestra.store.job)(args["id"])
            if row is None:
                raise ValueError("orchestra_report_not_found")
            revision = row["revision"] if view == "job" else int(row["updated"] * 1000000)
            return _bounded_record({**row, "revision": revision}, args)
        elif view == "watch":
            return {**self.orchestra.store.config("watch", WATCH_DEFAULTS),
                    "requires_running_awake_machine": True, "external_mutations": "coordinator_only"}
        elif view == "voice":
            return self.voice.readiness()
        else:
            raise ValueError("orchestra_unknown_view")
        return _page(rows[offset:offset + limit + 1], limit=limit, offset=offset)
