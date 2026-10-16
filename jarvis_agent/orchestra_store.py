"""Durable work queue and private specialist reports; never an external proof.

Reads are passive. Claims are transactional. An interrupted running job cannot
be replayed by another process; it remains available for operator review.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo

from .agent_profiles import defaults as role_defaults
from .personal_workspace import workspace_path, _text


WATCH_DEFAULTS = {"enabled": False, "max_runs_day": 4, "model_calls": 6,
                  "tool_calls": 12, "seconds": 180}
VOICE_DEFAULTS = {"enabled": False, "server": "", "contact_id": "", "dial_tool": "",
    "inbox_tool": "", "reply_tool": "", "contact_field": "contact_id", "message_field": "message",
    "fingerprint": "", "automatic_calls": False, "automatic_replies": False,
    "allow_night_calls": False, "max_calls_day": 2}
JOB_FIELDS = {"title", "request", "role", "first_at", "daily_time", "timezone", "enabled", "notify_owner"}
_SCHEMA = """
CREATE TABLE jobs(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL,
 state TEXT NOT NULL,next_at REAL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE runs(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,parent_id TEXT NOT NULL,role TEXT NOT NULL,
 state TEXT NOT NULL,request TEXT NOT NULL,report TEXT NOT NULL,receipt TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,
 source TEXT NOT NULL,target TEXT NOT NULL,kind TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE settings(name TEXT PRIMARY KEY,revision INTEGER NOT NULL,data TEXT NOT NULL);
CREATE TABLE inbound(event_key TEXT PRIMARY KEY,job_id TEXT NOT NULL);
CREATE TABLE calls(id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,state TEXT NOT NULL,
 message TEXT NOT NULL,receipt TEXT NOT NULL,created REAL NOT NULL,kind TEXT NOT NULL);
PRAGMA user_version=1;
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def instant(value):
    if not isinstance(value, str):
        raise ValueError("orchestra_aware_datetime_required")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.utcoffset() is None:
        raise ValueError("orchestra_aware_datetime_required")
    return moment.timestamp()


def next_daily(clock, zone, after):
    """First daily wall time after now; first fold, skip a nonexistent time."""
    import re
    if not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise ValueError("orchestra_invalid_daily_time")
    tz = ZoneInfo(zone) if zone != "UTC" else timezone.utc
    local = datetime.fromtimestamp(after, timezone.utc).astimezone(tz)
    hour, minute = map(int, clock.split(":"))
    for days in range(4):
        day = local.date() + timedelta(days=days)
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz, fold=0)
        stamp = candidate.timestamp()
        back = datetime.fromtimestamp(stamp, tz)
        if back.replace(tzinfo=None) == candidate.replace(tzinfo=None) and stamp > after:
            return stamp
    raise ValueError("orchestra_daily_time_unavailable")


def validate_job(data):
    if not isinstance(data, dict) or set(data) != JOB_FIELDS:
        raise ValueError("orchestra_invalid_job_fields")
    clean = {key: _text(data[key], key, cap, required=key in {"title", "request", "role", "first_at", "timezone"})
             for key, cap in (("title", 150), ("request", 4000), ("role", 40), ("first_at", 50),
                              ("daily_time", 5), ("timezone", 100))}
    role_defaults(clean["role"])
    instant(clean["first_at"])
    if clean["timezone"] != "UTC":
        ZoneInfo(clean["timezone"])
    if clean["daily_time"]:
        next_daily(clean["daily_time"], clean["timezone"], instant(clean["first_at"]) - 1)
    for key in ("enabled", "notify_owner"):
        if type(data[key]) is not bool:
            raise ValueError("orchestra_invalid_job_flag")
        clean[key] = data[key]
    return clean


class OrchestraStore:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("JARVIS_ORCHESTRA_PATH") or workspace_path().parent / "orchestra.sqlite3")

    @contextmanager
    def connection(self, *, write=False):
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        location = str(self.path) if write else self.path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(location, uri=not write, timeout=2 if write else .15)) as con:
            con.row_factory = sqlite3.Row
            if write:
                con.execute("BEGIN IMMEDIATE")
            with con:
                version = con.execute("PRAGMA user_version").fetchone()[0]
                if version == 0 and write and not con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            con.execute(statement)
                    if os.name != "nt":
                        os.chmod(self.path, 0o600)
                elif version != 1:
                    raise ValueError("orchestra_unknown_database_version")
                if not write:
                    con.execute("PRAGMA query_only=ON")
                yield con

    @staticmethod
    def _row(row):
        if row is None:
            return None
        result = dict(row)
        for key in ("data", "receipt"):
            if key in result:
                result[key] = json.loads(result[key])
        return result

    @staticmethod
    def _cas(actual, expected):
        if type(expected) is not int or actual != expected:
            raise ValueError("orchestra_changed_reload_required")

    def config(self, name, defaults):
        with self.connection() as con:
            row = con.execute("SELECT * FROM settings WHERE name=?", (name,)).fetchone() if con else None
        return {"revision": row["revision"] if row else 0, "data": json.loads(row["data"]) if row else json.loads(_json(defaults))}

    def save_config(self, name, data, *, expected_revision):
        if name not in {"watch", "voice"}:
            raise ValueError("orchestra_invalid_config")
        if name == "watch":
            if not isinstance(data, dict) or set(data) != set(WATCH_DEFAULTS) or type(data["enabled"]) is not bool:
                raise ValueError("orchestra_invalid_watch")
            for key, low, high in (("max_runs_day", 1, 24), ("model_calls", 1, 24), ("tool_calls", 1, 48), ("seconds", 15, 600)):
                if type(data[key]) is not int or not low <= data[key] <= high:
                    raise ValueError("orchestra_invalid_watch_budget")
        if name == "voice" and (not isinstance(data, dict) or set(data) != set(VOICE_DEFAULTS)):
            raise ValueError("orchestra_invalid_voice")
        if name == "voice":
            if any(type(data[k]) is not bool for k in ("enabled", "automatic_calls", "automatic_replies", "allow_night_calls")):
                raise ValueError("orchestra_invalid_voice_flag")
            if type(data["max_calls_day"]) is not int or not 1 <= data["max_calls_day"] <= 12:
                raise ValueError("orchestra_invalid_call_quota")
            for key, default in VOICE_DEFAULTS.items():
                if isinstance(default, str):
                    _text(data[key], key, 180)
        payload = _json(data)
        with self.connection(write=True) as con:
            row = con.execute("SELECT revision FROM settings WHERE name=?", (name,)).fetchone()
            revision = row[0] if row else 0
            self._cas(revision, expected_revision)
            con.execute("INSERT INTO settings VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET revision=excluded.revision,data=excluded.data",
                        (name, revision + 1, payload))
        return {"revision": revision + 1, "data": data}

    def heartbeat(self, owner, *, busy=False, stopped=False):
        with self.connection(write=True) as con:
            con.execute("INSERT INTO settings VALUES ('engine',1,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data",
                        (_json({"owner": owner, "busy": busy, "at": time.time(), "stopped": stopped}),))

    def save_job(self, data, *, identity="", expected_revision=0):
        clean, now = validate_job(data), time.time()
        with self.connection(write=True) as con:
            old = con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone() if identity else None
            if identity and old is None:
                raise ValueError("orchestra_job_not_found")
            if old and old["state"] == "running":
                raise ValueError("orchestra_job_running")
            self._cas(old["revision"] if old else 0, expected_revision)
            identity = identity or uuid.uuid4().hex[:24]
            con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "revision=excluded.revision,data=excluded.data,state=excluded.state,next_at=excluded.next_at,updated=excluded.updated",
                (identity, expected_revision + 1, _json(clean), "scheduled" if clean["enabled"] else "paused",
                 instant(clean["first_at"]), old["created"] if old else now, now))
        return self.job(identity)

    def job(self, identity):
        with self.connection() as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone() if con else None
        return self._row(row)

    def jobs(self, limit=50):
        with self.connection() as con:
            rows = con.execute("SELECT * FROM jobs ORDER BY state='running' DESC,next_at,created DESC LIMIT ?", (max(1, min(100, limit)),)).fetchall() if con else []
        return [self._row(row) for row in rows]

    def enqueue_inbound(self, event_key, data, event):
        clean = validate_job(data)
        clean.update(origin="voice", reply_event=event)
        identity, now = uuid.uuid4().hex[:24], time.time()
        with self.connection(write=True) as con:
            prior = con.execute("SELECT job_id FROM inbound WHERE event_key=?", (event_key,)).fetchone()
            if prior:
                return prior[0], False
            con.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", (identity, 1, _json(clean), "scheduled", now, now, now))
            con.execute("INSERT INTO inbound VALUES (?,?)", (event_key, identity))
        return identity, True

    def claim_due(self, *, now=None):
        now = time.time() if now is None else now
        policy = self.config("watch", WATCH_DEFAULTS)["data"]
        if not policy["enabled"]:
            return None
        with self.connection(write=True) as con:
            row = con.execute("SELECT data FROM settings WHERE name='watch'").fetchone()
            policy = json.loads(row[0]) if row else WATCH_DEFAULTS
            if not policy["enabled"]:
                return None
            if con.execute("SELECT id FROM jobs WHERE state='running' LIMIT 1").fetchone():
                return None
            count = con.execute("SELECT count(*) FROM runs WHERE job_id!='' AND created>?", (now - 86400,)).fetchone()[0]
            if count >= policy["max_runs_day"]:
                return None
            row = con.execute("SELECT * FROM jobs WHERE state='scheduled' AND next_at<=? "
                "ORDER BY json_extract(data,'$.origin')='voice' DESC,next_at,created LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            con.execute("UPDATE jobs SET state='running',revision=revision+1,updated=? WHERE id=?", (now, row["id"]))
        return self.job(row["id"])

    def finish_job(self, identity, *, success):
        with self.connection(write=True) as con:
            row = con.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
            if row is None or row["state"] != "running":
                raise ValueError("orchestra_job_not_running")
            data, now = json.loads(row["data"]), time.time()
            repeat = success and bool(data["daily_time"])
            stamp = next_daily(data["daily_time"], data["timezone"], now) if repeat else None
            con.execute("UPDATE jobs SET state=?,next_at=?,revision=revision+1,updated=? WHERE id=?",
                        ("scheduled" if repeat else "report_ready" if success else "needs_review", stamp, now, identity))

    def cancel_job(self, identity, *, expected_revision):
        with self.connection(write=True) as con:
            row = con.execute("SELECT revision,state FROM jobs WHERE id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("orchestra_job_not_found")
            self._cas(row["revision"], expected_revision)
            if row["state"] == "running":
                raise ValueError("orchestra_stop_engine_before_review")
            con.execute("UPDATE jobs SET state='cancelled',revision=revision+1,updated=? WHERE id=?", (time.time(), identity))

    def review_interrupted(self, identity, *, expected_revision):
        """Archive only. Never requeue or clear the MCP's uncertain-attempt ledger."""
        if self.config("watch", WATCH_DEFAULTS)["data"]["enabled"]:
            raise ValueError("orchestra_stop_engine_before_review")
        engine = self.config("engine", {})["data"]
        if engine and not engine.get("stopped") and time.time() - engine.get("at", 0) < 20:
            raise ValueError("orchestra_stop_engine_before_review")
        with self.connection(write=True) as con:
            row = con.execute("SELECT revision,state FROM jobs WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] != "running":
                raise ValueError("orchestra_job_not_running")
            self._cas(row["revision"], expected_revision)
            con.execute("UPDATE jobs SET state='needs_review',revision=revision+1,updated=? WHERE id=?", (time.time(), identity))

    def start_run(self, role, request, *, parent_id="", job_id=""):
        role_defaults(role)
        request = _text(request, "request", 4000, required=True)
        identity, now = uuid.uuid4().hex[:24], time.time()
        with self.connection(write=True) as con:
            con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (identity, job_id, parent_id, role, "running", request, "", "{}", now, now))
            self._event(con, identity, "interaction", role, "dispatch", now)
        return identity

    @staticmethod
    def _event(con, identity, source, target, kind, now):
        con.execute("INSERT INTO events(run_id,source,target,kind,created) VALUES (?,?,?,?,?)", (identity, source, target, kind, now))

    def finish_run(self, identity, *, state, report, receipt):
        if state not in {"report_ready", "needs_review", "failed", "stopped"}:
            raise ValueError("orchestra_invalid_run_state")
        report = _text(report, "report", 100000)
        with self.connection(write=True) as con:
            row = con.execute("SELECT role,state FROM runs WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] != "running":
                raise ValueError("orchestra_run_not_running")
            now = time.time()
            con.execute("UPDATE runs SET state=?,report=?,receipt=?,updated=? WHERE id=?", (state, report, _json(receipt), now, identity))
            self._event(con, identity, row["role"], "interaction", "report" if state == "report_ready" else "pause", now)

    def run(self, identity):
        with self.connection() as con:
            row = con.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone() if con else None
        return self._row(row)

    def runs(self, limit=30):
        with self.connection() as con:
            rows = con.execute("SELECT id,job_id,parent_id,role,state,created,updated FROM runs ORDER BY created DESC LIMIT ?", (max(1, min(100, limit)),)).fetchall() if con else []
        return [dict(row) for row in rows]

    def events(self, after=0, limit=80):
        with self.connection() as con:
            rows = con.execute("SELECT * FROM events WHERE sequence>? ORDER BY sequence DESC LIMIT ?", (after, max(1, min(100, limit)))).fetchall() if con else []
        return list(reversed([dict(row) for row in rows]))

    def reserve_call(self, request_key, message, *, maximum, kind="call"):
        identity, now = uuid.uuid4().hex[:24], time.time()
        with self.connection(write=True) as con:
            prior = con.execute("SELECT * FROM calls WHERE request_key=?", (request_key,)).fetchone()
            if prior:
                return self._row(prior), False
            if con.execute("SELECT id FROM calls WHERE state IN ('dispatching','unknown') AND kind=? LIMIT 1", (kind,)).fetchone():
                raise ValueError("orchestra_prior_voice_outcome_unknown")
            if con.execute("SELECT count(*) FROM calls WHERE created>? AND kind=?", (now - 86400, kind)).fetchone()[0] >= maximum:
                raise ValueError("orchestra_call_quota")
            con.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?)", (identity, request_key, "dispatching", message, "{}", now, kind))
        return {"id": identity, "state": "dispatching"}, True

    def finish_call(self, identity, state, receipt):
        with self.connection(write=True) as con:
            con.execute("UPDATE calls SET state=?,receipt=? WHERE id=?", (state, _json(receipt), identity))

    def calls(self, limit=30):
        with self.connection() as con:
            rows = con.execute("SELECT * FROM calls ORDER BY created DESC LIMIT ?", (max(1, min(100, limit)),)).fetchall() if con else []
        return [self._row(row) for row in rows]

    def review_call(self, identity, *, expected_state):
        """Operator acknowledgement, not delivery proof or a service retry."""
        with self.connection(write=True) as con:
            for name in ("watch", "voice"):
                setting = con.execute("SELECT data FROM settings WHERE name=?", (name,)).fetchone()
                if setting and json.loads(setting[0])["enabled"]:
                    raise ValueError("orchestra_disable_watch_and_voice_before_review")
            row = con.execute("SELECT * FROM calls WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] != expected_state or row["state"] not in {"unknown", "dispatching"}:
                raise ValueError("orchestra_changed_reload_required")
            if row["state"] == "dispatching" and time.time() - row["created"] < 600:
                raise ValueError("orchestra_voice_dispatch_still_recent")
            receipt = json.loads(row["receipt"])
            receipt.update(operator_reviewed=True, reviewed_at=time.time(), delivery_verified=False,
                           original_state=row["state"], automatic_replay=False)
            con.execute("UPDATE calls SET state='reviewed',receipt=? WHERE id=?", (_json(receipt), identity))

    def voice_cursor(self, binding, cursor=None):
        name = "voice_cursor:" + binding
        if cursor is None:
            return self.config(name, {"cursor": ""})["data"]["cursor"]
        if not isinstance(cursor, str) or len(cursor) > 2000:
            raise ValueError("orchestra_invalid_voice_cursor")
        with self.connection(write=True) as con:
            con.execute("INSERT INTO settings VALUES (?,1,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data",
                        (name, _json({"cursor": cursor})))

    def snapshot(self):
        engine = self.config("engine", {})["data"]
        return {"watch": self.config("watch", WATCH_DEFAULTS), "voice": self.config("voice", VOICE_DEFAULTS),
            "engine": {**engine, "online": bool(engine and not engine.get("stopped") and time.time()-engine.get("at", 0) < 15)},
            "jobs": self.jobs(30), "runs": self.runs(30), "events": self.events(limit=50),
            "independent_contexts": True, "external_actions_via_coordinator": True}
