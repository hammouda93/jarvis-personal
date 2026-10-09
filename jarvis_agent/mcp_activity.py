"""Atomic per-server MCP quotas and minimal activity, never tool arguments.

Reservations survive restarts. Quotas count logical sessions/tool attempts in
fixed UTC hours, not monetary charges or every HTTP packet inside the SDK.
"""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import time

from .sqlite_utils import ClosingConnection


class MCPBudgetExceeded(RuntimeError):
    pass


def quota_limits(entry: dict) -> dict:
    quotas = entry.get("quotas") or {}
    result = {}
    for key, default, cap in (("sessions_per_hour", 100, 200), ("calls_per_hour", 60, 1000)):
        value = quotas.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= cap:
            raise ValueError("invalid_mcp_quota")
        result[key] = value
    return result


class MCPActivityStore:
    def __init__(self, path: str | Path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=5, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS usage (server TEXT, hour INTEGER, sessions INTEGER, calls INTEGER, PRIMARY KEY(server,hour))")
            conn.execute("CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, server TEXT, operation TEXT, tool TEXT, started REAL, ended REAL, outcome TEXT)")
        except Exception:
            conn.close()
            raise
        return conn

    @staticmethod
    def _server(server):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,35}", server):
            raise ValueError("invalid_mcp_server_id")

    def reserve(self, server: str, operation: str, entry: dict, *, tool="") -> int:
        self._server(server)
        if operation not in {"call", "discover", "oauth_authorize"}:
            raise ValueError("invalid_mcp_activity_operation")
        if tool and not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", tool):
            raise ValueError("invalid_mcp_tool_name")
        limits = quota_limits(entry)
        now = self.clock()
        hour = int(now // 3600)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute("SELECT ended,outcome FROM attempts WHERE server=? ORDER BY id DESC LIMIT 1", (server,)).fetchone()
            if last and last["ended"] is not None and last["outcome"] in {"failed", "unknown", "preflight_failed"} and now - last["ended"] < 15:
                raise MCPBudgetExceeded("mcp_failure_cooldown")
            row = conn.execute("SELECT sessions,calls FROM usage WHERE server=? AND hour=?", (server, hour)).fetchone()
            sessions, calls = (row["sessions"], row["calls"]) if row else (0, 0)
            if sessions >= limits["sessions_per_hour"] or (operation == "call" and calls >= limits["calls_per_hour"]):
                raise MCPBudgetExceeded("mcp_hourly_quota_exhausted")
            conn.execute("INSERT INTO usage VALUES (?,?,?,?) ON CONFLICT(server,hour) DO UPDATE SET sessions=excluded.sessions,calls=excluded.calls",
                         (server, hour, sessions + 1, calls + int(operation == "call")))
            cursor = conn.execute("INSERT INTO attempts(server,operation,tool,started,outcome) VALUES (?,?,?,?,?)",
                                  (server, operation, tool, now, "reserved"))
            attempt = cursor.lastrowid
            conn.execute("DELETE FROM usage WHERE hour < ?", (hour - 24,))
            conn.execute("DELETE FROM attempts WHERE started < ? AND id NOT IN (SELECT id FROM attempts ORDER BY id DESC LIMIT 100)", (now - 86400,))
            return attempt

    def finish(self, attempt: int, outcome: str):
        if outcome not in {"success", "failed", "unknown", "preflight_failed", "cancelled"}:
            raise ValueError("invalid_mcp_activity_outcome")
        with self._connect() as conn:
            changed = conn.execute("UPDATE attempts SET ended=?,outcome=? WHERE id=? AND outcome='reserved'",
                                   (self.clock(), outcome, attempt)).rowcount
            if changed != 1:
                raise ValueError("mcp_attempt_already_finished")

    def summary(self, server: str) -> dict:
        self._server(server)
        result = {"sessions_used": 0, "calls_used": 0, "last_attempt_utc": "", "last_outcome": "", "recent": []}
        if not self.path.exists():
            return result
        from datetime import datetime, timezone
        with self._connect() as conn:
            usage = conn.execute("SELECT sessions,calls FROM usage WHERE server=? AND hour=?", (server, int(self.clock() // 3600))).fetchone()
            rows = conn.execute("SELECT operation,tool,started,ended,outcome FROM attempts WHERE server=? ORDER BY id DESC LIMIT 8", (server,)).fetchall()
        if usage:
            result.update(sessions_used=usage["sessions"], calls_used=usage["calls"])
        for row in rows:
            result["recent"].append({"operation": row["operation"], "tool": row["tool"],
                "utc": datetime.fromtimestamp(row["started"], timezone.utc).isoformat(timespec="seconds"),
                "outcome": row["outcome"]})
        if rows:
            result.update(last_attempt_utc=result["recent"][0]["utc"], last_outcome=rows[0]["outcome"])
        return result
