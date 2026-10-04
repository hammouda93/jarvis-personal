from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .kernel_contracts import EventKind, MissionStatus


_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token|password|secret)"
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_HOME_RE = re.compile(
    r"(?i)(?:[A-Z]:\\Users\\[^\\\s]+|/home/[^/\s]+|/Users/[^/\s]+)"
)


def _default_path() -> Path:
    root = Path(
        os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "mission_events.sqlite3"


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact structured trace payloads.

    This journal stores operational evidence, not hidden reasoning. Large raw
    model outputs and secrets should stay out of the trace.
    """
    if depth >= 5:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value
        text = _EMAIL_RE.sub("<email>", text)
        text = _HOME_RE.sub("<user-home>", text)
        if len(text) > 2400:
            text = text[:2400] + "…"
        return text
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            name = str(key)
            if _SECRET_RE.search(name):
                result[name] = "<redacted>"
            else:
                result[name] = _safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [
            _safe_value(item, depth=depth + 1)
            for item in list(value)[:80]
        ]
    return _safe_value(str(value), depth=depth + 1)


class StructuredEventJournal:
    """Local append-only mission/event trace inspired by AIOS syscalls."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    parent_mission_id TEXT,
                    user_id TEXT,
                    owner_agent_id TEXT,
                    goal_summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    parent_event_id TEXT,
                    kind TEXT NOT NULL,
                    agent_id TEXT,
                    component TEXT,
                    success INTEGER,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_mission_time
                    ON events(mission_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_events_kind_time
                    ON events(kind, created_at);
                """
            )

    def create_mission(
        self,
        *,
        goal_summary: str = "",
        user_id: str | None = None,
        owner_agent_id: str | None = None,
        parent_mission_id: str | None = None,
        mission_id: str | None = None,
    ) -> str:
        mission_id = mission_id or f"m_{uuid.uuid4().hex}"
        now = time.time()
        safe_goal = str(_safe_value(goal_summary or ""))[:600]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO missions (
                    mission_id, parent_mission_id, user_id, owner_agent_id,
                    goal_summary, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mission_id,
                    parent_mission_id,
                    user_id,
                    owner_agent_id,
                    safe_goal,
                    MissionStatus.CREATED.value,
                    now,
                    now,
                ),
            )
        self.append_event(
            mission_id=mission_id,
            kind=EventKind.MISSION_CREATED,
            agent_id=owner_agent_id,
            component="mission",
            payload={"goal_summary": safe_goal},
        )
        return mission_id

    def append_event(
        self,
        *,
        mission_id: str,
        kind: EventKind | str,
        payload: dict[str, Any] | None = None,
        agent_id: str | None = None,
        component: str | None = None,
        success: bool | None = None,
        parent_event_id: str | None = None,
        event_id: str | None = None,
    ) -> str:
        event_id = event_id or f"e_{uuid.uuid4().hex}"
        kind_value = kind.value if isinstance(kind, EventKind) else str(kind)
        safe_payload = _safe_value(payload or {})
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    event_id, mission_id, parent_event_id, kind, agent_id,
                    component, success, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    mission_id,
                    parent_event_id,
                    kind_value,
                    agent_id,
                    component,
                    None if success is None else int(bool(success)),
                    now,
                    json.dumps(safe_payload, ensure_ascii=False),
                ),
            )
            conn.execute(
                "UPDATE missions SET updated_at=? WHERE mission_id=?",
                (now, mission_id),
            )
        return event_id

    def set_status(
        self,
        mission_id: str,
        status: MissionStatus | str,
    ) -> None:
        value = status.value if isinstance(status, MissionStatus) else str(status)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE missions SET status=?, updated_at=? WHERE mission_id=?",
                (value, now, mission_id),
            )

    def finish_mission(
        self,
        mission_id: str,
        *,
        success: bool,
        summary: str = "",
        agent_id: str | None = None,
        final_status: MissionStatus | None = None,
    ) -> str:
        status = final_status or (
            MissionStatus.COMPLETED
            if success
            else MissionStatus.FAILED
        )
        self.set_status(mission_id, status)
        return self.append_event(
            mission_id=mission_id,
            kind=(
                EventKind.MISSION_COMPLETED
                if success
                else EventKind.MISSION_BLOCKED if status in {MissionStatus.BLOCKED, MissionStatus.WAITING_USER}
                else EventKind.MISSION_FAILED
            ),
            agent_id=agent_id,
            component="mission",
            success=success,
            payload={"summary": summary[:1200]},
        )

    def mission_trace(self, mission_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, parent_event_id, kind, agent_id, component,
                       success, created_at, payload_json
                FROM events
                WHERE mission_id=?
                ORDER BY created_at ASC, rowid ASC
                """,
                (mission_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "parent_event_id": row["parent_event_id"],
                    "kind": row["kind"],
                    "agent_id": row["agent_id"],
                    "component": row["component"],
                    "success": (
                        None
                        if row["success"] is None
                        else bool(row["success"])
                    ),
                    "created_at": row["created_at"],
                    "payload": payload,
                }
            )
        return result

    def recent_missions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT mission_id, parent_mission_id, user_id, owner_agent_id,
                       goal_summary, status, created_at, updated_at
                FROM missions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            missions = int(
                conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0]
            )
            events = int(
                conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            )
        return {"missions": missions, "events": events}
