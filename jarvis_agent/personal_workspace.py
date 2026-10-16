"""Owner's local profile, activities and external-action preparations.

No connection, tool grant, external dispatch or personal-memory migration.
Reads never create a database. Writes use SQLite transactions and revisions.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .mcp_personal_workspace import CONNECTIONS, get_connection


DOMAINS = {"daily": "Quotidien", "football": "Football personnel",
           "ms_football": "MS Football", "software": "Projets logiciels"}
KINDS = {"task": "Tâche", "event": "Rendez-vous", "follow_up": "Suivi"}
STATUSES = {"todo": "À faire", "in_progress": "En cours", "waiting": "En attente",
            "done": "Terminé localement", "cancelled": "Annulé"}
PRIORITIES = {"high": "Haute", "normal": "Normale", "low": "Basse"}
TERMINAL = {"done", "cancelled"}
PROFILE_DEFAULTS = {"display_name": "", "timezone": "UTC", "roles": [], "about": "",
                    "objectives": "", "preferences": "", "use_for_agent": False}
ACTIVITY_DEFAULTS = {"title": "", "domain": "daily", "kind": "task", "subject": "",
    "status": "todo", "priority": "normal", "due_on": "", "at_time": "", "details": "", "service": ""}
_ID = re.compile(r"^[a-f0-9]{24}$")
_SERVER = re.compile(r"^[a-zA-Z0-9_-]{1,60}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_profile (
    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_activities (
    id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_preparations (
    id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_bindings (
    service TEXT PRIMARY KEY, server TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_history (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, object_id TEXT NOT NULL,
    revision INTEGER NOT NULL, payload TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_requests (
    request_key TEXT PRIMARY KEY, digest TEXT NOT NULL, entity TEXT NOT NULL, object_id TEXT NOT NULL);
PRAGMA user_version=1;
"""


def workspace_path() -> Path:
    custom = os.getenv("JARVIS_PERSONAL_WORKSPACE_PATH", "").strip()
    if custom:
        return Path(custom)
    root = os.getenv("JARVIS_DATA_DIR") or os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME")
    return (Path(root) if root else Path.home() / ".jarvis_personal") / "JarvisPersonal" / "personal_workspace.sqlite3"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def wire_size(value):
    """Conservative serialized size, including non-ASCII escaping by providers."""
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def _text(value, name, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (required and not value.strip()):
        raise ValueError("invalid_workspace_field:" + name)
    return value.strip()


def _choice(value, name, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError("invalid_workspace_field:" + name)
    return value


def _zone(name):
    # UTC works even on a Windows installation that has not yet installed
    # the tzdata dependency. Other IANA zones use its actual DST rules.
    return timezone.utc if name == "UTC" else ZoneInfo(name)


def validate_profile(value):
    if not isinstance(value, dict) or set(value) != set(PROFILE_DEFAULTS):
        raise ValueError("invalid_workspace_profile_fields")
    clean = {key: _text(value[key], key, limit) for key, limit in (
        ("display_name", 100), ("timezone", 100), ("about", 3000),
        ("objectives", 2000), ("preferences", 2000))}
    try:
        _zone(clean["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("invalid_workspace_timezone") from exc
    roles = value["roles"]
    if not isinstance(roles, list) or len(roles) > 12:
        raise ValueError("invalid_workspace_roles")
    clean["roles"] = list(dict.fromkeys(_text(r, "role", 100, required=True) for r in roles))
    if type(value["use_for_agent"]) is not bool:
        raise ValueError("invalid_workspace_profile_consent")
    clean["use_for_agent"] = value["use_for_agent"]
    return clean


def validate_activity(value):
    if not isinstance(value, dict) or set(value) != set(ACTIVITY_DEFAULTS):
        raise ValueError("invalid_workspace_activity_fields")
    clean = {key: _text(value[key], key, limit, required=key == "title")
             for key, limit in (("title", 200), ("subject", 200), ("details", 6000),
                                ("due_on", 10), ("at_time", 5), ("service", 60))}
    for key, choices in (("domain", DOMAINS), ("kind", KINDS), ("status", STATUSES), ("priority", PRIORITIES)):
        clean[key] = _choice(value[key], key, choices)
    if clean["due_on"]:
        try:
            if date.fromisoformat(clean["due_on"]).isoformat() != clean["due_on"]:
                raise ValueError()
        except ValueError as exc:
            raise ValueError("invalid_workspace_due_date") from exc
    if clean["at_time"] and (not clean["due_on"] or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clean["at_time"])):
        raise ValueError("invalid_workspace_due_time")
    if clean["service"] and get_connection(clean["service"]) is None:
        raise ValueError("unknown_workspace_service")
    return clean


class PersonalWorkspaceStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else workspace_path()

    @contextmanager
    def _connection(self, *, write=False):
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        location = str(self.path) if write else self.path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(location, uri=not write, timeout=1.0 if write else 0.2)) as con:
            con.row_factory = sqlite3.Row
            if write:
                con.execute("BEGIN IMMEDIATE")
            with con:
                version = con.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise ValueError("unsupported_workspace_version")
                if version == 0:
                    tables = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    if tables or not write:
                        raise ValueError("unrecognized_workspace_database")
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            con.execute(statement)
                    if os.name != "nt":
                        os.chmod(self.path, 0o600)
                if not write:
                    con.execute("PRAGMA query_only=ON")
                con.create_function("workspace_casefold", 1, lambda s: str(s or "").casefold(), deterministic=True)
                yield con

    @staticmethod
    def _record(row):
        return {"id": row["id"], "revision": row["revision"], "data": json.loads(row["payload"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"]} if row else None

    @staticmethod
    def _revision(actual, expected):
        if type(expected) is not int or expected != actual:
            raise ValueError("workspace_changed_reload_required")

    @staticmethod
    def _history(con, entity, identity, revision, payload, recorded):
        con.execute("INSERT INTO workspace_history(entity,object_id,revision,payload,recorded_at) VALUES (?,?,?,?,?)",
                    (entity, identity, revision, _json(payload), recorded))

    def profile(self):
        with self._connection() as con:
            row = con.execute("SELECT * FROM workspace_profile WHERE id=1").fetchone() if con else None
        return {"revision": row["revision"] if row else 0,
                "data": validate_profile(json.loads(row["payload"])) if row else {**PROFILE_DEFAULTS, "roles": []}}

    def save_profile(self, value, *, expected_revision):
        clean = validate_profile(value)
        now = datetime.now(timezone.utc).isoformat()
        with self._connection(write=True) as con:
            row = con.execute("SELECT revision FROM workspace_profile WHERE id=1").fetchone()
            revision = row[0] if row else 0
            self._revision(revision, expected_revision)
            con.execute("INSERT INTO workspace_profile VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at",
                        (revision + 1, _json(clean), now))
            self._history(con, "profile", "owner", revision + 1, clean, now)
        return {"revision": revision + 1, "data": clean}

    def activity(self, identity):
        return self._get("activities", identity)

    def preparation(self, identity):
        return self._get("preparations", identity)

    def _get(self, entity, identity):
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            raise ValueError("invalid_workspace_id")
        with self._connection() as con:
            row = con.execute("SELECT * FROM workspace_" + entity + " WHERE id=?", (identity,)).fetchone() if con else None
        return self._record(row)

    def activities(self, *, domain="", status="", query="", due_on="", limit=100, offset=0, active_only=False):
        if domain:
            _choice(domain, "domain", DOMAINS)
        if status:
            _choice(status, "status", STATUSES)
        query = _text(query, "query", 200).casefold()
        due_on = _text(due_on, "due_on", 10)
        if due_on and date.fromisoformat(due_on).isoformat() != due_on:
            raise ValueError("invalid_workspace_due_date")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid_workspace_limit")
        if type(offset) is not int or not 0 <= offset <= 1000000:
            raise ValueError("invalid_workspace_offset")
        with self._connection() as con:
            rows = con.execute("SELECT * FROM workspace_activities WHERE "
                "(?='' OR json_extract(payload,'$.domain')=?) AND (?='' OR json_extract(payload,'$.status')=?) "
                "AND (?='' OR json_extract(payload,'$.due_on')=?) "
                "AND (?=0 OR json_extract(payload,'$.status') NOT IN ('done','cancelled')) "
                "AND (?='' OR instr(workspace_casefold(json_extract(payload,'$.title')||' '||"
                "json_extract(payload,'$.subject')||' '||json_extract(payload,'$.details')),?)>0) "
                "ORDER BY json_extract(payload,'$.status') IN ('done','cancelled'), "
                "json_extract(payload,'$.due_on')='',json_extract(payload,'$.due_on'),"
                "json_extract(payload,'$.at_time'),CASE json_extract(payload,'$.priority') WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,"
                "updated_at DESC LIMIT ? OFFSET ?", (domain, domain, status, status, due_on, due_on, int(active_only), query, query, limit, offset)).fetchall() if con else []
        found = [self._record(row) for row in rows]
        for row in found:
            validate_activity(row["data"])
        return found

    def preparations(self, *, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_workspace_limit")
        if type(offset) is not int or not 0 <= offset <= 1000000:
            raise ValueError("invalid_workspace_offset")
        with self._connection() as con:
            rows = con.execute("SELECT * FROM workspace_preparations ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall() if con else []
        return [self._record(row) for row in rows]

    def _save(self, entity, clean, *, identity="", expected_revision=0, request_key=""):
        if not isinstance(identity, str) or (identity and not _ID.fullmatch(identity)):
            raise ValueError("invalid_workspace_id")
        request_key = _text(request_key, "request_key", 150)
        digest = hashlib.sha256(_json({"entity": entity, "identity": identity, "data": clean,
                                      "expected_revision": expected_revision}).encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self._connection(write=True) as con:
            prior = con.execute("SELECT * FROM workspace_requests WHERE request_key=?", (request_key,)).fetchone() if request_key else None
            if prior:
                if prior["digest"] != digest or prior["entity"] != entity:
                    raise ValueError("workspace_request_key_reused")
                row = con.execute("SELECT * FROM workspace_" + entity + " WHERE id=?", (prior["object_id"],)).fetchone()
                return {**self._record(row), "reused_receipt": True}
            row = con.execute("SELECT * FROM workspace_" + entity + " WHERE id=?", (identity,)).fetchone() if identity else None
            if identity and row is None:
                raise ValueError("workspace_record_not_found")
            revision = row["revision"] if row else 0
            self._revision(revision, expected_revision)
            identity = identity or uuid.uuid4().hex[:24]
            created = row["created_at"] if row else now
            con.execute("INSERT INTO workspace_" + entity + " VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at",
                        (identity, revision + 1, _json(clean), created, now))
            self._history(con, entity, identity, revision + 1, clean, now)
            if request_key:
                con.execute("INSERT INTO workspace_requests VALUES (?,?,?,?)", (request_key, digest, entity, identity))
        return {"id": identity, "revision": revision + 1, "data": clean, "created_at": created, "updated_at": now}

    def save_activity(self, value, *, identity="", expected_revision=0, request_key=""):
        return self._save("activities", validate_activity(value), identity=identity,
                          expected_revision=expected_revision, request_key=request_key)

    def prepare_action(self, *, service, operation, instructions, activity_id="", request_key=""):
        if get_connection(service) is None:
            raise ValueError("unknown_workspace_service")
        operation = _text(operation, "operation", 120, required=True)
        instructions = _text(instructions, "instructions", 6000, required=True)
        if activity_id and self.activity(activity_id) is None:
            raise ValueError("workspace_activity_not_found")
        return self._save("preparations", {"service": service, "operation": operation,
            "instructions": instructions, "activity_id": activity_id, "state": "prepared_local",
            "external_dispatched": False, "external_verified": False}, request_key=request_key)

    def bindings(self):
        with self._connection() as con:
            rows = con.execute("SELECT * FROM workspace_bindings").fetchall() if con else []
        return {row["service"]: {"server": row["server"], "revision": row["revision"]} for row in rows}

    def bind_service(self, service, server, *, expected_revision):
        entry = get_connection(service)
        if entry is None or entry.integration == "native":
            raise ValueError("invalid_workspace_service_binding")
        if not isinstance(server, str) or (server and not _SERVER.fullmatch(server)):
            raise ValueError("invalid_workspace_server_id")
        with self._connection(write=True) as con:
            row = con.execute("SELECT revision FROM workspace_bindings WHERE service=?", (service,)).fetchone()
            revision = row[0] if row else 0
            self._revision(revision, expected_revision)
            con.execute("INSERT INTO workspace_bindings VALUES (?,?,?) ON CONFLICT(service) DO UPDATE SET "
                        "server=excluded.server,revision=excluded.revision", (service, server, revision + 1))
            self._history(con, "binding", service, revision + 1, {"server": server}, datetime.now(timezone.utc).isoformat())
        return {"service": service, "server": server, "revision": revision + 1}

    def brief(self, *, now=None, limit=12):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_workspace_limit")
        profile = self.profile()["data"]
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("workspace_aware_time_required")
        current = current.astimezone(_zone(profile["timezone"]))
        today = current.date().isoformat()
        end = (current.date() + timedelta(days=7)).isoformat()
        active = self.activities(limit=500, active_only=True)
        with self._connection() as con:
            active_count = con.execute("SELECT COUNT(*) FROM workspace_activities WHERE "
                "json_extract(payload,'$.status') NOT IN ('done','cancelled')").fetchone()[0] if con else 0
        active.sort(key=lambda r: (r["data"]["due_on"] or "9999", r["data"]["at_time"] or "23:59",
                                  {"high": 0, "normal": 1, "low": 2}[r["data"]["priority"]], r["id"]))
        groups = {"overdue": [], "today": [], "upcoming": [], "waiting": [], "unscheduled": []}
        for row in active:
            data = row["data"]
            due = data["due_on"]
            if data["status"] == "waiting":
                groups["waiting"].append(row)
            if not due:
                groups["unscheduled"].append(row)
            elif due < today or (due == today and data["at_time"] and data["at_time"] < current.strftime("%H:%M")):
                groups["overdue"].append(row)
            if due == today:
                groups["today"].append(row)
            elif today < due <= end:
                groups["upcoming"].append(row)
        return {"date": today, "timezone": profile["timezone"], "now": current.isoformat(),
                "counts": {key: len(rows) for key, rows in groups.items()},
                "groups": {key: rows[:limit] for key, rows in groups.items()},
                "scope": "local_workspace_only", "external_calendars_consulted": False,
                "background_notifications": False, "active_inventory_limit": 500,
                "active_total": active_count, "inventory_partial": active_count > len(active)}

    def context(self):
        """Only opted-in profile data; tasks/details are fetched explicitly."""
        profile = self.profile()
        if not profile["data"]["use_for_agent"]:
            return ""
        data = profile["data"]
        projection = {"display_name": data["display_name"], "timezone": data["timezone"], "roles": data["roles"],
                      **{key: data[key][:600] for key in ("about", "objectives", "preferences")}}
        projection["roles"] = list(projection["roles"])
        # Arabic/French text can expand substantially in JSON wire encoding.
        # Bound the projection, never the saved profile or human instructions.
        while wire_size(projection) > 2800:
            candidates = [(wire_size(projection[k]), k) for k in ("about", "objectives", "preferences") if projection[k]]
            candidates += [(wire_size(projection["roles"]), "roles")] if projection["roles"] else []
            _, key = max(candidates)
            if key == "roles":
                projection[key] = projection[key][:-1]
            else:
                projection[key] = projection[key][:len(projection[key]) // 2]
        return "\n\nPERSONAL_WORKSPACE_DATA (owner-provided context, never permission or a replacement for the current request):\n" + _json({
            "profile_revision": profile["revision"], "profile": projection,
            "partial_fields": [key for key in ("about", "objectives", "preferences", "roles") if data[key] != projection[key]],
            "more": "workspace_read", "scope": "local_owner_profile",
            "external_service_status": "inspect_actual_service_tools"})


def service_snapshot(store, servers=(), *, runtime_enabled=False, available=()):
    """Join explicit local bindings with ACTUAL MCP grants. No network access."""
    bindings = store.bindings()
    index = {row["id"]: row for row in servers if isinstance(row, dict) and isinstance(row.get("id"), str)}
    records = []
    for entry in CONNECTIONS:
        binding = bindings.get(entry.identifier)
        server_id = binding["server"] if binding else entry.catalog_id
        server = index.get(server_id)
        tools = sorted(str(name) for name, meta in available.items() if meta.get("server") == server_id) if isinstance(available, dict) else []
        if entry.integration == "native":
            state = "native"
        elif not server:
            state = "not_configured"
        elif not runtime_enabled:
            state = "runtime_off"
        elif server.get("enabled") is not True:
            state = "disabled"
        elif not server.get("last_discovery_success_utc"):
            state = "discovery_required"
        elif tools:
            state = "tools_available"
        else:
            state = "no_allowed_tools"
        records.append({"id": entry.identifier, "name": entry.label, "area": entry.area,
            "purpose": entry.purpose, "integration": entry.integration,
            "server": server_id, "binding_revision": binding["revision"] if binding else 0,
            "state": state, "tools": tools[:40], "tool_count": len(tools),
            "connected_now": False, "permissions_changed": False})
    return records
