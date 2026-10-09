from __future__ import annotations

import json
import os
import sqlite3
from .sqlite_utils import ClosingConnection
import threading
import time
from pathlib import Path
from typing import Any

from .kernel_contracts import (
    CorrectionCandidate,
    KnowledgeScope,
    PromotionTarget,
)


def _default_path() -> Path:
    root = Path(
        os.getenv("JARVIS_DATA_DIR") or os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "correction_candidates.sqlite3"


class CorrectionCandidateStore:
    """Persistent pending-feedback candidates.

    Validation and promotion are intentionally separate operations.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS correction_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    proposed_scope TEXT NOT NULL,
                    promotion_target TEXT NOT NULL,
                    user_id TEXT,
                    agent_id TEXT,
                    app_id TEXT,
                    domain TEXT,
                    skill_id TEXT,
                    source_event_ids_json TEXT NOT NULL,
                    test_ids_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    validated INTEGER NOT NULL DEFAULT 0,
                    rejected INTEGER NOT NULL DEFAULT 0,
                    promoted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    validated_at REAL,
                    promoted_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_correction_candidates_mission
                    ON correction_candidates(mission_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_correction_candidates_pending
                    ON correction_candidates(validated, rejected, promoted);
                """
            )

    def put(self, candidate: CorrectionCandidate) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO correction_candidates (
                    candidate_id, mission_id, summary, proposed_scope,
                    promotion_target, user_id, agent_id, app_id, domain,
                    skill_id, source_event_ids_json, test_ids_json,
                    evidence_json, validated, rejected, promoted,
                    created_at, validated_at, promoted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                          COALESCE(
                            (SELECT created_at
                             FROM correction_candidates
                             WHERE candidate_id=?),
                            ?
                          ),
                          NULL, NULL)
                """,
                (
                    candidate.candidate_id,
                    candidate.mission_id,
                    candidate.summary[:1600],
                    candidate.proposed_scope.value,
                    candidate.promotion_target.value,
                    candidate.user_id,
                    candidate.agent_id,
                    candidate.app_id,
                    candidate.domain,
                    candidate.skill_id,
                    json.dumps(
                        list(candidate.source_event_ids),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        list(candidate.test_ids),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        dict(candidate.evidence),
                        ensure_ascii=False,
                    )[:12000],
                    int(bool(candidate.validated)),
                    int(bool(candidate.rejected)),
                    candidate.candidate_id,
                    now,
                ),
            )

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM correction_candidates
                WHERE candidate_id=?
                """,
                (str(candidate_id),),
            ).fetchone()
        return self._row(row) if row else None

    def validate(
        self,
        candidate_id: str,
        *,
        accepted: bool,
    ) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE correction_candidates
                SET validated=?, rejected=?, validated_at=?
                WHERE candidate_id=? AND promoted=0
                """,
                (
                    int(bool(accepted)),
                    int(not bool(accepted)),
                    time.time(),
                    str(candidate_id),
                ),
            )
            return cursor.rowcount > 0

    def mark_promoted(self, candidate_id: str) -> bool:
        """Promotion is allowed only after explicit accepted validation."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE correction_candidates
                SET promoted=1, promoted_at=?
                WHERE candidate_id=?
                  AND validated=1
                  AND rejected=0
                  AND promoted=0
                """,
                (time.time(), str(candidate_id)),
            )
            return cursor.rowcount > 0

    def pending(
        self,
        *,
        mission_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = "validated=0 AND rejected=0 AND promoted=0"
        if mission_id is not None:
            where += " AND mission_id=?"
            params.append(str(mission_id))
        params.append(max(1, min(int(limit), 500)))

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM correction_candidates
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "candidate_id": row["candidate_id"],
            "mission_id": row["mission_id"],
            "summary": row["summary"],
            "proposed_scope": row["proposed_scope"],
            "promotion_target": row["promotion_target"],
            "user_id": row["user_id"],
            "agent_id": row["agent_id"],
            "app_id": row["app_id"],
            "domain": row["domain"],
            "skill_id": row["skill_id"],
            "source_event_ids": json.loads(
                row["source_event_ids_json"] or "[]"
            ),
            "test_ids": json.loads(row["test_ids_json"] or "[]"),
            "evidence": json.loads(row["evidence_json"] or "{}"),
            "validated": bool(row["validated"]),
            "rejected": bool(row["rejected"]),
            "promoted": bool(row["promoted"]),
            "created_at": row["created_at"],
            "validated_at": row["validated_at"],
            "promoted_at": row["promoted_at"],
        }
