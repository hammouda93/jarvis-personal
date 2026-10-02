from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .kernel_contracts import MissionContext, MissionStatus


def _default_path() -> Path:
    root = Path(
        os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "mission_context.sqlite3"


class MissionContextStore:
    """Persistent mission state for pause/resume and long-running work."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mission_contexts (
                    mission_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    parent_mission_id TEXT,
                    user_id TEXT,
                    owner_agent_id TEXT,
                    goal_summary TEXT NOT NULL DEFAULT '',
                    state_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_mission_context_user_status
                    ON mission_contexts(user_id, status, updated_at);
                """
            )

    @staticmethod
    def _payload(context: MissionContext) -> str:
        return json.dumps(
            context.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def save(
        self,
        context: MissionContext,
        *,
        expected_version: int | None = None,
    ) -> int:
        """Insert/update with optional optimistic concurrency control."""
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT version, created_at FROM mission_contexts WHERE mission_id=?",
                (context.mission_id,),
            ).fetchone()

            if row is None:
                if expected_version not in (None, 0):
                    raise RuntimeError("mission_context_version_conflict")
                version = 1
                created_at = now
                conn.execute(
                    """
                    INSERT INTO mission_contexts (
                        mission_id, version, status, parent_mission_id, user_id,
                        owner_agent_id, goal_summary, state_json, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        context.mission_id,
                        version,
                        context.status.value,
                        context.parent_mission_id,
                        context.user_id,
                        context.owner_agent_id,
                        context.user_goal[:1000],
                        self._payload(context),
                        created_at,
                        now,
                    ),
                )
                return version

            current_version = int(row["version"])
            if (
                expected_version is not None
                and int(expected_version) != current_version
            ):
                raise RuntimeError("mission_context_version_conflict")

            version = current_version + 1
            conn.execute(
                """
                UPDATE mission_contexts
                SET version=?, status=?, parent_mission_id=?, user_id=?,
                    owner_agent_id=?, goal_summary=?, state_json=?, updated_at=?
                WHERE mission_id=?
                """,
                (
                    version,
                    context.status.value,
                    context.parent_mission_id,
                    context.user_id,
                    context.owner_agent_id,
                    context.user_goal[:1000],
                    self._payload(context),
                    now,
                    context.mission_id,
                ),
            )
            return version

    def load(
        self,
        mission_id: str,
    ) -> tuple[MissionContext, int] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT version, state_json
                FROM mission_contexts
                WHERE mission_id=?
                """,
                (str(mission_id),),
            ).fetchone()
        if row is None:
            return None

        raw = json.loads(row["state_json"] or "{}")
        raw["status"] = MissionStatus(
            raw.get("status") or MissionStatus.CREATED.value
        )
        context = MissionContext(**raw)
        return context, int(row["version"])

    def list_resumable(
        self,
        *,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        statuses = (
            MissionStatus.CREATED.value,
            MissionStatus.RUNNING.value,
            MissionStatus.WAITING_USER.value,
            MissionStatus.WAITING_EXTERNAL.value,
            MissionStatus.BLOCKED.value,
        )
        placeholders = ",".join("?" for _ in statuses)
        params: list[Any] = list(statuses)
        where = f"status IN ({placeholders})"
        if user_id is not None:
            where += " AND user_id=?"
            params.append(str(user_id))
        params.append(max(1, min(int(limit), 200)))

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT mission_id, version, status, parent_mission_id, user_id,
                       owner_agent_id, goal_summary, created_at, updated_at
                FROM mission_contexts
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_status(
        self,
        mission_id: str,
        status: MissionStatus,
    ) -> bool:
        now = time.time()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mission_contexts
                SET status=?, updated_at=?
                WHERE mission_id=?
                """,
                (status.value, now, str(mission_id)),
            )
            return cursor.rowcount > 0
