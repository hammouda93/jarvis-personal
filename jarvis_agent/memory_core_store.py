"""Semantic sidecar index for the existing local memory SQLite database."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .memory import MemoryItem, LocalMemory
from .semantic_memory import (
    MemoryProjection,
    SemanticFactRecord,
)


def _hash_raw(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class MemoryCoreStore(LocalMemory):
    """Original raw memory plus a versioned, rebuildable semantic sidecar."""

    def _connect(self):
        return sqlite3.connect(
            str(self.db_path),
            timeout=5,
            factory=_ClosingConnection,
        )

    def _init_db(self) -> None:
        super()._init_db()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_semantic_facts (
                    fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    object_value TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    qualifiers_json TEXT NOT NULL DEFAULT '{}',
                    scope TEXT NOT NULL DEFAULT 'global',
                    cardinality TEXT NOT NULL DEFAULT 'unknown',
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    parser_version TEXT NOT NULL,
                    provenance TEXT NOT NULL DEFAULT 'legacy',
                    raw_hash TEXT NOT NULL,
                    projected_at TEXT NOT NULL,
                    UNIQUE(memory_id, ordinal, parser_version)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_semantic_admission (
                    memory_id INTEGER PRIMARY KEY,
                    provenance TEXT NOT NULL,
                    admitted_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_semantic_state (
                    memory_id INTEGER PRIMARY KEY,
                    raw_hash TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    projected_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_semantic_relation "
                "ON memory_semantic_facts(relation, scope, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_semantic_memory "
                "ON memory_semantic_facts(memory_id, status)"
            )

    def mark_admission(
        self,
        memory_id: int,
        *,
        provenance: str,
    ) -> None:
        if self.get_memory(memory_id) is None:
            raise KeyError(f"memory_not_found:{memory_id}")
        value = str(provenance or "").strip() or "legacy"
        admitted_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_semantic_admission(
                    memory_id, provenance, admitted_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    provenance=excluded.provenance,
                    admitted_at=excluded.admitted_at
                """,
                (int(memory_id), value, admitted_at),
            )

    def memory_provenance(self, memory_id: int) -> str:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT provenance FROM memory_semantic_admission "
                "WHERE memory_id = ?",
                (int(memory_id),),
            ).fetchone()
        return str(row[0]) if row else ""

    def get_memory(self, memory_id: int) -> MemoryItem | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT id, content, tags, created_at "
                "FROM memories WHERE id = ?",
                (int(memory_id),),
            ).fetchone()
        if not row:
            return None
        return MemoryItem(
            id=int(row[0]),
            content=str(row[1]),
            tags=str(row[2]),
            created_at=str(row[3]),
        )

    def all_memories(self) -> list[MemoryItem]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, content, tags, created_at "
                "FROM memories ORDER BY id ASC"
            ).fetchall()
        return [
            MemoryItem(
                id=int(row[0]),
                content=str(row[1]),
                tags=str(row[2]),
                created_at=str(row[3]),
            )
            for row in rows
        ]

    def recent_memories(self, *, limit: int = 20) -> list[MemoryItem]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, content, tags, created_at "
                "FROM memories ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [
            MemoryItem(
                id=int(row[0]),
                content=str(row[1]),
                tags=str(row[2]),
                created_at=str(row[3]),
            )
            for row in rows
        ]

    def pending_projection_items(
        self,
        parser_version: str,
        *,
        limit: int = 64,
    ) -> list[MemoryItem]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT m.id, m.content, m.tags, m.created_at,
                       s.raw_hash, s.parser_version, s.status
                FROM memories AS m
                LEFT JOIN memory_semantic_state AS s
                    ON s.memory_id = m.id
                ORDER BY m.id ASC
                """
            ).fetchall()

        result: list[MemoryItem] = []
        for row in rows:
            current_hash = _hash_raw(str(row[1]))
            state_hash = str(row[4] or "")
            state_version = str(row[5] or "")
            state_status = str(row[6] or "")
            if (
                current_hash != state_hash
                or state_version != parser_version
                or state_status not in {"indexed", "unprojected", "error"}
            ):
                result.append(
                    MemoryItem(
                        id=int(row[0]),
                        content=str(row[1]),
                        tags=str(row[2]),
                        created_at=str(row[3]),
                    )
                )
            if len(result) >= max(1, min(int(limit), 256)):
                break
        return result

    def save_projection(
        self,
        memory_id: int,
        projections: list[MemoryProjection] | tuple[MemoryProjection, ...],
        *,
        parser_version: str,
        provenance: str,
    ) -> None:
        item = self.get_memory(memory_id)
        if item is None:
            raise KeyError(f"memory_not_found:{memory_id}")
        raw_hash = _hash_raw(item.content)
        projected_at = datetime.now(timezone.utc).isoformat()
        facts = list(projections)
        if provenance == "explicit":
            self.mark_admission(
                memory_id,
                provenance="explicit",
            )

        with self._connect() as conn:
            conn.execute(
                "DELETE FROM memory_semantic_facts WHERE memory_id = ?",
                (int(memory_id),),
            )
            for ordinal, projection in enumerate(facts):
                conn.execute(
                    """
                    INSERT INTO memory_semantic_facts(
                        memory_id, ordinal, subject, relation, object_value,
                        kind, qualifiers_json, scope, cardinality, confidence,
                        status, parser_version, provenance, raw_hash, projected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        int(memory_id),
                        int(ordinal),
                        projection.subject,
                        projection.relation,
                        projection.value,
                        projection.kind,
                        json.dumps(
                            projection.qualifiers,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        projection.scope,
                        projection.cardinality,
                        float(projection.confidence),
                        parser_version,
                        str(provenance or "legacy"),
                        raw_hash,
                        projected_at,
                    ),
                )
            if provenance == "explicit":
                for projection in facts:
                    if (
                        projection.cardinality != "single"
                        or projection.confidence < 0.85
                    ):
                        continue
                    qualifiers_json = json.dumps(
                        projection.qualifiers,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    conn.execute(
                        """
                        UPDATE memory_semantic_facts
                        SET status = 'superseded'
                        WHERE memory_id != ?
                          AND status = 'active'
                          AND subject = ?
                          AND relation = ?
                          AND scope = ?
                          AND qualifiers_json = ?
                          AND cardinality = 'single'
                        """,
                        (
                            int(memory_id),
                            projection.subject,
                            projection.relation,
                            projection.scope,
                            qualifiers_json,
                        ),
                    )

            conn.execute(
                """
                INSERT INTO memory_semantic_state(
                    memory_id, raw_hash, parser_version, status, error,
                    projected_at
                ) VALUES (?, ?, ?, ?, '', ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    raw_hash=excluded.raw_hash,
                    parser_version=excluded.parser_version,
                    status=excluded.status,
                    error='',
                    projected_at=excluded.projected_at
                """,
                (
                    int(memory_id),
                    raw_hash,
                    parser_version,
                    "indexed" if facts else "unprojected",
                    projected_at,
                ),
            )

    def mark_projection_error(
        self,
        memory_id: int,
        *,
        parser_version: str,
        error: str,
    ) -> None:
        item = self.get_memory(memory_id)
        if item is None:
            return
        projected_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_semantic_state(
                    memory_id, raw_hash, parser_version, status, error,
                    projected_at
                ) VALUES (?, ?, ?, 'error', ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    raw_hash=excluded.raw_hash,
                    parser_version=excluded.parser_version,
                    status='error',
                    error=excluded.error,
                    projected_at=excluded.projected_at
                """,
                (
                    int(memory_id),
                    _hash_raw(item.content),
                    parser_version,
                    str(error or "")[:1000],
                    projected_at,
                ),
            )

    def semantic_facts(
        self,
        *,
        scope: str | None = None,
        status: str | None = "active",
    ) -> list[SemanticFactRecord]:
        clauses = []
        params: list[object] = []
        if status is not None:
            clauses.append("f.status = ?")
            params.append(status)
        if scope:
            if scope == "global":
                clauses.append("f.scope = 'global'")
            else:
                clauses.append("f.scope IN (?, 'global')")
                params.append(scope)
        where = (
            " WHERE " + " AND ".join(clauses)
            if clauses
            else ""
        )
        sql = (
            "SELECT f.fact_id, f.memory_id, f.ordinal, f.subject, f.relation, "
            "f.object_value, f.kind, f.qualifiers_json, f.scope, "
            "f.cardinality, f.confidence, f.provenance, f.parser_version, "
            "f.status, m.content, m.created_at "
            "FROM memory_semantic_facts AS f "
            "JOIN memories AS m ON m.id = f.memory_id"
            + where
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()

        result: list[SemanticFactRecord] = []
        for row in rows:
            try:
                qualifiers = json.loads(str(row[7] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                qualifiers = {}
            projection = MemoryProjection(
                subject=str(row[3]),
                relation=str(row[4]),
                value=str(row[5]),
                kind=str(row[6]),
                qualifiers=qualifiers if isinstance(qualifiers, dict) else {},
                scope=str(row[8]),
                cardinality=str(row[9]),
                confidence=float(row[10]),
            )
            result.append(
                SemanticFactRecord(
                    fact_id=int(row[0]),
                    memory_id=int(row[1]),
                    ordinal=int(row[2]),
                    projection=projection,
                    provenance=str(row[11]),
                    parser_version=str(row[12]),
                    status=str(row[13]),
                    raw_content=str(row[14]),
                    created_at=str(row[15]),
                )
            )
        return result

    def semantic_summary(self) -> dict[str, object]:
        with closing(self._connect()) as conn:
            raw_count = int(
                conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            )
            fact_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM memory_semantic_facts"
                ).fetchone()[0]
            )
            status_rows = conn.execute(
                "SELECT status, COUNT(*) FROM memory_semantic_state "
                "GROUP BY status"
            ).fetchall()
            admission_rows = conn.execute(
                "SELECT provenance, COUNT(*) "
                "FROM memory_semantic_admission GROUP BY provenance"
            ).fetchall()
        return {
            "raw_memories": raw_count,
            "semantic_facts": fact_count,
            "projection_states": {
                str(row[0]): int(row[1])
                for row in status_rows
            },
            "admissions": {
                str(row[0]): int(row[1])
                for row in admission_rows
            },
        }

    def reset_semantic_index(self, *, errors_only: bool = False) -> None:
        """Clear only rebuildable semantic sidecar state; raw memories survive."""
        with self._connect() as conn:
            if errors_only:
                ids = [
                    int(row[0])
                    for row in conn.execute(
                        "SELECT memory_id FROM memory_semantic_state "
                        "WHERE status = 'error'"
                    ).fetchall()
                ]
                for memory_id in ids:
                    conn.execute(
                        "DELETE FROM memory_semantic_facts "
                        "WHERE memory_id = ?",
                        (memory_id,),
                    )
                    conn.execute(
                        "DELETE FROM memory_semantic_state "
                        "WHERE memory_id = ?",
                        (memory_id,),
                    )
            else:
                conn.execute("DELETE FROM memory_semantic_facts")
                conn.execute("DELETE FROM memory_semantic_state")

    def semantic_state(self, memory_id: int) -> dict[str, str] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT raw_hash, parser_version, status, error, projected_at "
                "FROM memory_semantic_state WHERE memory_id = ?",
                (int(memory_id),),
            ).fetchone()
        if not row:
            return None
        return {
            "raw_hash": str(row[0]),
            "parser_version": str(row[1]),
            "status": str(row[2]),
            "error": str(row[3]),
            "projected_at": str(row[4]),
        }
