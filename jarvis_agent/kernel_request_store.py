from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .kernel_contracts import KernelRequest, SyscallKind, SyscallStatus


def _default_path() -> Path:
    root = Path(
        os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "kernel_requests.sqlite3"


class KernelRequestStore:
    """Durable request ledger for restart-safe kernel recovery.

    RUNNING requests are intentionally not auto-replayed after a restart.
    Callers can surface them as recovery_required and verify external state
    before choosing retry/skip/repair.
    """

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
                CREATE TABLE IF NOT EXISTS kernel_requests (
                    request_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    step_id TEXT,
                    user_id TEXT,
                    agent_id TEXT NOT NULL,
                    syscall_kind TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    requires_approval INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_kernel_requests_status
                    ON kernel_requests(status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_kernel_requests_mission
                    ON kernel_requests(mission_id, updated_at);
                """
            )

    def put(
        self,
        request: KernelRequest,
        *,
        status: SyscallStatus,
        error: str = "",
    ) -> None:
        now = time.time()
        created_at = (
            float(request.created_at)
            if request.created_at is not None
            else now
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kernel_requests (
                    request_id, mission_id, step_id, user_id, agent_id,
                    syscall_kind, capability, priority, requires_approval,
                    payload_json, status, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    step_id=excluded.step_id,
                    user_id=excluded.user_id,
                    agent_id=excluded.agent_id,
                    syscall_kind=excluded.syscall_kind,
                    capability=excluded.capability,
                    priority=excluded.priority,
                    requires_approval=excluded.requires_approval,
                    payload_json=excluded.payload_json,
                    status=excluded.status,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    request.request_id,
                    request.mission_id,
                    request.step_id,
                    request.user_id,
                    request.agent_id,
                    request.syscall_kind.value,
                    request.capability,
                    int(request.priority),
                    int(bool(request.requires_approval)),
                    json.dumps(
                        dict(request.payload or {}),
                        ensure_ascii=False,
                    )[:20000],
                    status.value,
                    str(error or "")[:1200],
                    created_at,
                    now,
                ),
            )

    def mark(
        self,
        request_id: str,
        status: SyscallStatus,
        *,
        error: str = "",
    ) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE kernel_requests
                SET status=?, error=?, updated_at=?
                WHERE request_id=?
                """,
                (
                    status.value,
                    str(error or "")[:1200],
                    time.time(),
                    str(request_id),
                ),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _request(row: sqlite3.Row) -> KernelRequest:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        return KernelRequest(
            request_id=row["request_id"],
            mission_id=row["mission_id"],
            syscall_kind=SyscallKind(row["syscall_kind"]),
            capability=row["capability"],
            agent_id=row["agent_id"],
            payload=payload,
            step_id=row["step_id"],
            user_id=row["user_id"],
            priority=int(row["priority"]),
            requires_approval=bool(row["requires_approval"]),
            created_at=float(row["created_at"]),
        )

    def by_status(
        self,
        status: SyscallStatus,
        *,
        limit: int = 200,
    ) -> list[KernelRequest]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM kernel_requests
                WHERE status=?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    status.value,
                    max(1, min(int(limit), 2000)),
                ),
            ).fetchall()
        return [self._request(row) for row in rows]

    def recovery_required(self) -> list[KernelRequest]:
        return self.by_status(SyscallStatus.RUNNING)

    def for_mission(
        self,
        mission_id: str,
        *,
        statuses: tuple[SyscallStatus, ...] | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [str(mission_id)]
        where = "mission_id=?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            params.extend(status.value for status in statuses)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM kernel_requests
                WHERE {where}
                ORDER BY created_at ASC
                """,
                tuple(params),
            ).fetchall()
        return [
            {
                "request": self._request(row),
                "status": SyscallStatus(row["status"]),
                "error": row["error"],
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM kernel_requests
                WHERE request_id=?
                """,
                (str(request_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "request": self._request(row),
            "status": SyscallStatus(row["status"]),
            "error": row["error"],
            "updated_at": float(row["updated_at"]),
        }
