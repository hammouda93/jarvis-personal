"""Read-only, bounded operator telemetry for the existing Jarvis runtime.

No model calls, no tool execution, no key loading, and no writes.
A missing/disabled subsystem is never represented as active.
"""
from __future__ import annotations

import json
import os
from contextlib import closing
import sqlite3
import time
from pathlib import Path
from typing import Any


def _flag(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


def _state_root() -> Path:
    env = os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME")
    return Path(env) if env else Path.home()


def _action_path() -> Path:
    root = os.getenv("JARVIS_HERMES_RELIABILITY_DIR")
    if root:
        return Path(root) / "actions.sqlite3"
    env = os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME")
    home = Path(env) / "JarvisPersonal" / "action_reliability" if env else Path.home() / ".jarvis_personal" / "action_reliability"
    return home / "actions.sqlite3"


def _mission_root() -> Path:
    env = os.getenv("JARVIS_RUNTIME_CONVERGENCE_DIR")
    return Path(env) if env else _state_root() / "JarvisPersonal" / "runtime_convergence"


def _readonly(db: Path, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Tiny read-only queries with bounded SQLite busy timeout; never initialize DB."""
    if not db.is_file():
        return []
    try:
        # The URI is platform aware and never opens SQLite in write mode.
        uri = db.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.12)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            return [dict(row) for row in con.execute(sql, params).fetchall()]
    except (OSError, sqlite3.Error, ValueError):
        return []


def snapshot(*, max_items: int = 7) -> dict[str, Any]:
    """Local UI projection. Displays *recorded* events, never hypothetical ones."""
    limit = max(1, min(20, int(max_items)))
    reliability_on = _flag("JARVIS_HERMES_RELIABILITY_ENABLED")
    mission_on = _flag("JARVIS_RUNTIME_CONVERGENCE_ENABLED")
    rows = _readonly(
        _action_path(),
        "SELECT action_id,turn_id,name,status,success,verified,created_at, "
        "CASE WHEN status='guarded' THEN evidence_ref ELSE '' END AS guard_reason "
        "FROM actions ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (limit,),
    ) if reliability_on else []
    actions = [
        {
            "id": str(row["action_id"])[:40],
            "turn_id": str(row["turn_id"])[:40],
            "tool": str(row["name"])[:100],
            "status": str(row["status"])[:35],
            "verified": row["verified"] == 1,
            "success": row["success"] == 1,
            "guard_reason": str(row["guard_reason"] or "")[:50],
            "when": str(row["created_at"])[:35],
        }
        for row in rows
    ]
    user_id = os.getenv("JARVIS_KERNEL_SHADOW_USER_ID") or "local-user"
    records = _readonly(
        _mission_root() / "mission_context.sqlite3",
        "SELECT mission_id,goal_summary,status,updated_at,state_json "
        "FROM mission_contexts WHERE user_id=? "
        "ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit),
    ) if mission_on else []
    mission_records = records
    missions = []
    for row in records:
        try:
            state = json.loads(row.get("state_json") or "{}")
        except (TypeError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        observed = state.get("observed_state") or {}
        if not isinstance(observed, dict):
            observed = {}
        pending = state.get("pending_action") or {}
        if not isinstance(pending, dict):
            pending = {}
        missions.append({
            "id": str(row["mission_id"])[:70],
            "goal": str(row["goal_summary"])[:150],
            "status": str(row["status"])[:35],
            "verified": observed.get("goal_verified") is True,
            "needs_review": pending.get("manual_review_required") is True,
            "updated_at": float(row["updated_at"]),
        })
    # Only events belonging to the current user-owned visible missions.
    events = []
    if missions:
        mission_ids = [mission["id"] for mission in missions]
        query_args = ",".join("?" for _ in mission_ids)
        records = _readonly(
            _mission_root() / "mission_events.sqlite3",
            "SELECT mission_id,kind,component,success,created_at "
            "FROM events WHERE mission_id IN (" + query_args + ") "
            "ORDER BY created_at DESC LIMIT ?",
            tuple(mission_ids) + (limit,),
        )
        events = [
            {
                "mission_id": str(row["mission_id"])[:65],
                "kind": str(row["kind"])[:90],
                "component": str(row["component"] or "")[:80],
                "success": (
                    True if row["success"] == 1
                    else False if row["success"] == 0
                    else None
                ),
            }
            for row in records
        ]
    current_tasks = []
    if missions:
        graph = _readonly(
            _mission_root() / "task_graphs.sqlite3",
            "SELECT graph_json FROM mission_task_graphs WHERE mission_id=?",
            (missions[0]["id"],),
        )
        if graph:
            try:
                nodes = json.loads(graph[0]["graph_json"]).get("nodes", [])[-limit:]
            except (ValueError, AttributeError, TypeError):
                nodes = []
            for node in nodes:
                if isinstance(node, dict):
                    current_tasks.append({
                        "name": str(node.get("task_id") or "")[:50],
                        "capability": str(node.get("capability") or "")[:65],
                        "status": str(node.get("status") or "")[:35],
                        "tools": [str(x)[:50] for x in (node.get("result") or {}).get("action_names", [])[:5]],
                    })
    supervisor = None
    if missions:
        # Recompute from the same read-only SQLite mission/graph snapshot.
        # No model invocation and no "success" inferred from an action count.
        from .semantic_goal_supervisor import evaluate_mission

        current_raw = mission_records[0] if mission_records else {}
        try:
            current_state = json.loads(current_raw.get("state_json") or "{}")
        except (ValueError, TypeError, AttributeError):
            current_state = {}
        if not isinstance(current_state, dict):
            current_state = {}
        expected = current_state.get("expected_state") or {}
        observed = current_state.get("observed_state") or {}
        if not isinstance(expected, dict):
            expected = {}
        if not isinstance(observed, dict):
            observed = {}
        supervisor = evaluate_mission({
            "mission_id": missions[0]["id"],
            "status": missions[0]["status"],
            "goal_verified": missions[0]["verified"],
            "manual_review_required": missions[0]["needs_review"],
            "semantic_plan": expected.get("semantic_contract"),
            # Only requirement keys reach the UI; never the private proof refs.
            "plan_evidence": {
                str(key): "recorded"
                for key, val in (observed.get("plan_evidence") or {}).items()
                if str(val or "").strip()
            } if isinstance(observed.get("plan_evidence"), dict) else {},
            "tasks": [
                {"status": item.get("status"),
                 "action_names": item.get("tools", [])}
                for item in current_tasks
            ],
        })
    unknown = sum(row["status"] in ("unknown", "dispatched") for row in actions)
    # Numeric summaries only: never read or expose personal memory content,
    # skill procedures, knowledge lessons or learned application identities.
    skills_root = (
        Path(os.getenv("LOCALAPPDATA")) / "JarvisPersonal"
        if os.getenv("LOCALAPPDATA")
        else Path.home() / ".jarvis_personal"
    )
    knowledge_path = skills_root / "agent_knowledge.sqlite3"
    knowledge_stats = {}
    for table, condition in (
        ("skills", "active=1"),
        ("lessons", "active=1"),
        ("app_profiles", "1=1"),
    ):
        total = _readonly(
            knowledge_path, f"SELECT COUNT(*) AS n FROM {table} WHERE {condition}"
        )
        knowledge_stats[table] = (
            int(total[0]["n"]) if total else None
        )
    memory_stats = None
    if _flag("JARVIS_MEMORY_CORE_ENABLED"):
        memory_db = skills_root / "memory.sqlite3"
        stored = _readonly(
            memory_db, "SELECT COUNT(*) AS n FROM memories"
        )
        facts = _readonly(
            memory_db,
            "SELECT COUNT(*) AS n FROM memory_semantic_facts WHERE status='active'"
        )
        memory_stats = {
            "raw_count": int(stored[0]["n"]) if stored else None,
            "active_semantic_facts": int(facts[0]["n"]) if facts else None,
        }

    from .config import settings
    from .mcp_server_registry import MCPRegistry
    try:
        mcp_servers = MCPRegistry().list_servers()
    except (OSError, ValueError, TypeError, KeyError):
        mcp_servers = []
    return {
        "mcp": {
            "enabled": _flag("JARVIS_MCP_ENABLED"),
            "servers": mcp_servers,
        },
        "reliability_enabled": reliability_on,
        "mission_enabled": mission_on,
        "kernel_shadow_enabled": bool(settings.kernel_shadow_enabled),
        "memory_enabled": _flag("JARVIS_MEMORY_CORE_ENABLED"),
        "semantic_memory_enabled": (
            _flag("JARVIS_MEMORY_CORE_ENABLED")
            and _flag("JARVIS_SEMANTIC_MEMORY_V5_ENABLED")
        ),
        "browser_core_enabled": _flag("JARVIS_BROWSER_CORE_ENABLED"),
        "computer_core_enabled": _flag("JARVIS_COMPUTER_CORE_ENABLED"),
        "learning_enabled": bool(settings.operational_learning_enabled),
        "knowledge_stats": knowledge_stats,
        "memory_stats": memory_stats,
        "actions": actions,
        "missions": missions,
        "tasks": current_tasks,
        "supervisor": supervisor,
        "events": events,
        "unresolved_visible": unknown,
        "guarded_visible": sum(row["status"] == "guarded" for row in actions),
        "sample_limit": limit,
        "observed_at": time.time(),
    }


def runtime_model_snapshot(runtime: Any) -> dict[str, Any]:
    """Extract existing local counters only; never call a provider."""
    current = runtime
    budget = None
    used = None
    provider = ""
    category = ""
    last_usage = None
    effective_provider = ""
    seen = set()
    for _ in range(12):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if budget is None:
            budget = getattr(current, "reliability_round_budget", None)
            used = getattr(current, "reliability_rounds_used", None)
        if not effective_provider:
            effective_provider = str(
                getattr(current, "reliability_last_effective_provider", "") or ""
            )
        if last_usage is None:
            value = getattr(current, "reliability_last_usage", None)
            if isinstance(value, dict):
                last_usage = {
                    key: int(value[key]) if isinstance(value.get(key), int) else None
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                }
        if not provider:
            provider = str(getattr(current, "provider_name", "") or "")
        if not category:
            category = str(
                getattr(current, "last_api_error_category", "")
                or getattr(current, "reliability_last_failure_category", "") or ""
            )
        current = getattr(current, "delegate", None)
    return {
        "provider": provider or "non renseigné",
        "effective_provider": effective_provider[:40],
        "rounds_used": used if isinstance(used, int) else None,
        "rounds_limit": budget if isinstance(budget, int) else None,
        "failure_category": category[:50],
        "reported_usage": last_usage,
    }
