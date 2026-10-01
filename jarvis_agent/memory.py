from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _default_db_path() -> Path:
    local = os.getenv("LOCALAPPDATA", "").strip()
    if local:
        root = Path(local) / "JarvisPersonal"
    else:
        root = Path.home() / ".jarvis_personal"
    root.mkdir(parents=True, exist_ok=True)
    return root / "memory.sqlite3"


@dataclass(frozen=True)
class MemoryItem:
    id: int
    content: str
    tags: str
    created_at: str


class LocalMemory:
    """Small local-first persistent memory.

    V2 intentionally starts with explicit text memory and SQLite. Semantic
    embeddings can be added later without changing the agent tool contract.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _default_db_path()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), timeout=5)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_created_at "
                "ON memories(created_at)"
            )

    def remember(self, content: str, *, tags: str = "") -> MemoryItem:
        text = (content or "").strip()
        if not text:
            raise ValueError("memory content is empty")

        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO memories(content, tags, created_at) VALUES (?, ?, ?)",
                (text, (tags or "").strip(), created_at),
            )
            memory_id = int(cursor.lastrowid)

        return MemoryItem(
            id=memory_id,
            content=text,
            tags=(tags or "").strip(),
            created_at=created_at,
        )

    def search(self, query: str, *, limit: int = 5) -> list[MemoryItem]:
        text = (query or "").strip()
        if not text:
            return []

        raw_words = [
            word.lower()
            for word in re.findall(r"[a-zA-ZÀ-ÿ0-9]+", text)
            if len(word.strip()) >= 2
        ]
        stopwords = {
            "quel", "quelle", "quels", "quelles", "que", "quoi", "qui",
            "est", "ce", "cet", "cette", "ces", "de", "du", "des", "le",
            "la", "les", "un", "une", "mon", "ma", "mes", "ton", "ta",
            "tes", "son", "sa", "ses", "notre", "nos", "votre", "vos",
            "leur", "leurs", "je", "tu", "il", "elle", "nous", "vous",
            "ils", "elles", "sur", "dans", "avec", "pour", "et", "ou",
            "au", "aux", "me", "te", "se", "moi", "toi", "rappelle",
            "rappelles", "souviens", "remember", "what", "which", "the",
            "a", "an", "of", "about", "my", "your",
        }
        words = [word for word in raw_words if word not in stopwords]
        if not words:
            words = raw_words or [text.lower()]

        clauses = []
        params: list[str | int] = []
        for word in words[:8]:
            clauses.append(
                "(LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)"
            )
            like = f"%{word}%"
            params.extend([like, like])

        sql = (
            "SELECT id, content, tags, created_at FROM memories WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id DESC LIMIT ?"
        )
        params.append(max(1, min(int(limit), 20)))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            MemoryItem(
                id=int(row[0]),
                content=str(row[1]),
                tags=str(row[2]),
                created_at=str(row[3]),
            )
            for row in rows
        ]


LOCAL_MEMORY = LocalMemory()
