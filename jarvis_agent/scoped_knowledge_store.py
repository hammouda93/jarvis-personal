from __future__ import annotations

import json
import os
import sqlite3
from .sqlite_utils import ClosingConnection
import threading
from pathlib import Path

from .kernel_contracts import (
    KnowledgeIdentity,
    KnowledgeScope,
    SharingPolicy,
)
from .knowledge_broker import ScopedKnowledgeRecord


def _default_path() -> Path:
    root = Path(
        os.getenv("JARVIS_DATA_DIR") or os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "scoped_knowledge.sqlite3"


class SQLiteScopedKnowledgeBackend:
    """Persistent backend implementing the KnowledgeBroker storage contract."""

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
                CREATE TABLE IF NOT EXISTS scoped_knowledge (
                    knowledge_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    owner_user_id TEXT,
                    owner_agent_id TEXT,
                    organization_id TEXT,
                    app_id TEXT,
                    domain TEXT,
                    skill_id TEXT,
                    sharing_policy TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    relevance REAL NOT NULL DEFAULT 0.0
                );

                CREATE INDEX IF NOT EXISTS idx_scoped_knowledge_user
                    ON scoped_knowledge(owner_user_id, scope);
                CREATE INDEX IF NOT EXISTS idx_scoped_knowledge_agent
                    ON scoped_knowledge(owner_agent_id, scope);
                CREATE INDEX IF NOT EXISTS idx_scoped_knowledge_domain
                    ON scoped_knowledge(domain, scope);
                """
            )

    def upsert(self, record: ScopedKnowledgeRecord) -> None:
        identity = record.identity
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO scoped_knowledge (
                    knowledge_id, content, scope, owner_user_id,
                    owner_agent_id, organization_id, app_id, domain,
                    skill_id, sharing_policy, metadata_json, relevance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(knowledge_id) DO UPDATE SET
                    content=excluded.content,
                    scope=excluded.scope,
                    owner_user_id=excluded.owner_user_id,
                    owner_agent_id=excluded.owner_agent_id,
                    organization_id=excluded.organization_id,
                    app_id=excluded.app_id,
                    domain=excluded.domain,
                    skill_id=excluded.skill_id,
                    sharing_policy=excluded.sharing_policy,
                    metadata_json=excluded.metadata_json,
                    relevance=excluded.relevance
                """,
                (
                    record.knowledge_id,
                    record.content[:12000],
                    identity.scope.value,
                    identity.owner_user_id,
                    identity.owner_agent_id,
                    identity.organization_id,
                    identity.app_id,
                    identity.domain,
                    identity.skill_id,
                    identity.sharing_policy.value,
                    json.dumps(
                        dict(record.metadata),
                        ensure_ascii=False,
                    )[:12000],
                    max(0.0, min(float(record.relevance), 1.0)),
                ),
            )

    def search(
        self,
        query: str,
        *,
        limit: int,
    ) -> list[ScopedKnowledgeRecord]:
        terms = [
            token.lower()
            for token in str(query or "").split()
            if len(token.strip()) >= 2
        ]
        fetch_limit = max(20, min(int(limit) * 8, 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM scoped_knowledge
                ORDER BY relevance DESC, knowledge_id ASC
                LIMIT ?
                """,
                (fetch_limit,),
            ).fetchall()

        result: list[ScopedKnowledgeRecord] = []
        for row in rows:
            haystack = (
                str(row["content"] or "")
                + " "
                + str(row["metadata_json"] or "")
            ).lower()
            if terms and not any(term in haystack for term in terms):
                continue
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            identity = KnowledgeIdentity(
                scope=KnowledgeScope(row["scope"]),
                owner_user_id=row["owner_user_id"],
                owner_agent_id=row["owner_agent_id"],
                organization_id=row["organization_id"],
                app_id=row["app_id"],
                domain=row["domain"],
                skill_id=row["skill_id"],
                sharing_policy=SharingPolicy(row["sharing_policy"]),
            )
            result.append(
                ScopedKnowledgeRecord(
                    knowledge_id=row["knowledge_id"],
                    identity=identity,
                    content=row["content"],
                    metadata=metadata,
                    relevance=float(row["relevance"] or 0.0),
                )
            )
            if len(result) >= max(1, int(limit)):
                break
        return result
