from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def _default_path() -> Path:
    root = Path(
        os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "model_telemetry.sqlite3"


class ModelTelemetryStore:
    """Passive provider/model telemetry. It never routes requests itself."""

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
                CREATE TABLE IF NOT EXISTS model_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    task_class TEXT NOT NULL DEFAULT '',
                    latency_ms REAL,
                    success INTEGER NOT NULL,
                    error_kind TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    estimated_cost REAL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_model_calls_provider_time
                    ON model_calls(provider, created_at);
                CREATE INDEX IF NOT EXISTS idx_model_calls_task_time
                    ON model_calls(task_class, created_at);
                """
            )

    def record(
        self,
        *,
        provider: str,
        model: str,
        success: bool,
        latency_s: float | None = None,
        task_class: str = "",
        error_kind: str = "",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(metadata or {})
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_calls (
                    created_at, provider, model, task_class, latency_ms,
                    success, error_kind, input_tokens, output_tokens,
                    estimated_cost, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    str(provider),
                    str(model),
                    str(task_class or ""),
                    (
                        None
                        if latency_s is None
                        else max(0.0, float(latency_s) * 1000.0)
                    ),
                    int(bool(success)),
                    str(error_kind or "")[:120],
                    input_tokens,
                    output_tokens,
                    estimated_cost,
                    json.dumps(payload, ensure_ascii=False)[:4000],
                ),
            )

    def summary(
        self,
        *,
        task_class: str | None = None,
        since_hours: float = 24.0,
    ) -> list[dict[str, Any]]:
        cutoff = time.time() - max(0.0, float(since_hours)) * 3600.0
        where = "created_at >= ?"
        params: list[Any] = [cutoff]
        if task_class:
            where += " AND task_class = ?"
            params.append(str(task_class))

        query = f"""
            SELECT
                provider,
                model,
                COUNT(*) AS calls,
                SUM(success) AS successes,
                AVG(latency_ms) AS avg_latency_ms,
                SUM(CASE WHEN error_kind='rate_limit' THEN 1 ELSE 0 END)
                    AS rate_limits
            FROM model_calls
            WHERE {where}
            GROUP BY provider, model
            ORDER BY successes DESC, avg_latency_ms ASC
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            calls = int(row["calls"] or 0)
            successes = int(row["successes"] or 0)
            result.append(
                {
                    "provider": row["provider"],
                    "model": row["model"],
                    "calls": calls,
                    "successes": successes,
                    "success_rate": (
                        successes / calls if calls else 0.0
                    ),
                    "avg_latency_ms": (
                        None
                        if row["avg_latency_ms"] is None
                        else round(float(row["avg_latency_ms"]), 2)
                    ),
                    "rate_limits": int(row["rate_limits"] or 0),
                }
            )
        return result

    def stats(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            calls = int(
                conn.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0]
            )
        return {"model_calls": calls}
