from __future__ import annotations

import json
import os
import sqlite3
from .sqlite_utils import ClosingConnection
import threading
import time
from pathlib import Path

from .task_graph import MissionTaskGraph, TaskNode, TaskStatus


def _default_path() -> Path:
    root = Path(
        os.getenv("JARVIS_DATA_DIR") or os.getenv("LOCALAPPDATA")
        or os.getenv("XDG_STATE_HOME")
        or Path.home()
    )
    return root / "JarvisPersonal" / "task_graphs.sqlite3"


class TaskGraphStore:
    """Persistent DAG state for long-running mission pause/resume."""

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
                CREATE TABLE IF NOT EXISTS mission_task_graphs (
                    mission_id TEXT PRIMARY KEY,
                    graph_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    @staticmethod
    def _serialize(graph: MissionTaskGraph) -> str:
        payload = {
            "mission_id": graph.mission_id,
            "nodes": [
                {
                    "task_id": node.task_id,
                    "mission_id": node.mission_id,
                    "capability": node.capability,
                    "agent_id": node.agent_id,
                    "dependencies": sorted(node.dependencies),
                    "status": node.status.value,
                    "priority": node.priority,
                    "payload": dict(node.payload),
                    "result": dict(node.result),
                    "error": node.error,
                }
                for node in graph.nodes()
            ],
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def save(self, graph: MissionTaskGraph) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mission_task_graphs (
                    mission_id, graph_json, updated_at
                ) VALUES (?, ?, ?)
                ON CONFLICT(mission_id) DO UPDATE SET
                    graph_json=excluded.graph_json,
                    updated_at=excluded.updated_at
                """,
                (
                    graph.mission_id,
                    self._serialize(graph),
                    time.time(),
                ),
            )

    def load(self, mission_id: str) -> MissionTaskGraph | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT graph_json
                FROM mission_task_graphs
                WHERE mission_id=?
                """,
                (str(mission_id),),
            ).fetchone()
        if row is None:
            return None
        raw = json.loads(row["graph_json"] or "{}")
        graph = MissionTaskGraph(str(raw.get("mission_id") or mission_id))
        nodes = list(raw.get("nodes") or [])
        # Add all nodes first so dependency validation can resolve forward refs.
        for item in nodes:
            node = TaskNode(
                task_id=str(item["task_id"]),
                mission_id=str(item["mission_id"]),
                capability=str(item["capability"]),
                agent_id=str(item["agent_id"]),
                dependencies=set(item.get("dependencies") or []),
                status=TaskStatus(item.get("status") or TaskStatus.PENDING.value),
                priority=int(item.get("priority") or 100),
                payload=dict(item.get("payload") or {}),
                result=dict(item.get("result") or {}),
                error=str(item.get("error") or ""),
            )
            graph.add(node)
        return graph

    def delete(self, mission_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM mission_task_graphs WHERE mission_id=?",
                (str(mission_id),),
            )
            return cursor.rowcount > 0
