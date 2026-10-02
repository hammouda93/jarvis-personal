from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .kernel_contracts import RiskLevel


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    mission_id: str
    request_id: str
    agent_id: str
    capability: str
    summary: str
    risk: RiskLevel
    status: ApprovalStatus
    created_at: float
    expires_at: float | None = None


def _default_path() -> Path:
    root = Path(
        os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "approvals.sqlite3"


class HumanApprovalManager:
    """Persistent approval state for external/destructive future actions."""

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
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    resolved_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_approvals_mission_status
                    ON approvals(mission_id, status, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_request
                    ON approvals(request_id);
                """
            )

    def create(
        self,
        *,
        mission_id: str,
        request_id: str,
        agent_id: str,
        capability: str,
        summary: str,
        risk: RiskLevel,
        ttl_s: float | None = 300.0,
    ) -> ApprovalRequest:
        now = time.time()
        expires_at = (
            None
            if ttl_s is None
            else now + max(1.0, float(ttl_s))
        )
        approval_id = f"a_{uuid.uuid4().hex}"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, mission_id, request_id, agent_id,
                    capability, summary, risk, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    mission_id,
                    request_id,
                    agent_id,
                    capability,
                    summary[:1200],
                    risk.value,
                    ApprovalStatus.PENDING.value,
                    now,
                    expires_at,
                ),
            )
        return ApprovalRequest(
            approval_id=approval_id,
            mission_id=mission_id,
            request_id=request_id,
            agent_id=agent_id,
            capability=capability,
            summary=summary[:1200],
            risk=risk,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=expires_at,
        )

    def _refresh_expiry(
        self,
        conn: sqlite3.Connection,
        approval_id: str,
    ) -> None:
        now = time.time()
        conn.execute(
            """
            UPDATE approvals
            SET status=?, resolved_at=?
            WHERE approval_id=?
              AND status=?
              AND expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (
                ApprovalStatus.EXPIRED.value,
                now,
                approval_id,
                ApprovalStatus.PENDING.value,
                now,
            ),
        )

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock, self._connect() as conn:
            self._refresh_expiry(conn, str(approval_id))
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id=?",
                (str(approval_id),),
            ).fetchone()
        return self._row_to_request(row) if row else None

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> bool:
        with self._lock, self._connect() as conn:
            self._refresh_expiry(conn, str(approval_id))
            status = (
                ApprovalStatus.APPROVED
                if approved
                else ApprovalStatus.DENIED
            )
            cursor = conn.execute(
                """
                UPDATE approvals
                SET status=?, resolved_at=?
                WHERE approval_id=? AND status=?
                """,
                (
                    status.value,
                    time.time(),
                    str(approval_id),
                    ApprovalStatus.PENDING.value,
                ),
            )
            return cursor.rowcount > 0

    def consume(self, approval_id: str) -> bool:
        """Consume an approval exactly once before executing the side effect."""
        with self._lock, self._connect() as conn:
            self._refresh_expiry(conn, str(approval_id))
            cursor = conn.execute(
                """
                UPDATE approvals
                SET status=?, resolved_at=?
                WHERE approval_id=? AND status=?
                """,
                (
                    ApprovalStatus.CONSUMED.value,
                    time.time(),
                    str(approval_id),
                    ApprovalStatus.APPROVED.value,
                ),
            )
            return cursor.rowcount > 0

    def pending(
        self,
        *,
        mission_id: str | None = None,
    ) -> list[ApprovalRequest]:
        with self._lock, self._connect() as conn:
            now = time.time()
            conn.execute(
                """
                UPDATE approvals
                SET status=?, resolved_at=?
                WHERE status=?
                  AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (
                    ApprovalStatus.EXPIRED.value,
                    now,
                    ApprovalStatus.PENDING.value,
                    now,
                ),
            )
            if mission_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM approvals
                    WHERE status=?
                    ORDER BY created_at ASC
                    """,
                    (ApprovalStatus.PENDING.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM approvals
                    WHERE status=? AND mission_id=?
                    ORDER BY created_at ASC
                    """,
                    (
                        ApprovalStatus.PENDING.value,
                        str(mission_id),
                    ),
                ).fetchall()
        return [self._row_to_request(row) for row in rows]

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=row["approval_id"],
            mission_id=row["mission_id"],
            request_id=row["request_id"],
            agent_id=row["agent_id"],
            capability=row["capability"],
            summary=row["summary"],
            risk=RiskLevel(row["risk"]),
            status=ApprovalStatus(row["status"]),
            created_at=float(row["created_at"]),
            expires_at=(
                None
                if row["expires_at"] is None
                else float(row["expires_at"])
            ),
        )
