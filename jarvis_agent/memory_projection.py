"""Opt-in durable projection queue. Raw notes and jobs share the existing SQLite."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import threading
import time
import uuid

from .memory import MemoryItem
from .memory_core_store import _hash_raw


def projection_policy(interpreter) -> str:
    """Pin provider/model/endpoint consent, without persisting any API key."""
    providers = (interpreter._provider_chain() if hasattr(interpreter, "_provider_chain")
                 else [getattr(interpreter, "provider", type(interpreter).__name__)])
    policy = {"provider": getattr(interpreter, "provider", "fixture"),
              "model": getattr(interpreter, "model", ""),
              "allow_cloud": bool(getattr(interpreter, "allow_cloud", False)),
              "chain": list(providers)}
    if hasattr(interpreter, "_provider_config"):
        from .config import settings
        policy["endpoints"] = [settings.ollama_base_url.rstrip("/") if provider == "ollama"
                               else interpreter._provider_config(provider)[0] for provider in providers]
        policy["models"] = [interpreter._model_for(provider) for provider in providers]
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


class ProjectionQueue:
    def __init__(self, store, parser_version: str, policy_key: str):
        self.store, self.parser_version, self.policy_key = store, parser_version, policy_key
        with store._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS memory_projection_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL,
                raw_hash TEXT NOT NULL, parser_version TEXT NOT NULL, policy_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                lease_until REAL NOT NULL DEFAULT 0, owner TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL,
                UNIQUE(memory_id, raw_hash, parser_version, policy_key))""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_projection_ready ON "
                         "memory_projection_jobs(policy_key,parser_version,status,lease_until,id)")

    def remember(self, content: str, *, tags: str = "") -> MemoryItem:
        raw, tags = str(content or "").strip(), str(tags or "").strip()
        if not raw:
            raise ValueError("memory_content_empty")
        created = datetime.now(timezone.utc).isoformat()
        # Acknowledgement happens only after raw source, admission and job commit.
        with self.store._connect() as conn:
            cursor = conn.execute("INSERT INTO memories(content,tags,created_at) VALUES (?,?,?)",
                                  (raw, tags, created))
            identity = int(cursor.lastrowid)
            conn.execute("INSERT INTO memory_semantic_admission VALUES (?,?,?)",
                         (identity, "explicit", created))
            self._enqueue(conn, identity, raw)
        return MemoryItem(identity, raw, tags, created)

    def _enqueue(self, conn, memory_id, raw):
        return conn.execute("""INSERT INTO memory_projection_jobs
            (memory_id,raw_hash,parser_version,policy_key,updated_at) VALUES (?,?,?,?,?)
            ON CONFLICT(memory_id,raw_hash,parser_version,policy_key) DO NOTHING""",
            (memory_id, _hash_raw(raw), self.parser_version, self.policy_key, time.time())).rowcount

    def enqueue(self, memory_id: int) -> None:
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT content FROM memories WHERE id=?", (memory_id,)).fetchone()
            if row is None:
                raise KeyError("memory_not_found")
            self._enqueue(conn, memory_id, row[0])

    def enqueue_missing(self, *, limit: int = 256) -> int:
        """Explicit migration/recovery of old raw notes, never an eager read path."""
        queued = 0
        for item in self.store.all_memories():
            state = self.store.semantic_state(item.id)
            if state and (state["raw_hash"] == _hash_raw(item.content)
                          and state["parser_version"] == self.parser_version
                          and state["status"] == "indexed"):
                continue
            with self.store._connect() as conn:
                queued += self._enqueue(conn, item.id, item.content)
            if queued >= max(1, min(limit, 256)):
                break
        return queued

    def claim(self, *, now: float | None = None, lease_seconds: float = 300) -> dict | None:
        instant = time.time() if now is None else now
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.row_factory = sqlite3.Row
            conn.execute("""UPDATE memory_projection_jobs SET status='failed',
                error='projection_lease_retry_budget_exhausted',updated_at=?
                WHERE status='running' AND lease_until<=? AND attempts>=3
                AND policy_key=? AND parser_version=?""",
                (instant, instant, self.policy_key, self.parser_version))
            row = conn.execute("""SELECT * FROM memory_projection_jobs
                WHERE policy_key=? AND parser_version=? AND
                (status='queued' OR (status='running' AND lease_until<=? AND attempts<3))
                ORDER BY id LIMIT 1""", (self.policy_key, self.parser_version, instant)).fetchone()
            if row is None:
                return None
            owner = uuid.uuid4().hex
            conn.execute("""UPDATE memory_projection_jobs SET status='running',owner=?,
                lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?""",
                (owner, instant + max(1, lease_seconds), instant, row["id"]))
            return {**dict(row), "owner": owner, "attempts": row["attempts"] + 1}

    def fail(self, job: dict, error: str) -> None:
        diagnostic = str(error or "projection_failed")[:1000]
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("""UPDATE memory_projection_jobs SET status='failed',error=?,
                updated_at=?,lease_until=0 WHERE id=? AND owner=? AND status='running'""",
                (diagnostic, time.time(), job["id"], job["owner"])).rowcount
            raw = conn.execute("SELECT content FROM memories WHERE id=?", (job["memory_id"],)).fetchone()
            if changed and raw and _hash_raw(raw[0]) == job["raw_hash"]:
                conn.execute("""INSERT INTO memory_semantic_state
                    VALUES (?,?,?,'error',?,?) ON CONFLICT(memory_id) DO UPDATE SET
                    raw_hash=excluded.raw_hash,parser_version=excluded.parser_version,
                    status='error',error=excluded.error,projected_at=excluded.projected_at""",
                    (job["memory_id"], job["raw_hash"], job["parser_version"], diagnostic,
                     datetime.now(timezone.utc).isoformat()))

    def retry_failed(self, *, max_attempts: int = 3) -> int:
        """Explicit bounded operator recovery; failed jobs never retry by themselves."""
        if not 1 <= max_attempts <= 10:
            raise ValueError("projection_retry_budget_requires_1_to_10")
        with self.store._connect() as conn:
            cursor = conn.execute("""UPDATE memory_projection_jobs SET status='queued',owner='',
                lease_until=0,updated_at=? WHERE status='failed' AND attempts<?
                AND policy_key=? AND parser_version=?""",
                (time.time(), max_attempts, self.policy_key, self.parser_version))
            return cursor.rowcount

    def status(self) -> dict:
        with closing(self.store._connect()) as conn:
            counts = conn.execute("SELECT status,COUNT(*) FROM memory_projection_jobs GROUP BY status").fetchall()
        return {str(name): int(count) for name, count in counts}

    def diagnostics(self, *, limit: int = 10) -> list[dict]:
        with closing(self.store._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""SELECT id,memory_id,status,attempts,error,
                policy_key,parser_version FROM memory_projection_jobs ORDER BY id DESC LIMIT ?""",
                (max(1, min(limit, 50)),)).fetchall()
        return [{"job_id": row["id"], "memory_id": row["memory_id"], "status": row["status"],
                 "attempts": row["attempts"], "error": row["error"],
                 "policy_matches": row["policy_key"] == self.policy_key
                                   and row["parser_version"] == self.parser_version}
                for row in rows]


class ProjectionWorker:
    def __init__(self, engine):
        self.engine = engine
        self.queue = ProjectionQueue(engine.store, engine.parser_version, projection_policy(engine.interpreter))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.last_error = ""

    def process_one(self) -> bool:
        job = self.queue.claim()
        if job is None:
            return False
        try:
            source = self.engine.store.get_memory(job["memory_id"])
            if source is None or _hash_raw(source.content) != job["raw_hash"]:
                raise RuntimeError("projection_source_changed")
            provenance = self.engine.store.memory_provenance(job["memory_id"]) or "legacy"
            self.engine.project_memory(job["memory_id"], provenance=provenance, projection_job=job)
        except Exception as exc:
            self.queue.fail(job, f"{type(exc).__name__}: {exc}")
        return True

    def start(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="jarvis-memory-projection", daemon=True)
                self._thread.start()
            self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.process_one():
                    continue
            except Exception as exc:
                self.last_error = type(exc).__name__
            self._wake.wait(1)
            self._wake.clear()

    def close(self, *, timeout: float = 15) -> bool:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True
