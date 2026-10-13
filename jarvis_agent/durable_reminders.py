"""Opt-in local reminders: durable occurrences, never an autonomous tool executor."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .native_tools import AgentActionResult
from .sqlite_utils import ClosingConnection


def reminder_path() -> Path:
    root = os.getenv("JARVIS_DATA_DIR")
    if root:
        return Path(root) / "reminders.sqlite3"
    local = os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME")
    return (Path(local) / "JarvisPersonal" if local else Path.home() / ".jarvis_personal") / "reminders.sqlite3"


def _utc(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).isoformat(timespec="seconds")


def reminder_preview(arguments: dict, *, now: float) -> dict:
    if set(arguments) - {"title", "start_at", "timezone", "recurrence", "preview_digest"}:
        raise ValueError("unsupported_reminder_arguments")
    title = arguments.get("title")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 500:
        raise ValueError("reminder_title_required_max_500")
    start = datetime.fromisoformat(str(arguments.get("start_at") or ""))
    if start.tzinfo is None or start.microsecond:
        raise ValueError("reminder_aware_date_required_whole_seconds")
    zone = ZoneInfo(str(arguments.get("timezone") or ""))
    local = start.astimezone(zone)
    if local.replace(tzinfo=None) != start.replace(tzinfo=None) or local.utcoffset() != start.utcoffset():
        raise ValueError("reminder_timezone_offset_mismatch_or_nonexistent_time")
    if start.timestamp() <= now:
        raise ValueError("reminder_future_date_required")
    recurrence = arguments.get("recurrence", "once")
    if recurrence not in {"once", "daily", "weekly"}:
        raise ValueError("unsupported_reminder_recurrence")
    packet = {"title": title.strip(), "start_at": local.isoformat(timespec="seconds"),
              "timezone": zone.key, "recurrence": recurrence}
    digest = hashlib.sha256(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {**packet, "preview_digest": digest, "next_run_utc": _utc(local),
            "notification_target": "jarvis_in_app", "requires_app_open": True,
            "automatic_mission_execution": False}


def _next_occurrence(packet: dict, now: float) -> datetime | None:
    first = datetime.fromisoformat(packet["start_at"])
    zone = ZoneInfo(packet["timezone"])
    if packet["recurrence"] == "once":
        return DateTrigger(run_date=first, timezone=zone).get_next_fire_time(
            first, datetime.fromtimestamp(now, timezone.utc))
    trigger = CronTrigger(timezone=zone, start_date=first, hour=first.hour,
        minute=first.minute, second=first.second,
        day_of_week=first.weekday() if packet["recurrence"] == "weekly" else "*")
    reference = datetime.fromtimestamp(now, timezone.utc) + timedelta(seconds=1)
    # APScheduler handles calendar arithmetic. Skip nonexistent wall times;
    # duplicate fold occurrences are independently deduplicated in SQLite.
    for _ in range(8):
        candidate = trigger.get_next_fire_time(None, reference)
        if candidate is None:
            return None
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) == candidate.replace(tzinfo=None):
            return candidate
        reference = candidate.astimezone(timezone.utc) + timedelta(seconds=1)
    raise ValueError("reminder_calendar_progress_failed")


class ReminderStore:
    def __init__(self, path: Path | str | None = None, *, clock=time.time):
        self.path = Path(path) if path is not None else reminder_path()
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY, definition TEXT NOT NULL, digest TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL, next_run REAL, revision INTEGER NOT NULL DEFAULT 1,
                error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS reminder_occurrences (
                id INTEGER PRIMARY KEY, reminder_id TEXT NOT NULL, due_utc TEXT NOT NULL,
                wall_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                catch_up INTEGER NOT NULL, acknowledged_at REAL,
                UNIQUE(reminder_id,wall_key))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS reminder_history (
                id INTEGER PRIMARY KEY, reminder_id TEXT NOT NULL, operation TEXT NOT NULL,
                revision INTEGER NOT NULL, occurred_at REAL NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reminder_due ON reminders(state,next_run)")

    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=0.2, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def _history(self, conn, identity, operation, revision):
        conn.execute("INSERT INTO reminder_history(reminder_id,operation,revision,occurred_at) VALUES (?,?,?,?)",
                     (identity, operation, revision, self.clock()))

    def create(self, arguments: dict) -> dict:
        preview = reminder_preview(arguments, now=self.clock())
        if arguments.get("preview_digest") != preview["preview_digest"]:
            raise ValueError("reminder_preview_changed")
        definition = {key: preview[key] for key in ("title", "start_at", "timezone", "recurrence")}
        identity = "reminder_" + preview["preview_digest"][:24]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT id FROM reminders WHERE digest=?", (preview["preview_digest"],)).fetchone()
            if not exists:
                active = conn.execute("SELECT COUNT(*) FROM reminders WHERE state IN ('active','paused')").fetchone()[0]
                if active >= 100:
                    raise ValueError("reminder_active_limit_reached")
                conn.execute("INSERT INTO reminders(id,definition,digest,state,next_run,created_at) VALUES (?,?,?,'active',?,?)",
                    (identity, json.dumps(definition, ensure_ascii=False), preview["preview_digest"],
                     datetime.fromisoformat(preview["next_run_utc"]).timestamp(), self.clock()))
                self._history(conn, identity, "created", 1)
        return self.get(identity)

    def get(self, identity: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM reminders WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("reminder_not_found")
        return {"reminder_id": row["id"], **json.loads(row["definition"]), "state": row["state"],
                "revision": row["revision"], "error": row["error"],
                "next_run_utc": _utc(datetime.fromtimestamp(row["next_run"], timezone.utc)) if row["next_run"] is not None else None}

    def inspect(self, *, limit=30) -> dict:
        with self._connect() as conn:
            identities = conn.execute("SELECT id FROM reminders ORDER BY created_at DESC LIMIT ?",
                                      (max(1, min(int(limit), 50)),)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
            counts = dict(conn.execute("SELECT state,COUNT(*) FROM reminders GROUP BY state").fetchall())
        return {"reminders": [self.get(row["id"]) for row in identities], "counts": counts,
                "omitted": max(0, total - len(identities)), "notifications": self.pending(limit=limit),
                "read_only": True, "requires_app_open": True, "automatic_mission_execution": False}

    def set_state(self, identity: str, state: str, *, expected_revision: int) -> dict:
        if state not in {"active", "paused", "cancelled"} or type(expected_revision) is not int:
            raise ValueError("invalid_reminder_state_or_revision")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state,revision FROM reminders WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise KeyError("reminder_not_found")
            if row["revision"] != expected_revision:
                raise ValueError("reminder_revision_changed")
            if row["state"] in {"finished", "failed", "cancelled"}:
                raise ValueError("reminder_terminal_requires_new_schedule")
            if row["state"] != state:
                conn.execute("UPDATE reminders SET state=?,revision=revision+1 WHERE id=?", (state, identity))
                self._history(conn, identity, state, expected_revision + 1)
                if state == "cancelled":
                    conn.execute("UPDATE reminder_occurrences SET state='cancelled' WHERE reminder_id=? AND state='pending'", (identity,))
        return self.get(identity)

    def advance_due(self, *, limit=16) -> int:
        now = self.clock()
        produced = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM reminders WHERE state='active' AND next_run<=? ORDER BY next_run LIMIT ?",
                                (now, max(1, min(int(limit), 100)))).fetchall()
            for row in rows:
                try:
                    packet = json.loads(row["definition"])
                    due = datetime.fromtimestamp(row["next_run"], timezone.utc)
                    zone = ZoneInfo(packet["timezone"])
                    wall_key = due.astimezone(zone).replace(tzinfo=None).isoformat(timespec="seconds")
                    following = _next_occurrence(packet, now)
                    if following is not None and following.timestamp() <= now:
                        raise ValueError("reminder_calendar_did_not_advance")
                    inserted = conn.execute("""INSERT INTO reminder_occurrences(reminder_id,due_utc,wall_key,catch_up)
                        VALUES (?,?,?,?) ON CONFLICT(reminder_id,wall_key) DO NOTHING""",
                        (row["id"], _utc(due), wall_key, int(now - due.timestamp() > 5))).rowcount
                    produced += inserted
                    conn.execute("UPDATE reminders SET next_run=?,state=?,revision=revision+1 WHERE id=?",
                        (following.timestamp() if following else None, "active" if following else "finished", row["id"]))
                    self._history(conn, row["id"], "occurrence_pending" if inserted else "duplicate_wall_time_skipped", row["revision"] + 1)
                except (ValueError, KeyError, TypeError) as exc:
                    conn.execute("UPDATE reminders SET state='failed',error=?,revision=revision+1 WHERE id=?",
                                 (type(exc).__name__, row["id"]))
                    self._history(conn, row["id"], "failed", row["revision"] + 1)
        return produced

    def pending(self, *, limit=50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("""SELECT o.*,r.definition FROM reminder_occurrences o JOIN reminders r
                ON o.reminder_id=r.id WHERE o.state='pending' AND r.state NOT IN ('paused','cancelled')
                ORDER BY o.id LIMIT ?""", (max(1, min(int(limit), 500)),)).fetchall()
        return [{"occurrence_id": row["id"], "reminder_id": row["reminder_id"],
                 "title": json.loads(row["definition"])["title"], "due_utc": row["due_utc"],
                 "catch_up": bool(row["catch_up"]), "acknowledged": False} for row in rows]

    def acknowledge(self, occurrence_id: int) -> dict:
        if type(occurrence_id) is not int or occurrence_id < 1:
            raise ValueError("invalid_reminder_occurrence")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state,reminder_id FROM reminder_occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if row is None or row["state"] == "cancelled":
                raise ValueError("reminder_pending_occurrence_required")
            if row["state"] == "pending":
                conn.execute("UPDATE reminder_occurrences SET state='acknowledged',acknowledged_at=? WHERE id=?",
                             (self.clock(), occurrence_id))
                revision = conn.execute("SELECT revision FROM reminders WHERE id=?", (row["reminder_id"],)).fetchone()[0]
                self._history(conn, row["reminder_id"], "acknowledged", revision)
        return {"occurrence_id": occurrence_id, "acknowledged": True}


class ReminderService:
    def __init__(self, store: ReminderStore):
        self.store = store
        self._stop = threading.Event()
        self._thread = None
        self._emitted = set()
        self.last_error = ""

    def tick(self, notify) -> None:
        self.store.advance_due()
        pending = self.store.pending(limit=500)
        self._emitted.intersection_update(row["occurrence_id"] for row in pending)
        for item in pending:
            if item["occurrence_id"] not in self._emitted:
                notify(item)
                self._emitted.add(item["occurrence_id"])

    def start(self, notify) -> None:
        if self._thread is not None:
            return
        def run():
            while not self._stop.is_set():
                try:
                    self.tick(notify)
                    self.last_error = ""
                except Exception as exc:
                    self.last_error = type(exc).__name__
                self._stop.wait(1)
        self._thread = threading.Thread(target=run, name="jarvis-reminders", daemon=True)
        self._thread.start()

    def close(self) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(3)
        return self._thread is None or not self._thread.is_alive()


_CREATE = {"title": {"type": "string"}, "start_at": {"type": "string", "description": "Date ISO avec offset explicite, secondes entieres."},
           "timezone": {"type": "string", "description": "Fuseau IANA explicitement confirme, par exemple Africa/Lagos."},
           "recurrence": {"type": "string", "enum": ["once", "daily", "weekly"]}}


class ReminderToolRegistry:
    def __init__(self, delegate, service: ReminderService):
        self.delegate, self.reminder_service = delegate, service

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def ollama_tools(self):
        tools = list(self.delegate.ollama_tools())
        for name, description, properties, required in (
            ("reminder_preview", "Prepare un rappel LOCAL sans l'enregistrer. Clarifie la date/fuseau; aucune execution de mission.", _CREATE, ["title", "start_at", "timezone"]),
            ("reminder_create", "Enregistre le rappel previsualise, apres confirmation explicite. Notification dans Jarvis ouvert, pas un service Windows ni un evenement memoire.", {**_CREATE, "preview_digest": {"type": "string"}}, ["title", "start_at", "timezone", "preview_digest"]),
            ("reminder_list", "Lit les rappels locaux et notifications non acquittees, sans declencher d'action.", {}, []),
            ("reminder_set_state", "Suspend, reprend ou annule un rappel observe, apres confirmation; CAS sur la revision.",
             {"reminder_id": {"type": "string"}, "state": {"type": "string", "enum": ["active", "paused", "cancelled"]}, "expected_revision": {"type": "integer"}}, ["reminder_id", "state", "expected_revision"]),
            ("reminder_acknowledge", "Enregistre la reception d'une notification apres confirmation utilisateur; ne prouve pas l'accomplissement de son contenu.", {"occurrence_id": {"type": "integer"}}, ["occurrence_id"]),
        ):
            tools.append({"type": "function", "function": {"name": name, "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}}})
        return tools

    def openai_tools(self):
        return [{"type": "function", **item["function"], "strict": False} for item in self.ollama_tools()]

    def requires_confirmation(self, name):
        if name.startswith("reminder_"):
            return name not in {"reminder_preview", "reminder_list"}
        return self.delegate.requires_confirmation(name)

    def execute(self, name, arguments, *, approved=False):
        if not name.startswith("reminder_"):
            return self.delegate.execute(name, arguments, approved=approved)
        if self.requires_confirmation(name) and not approved:
            return AgentActionResult(name, False, "Confirmation du rappel requise.", detail='{"approval_required":true,"verified":false}')
        store = self.reminder_service.store
        try:
            if name == "reminder_preview":
                payload = reminder_preview(arguments, now=store.clock())
            elif name == "reminder_create":
                payload = store.create(arguments)
            elif name == "reminder_list":
                payload = store.inspect()
            elif name == "reminder_set_state":
                payload = store.set_state(arguments["reminder_id"], arguments["state"], expected_revision=arguments["expected_revision"])
            elif name == "reminder_acknowledge":
                payload = store.acknowledge(arguments["occurrence_id"])
            else:
                raise ValueError("unknown_reminder_tool")
            return AgentActionResult(name, True, "Etat local du rappel enregistre ou observe; aucune mission executee.",
                detail=json.dumps({**payload, "verified": False, "source": "local_reminder_store"}, ensure_ascii=False))
        except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            code = str(exc) if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__
            return AgentActionResult(name, False, "Rappel non confirme ; corriger ou clarifier la demande.",
                detail=json.dumps({"error": code[:100], "verified": False}, ensure_ascii=False))
