"""Opt-in reliability boundary inspired by Hermes tool-round persistence.

The existing Jarvis runtime stays the only executor. Never retry an action
with an unknown external outcome, even across restarts. No model calls here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .native_tools import AgentActionResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    home = os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME")
    return Path(home) / "JarvisPersonal" / "action_reliability" if home else Path.home() / ".jarvis_personal" / "action_reliability"


# Deliberately generic categories. Unrecognized tools are NOT assumed read-only.
_READ_ONLY = frozenset({
    "list_windows", "inspect_active_window", "inspect_interface",
    "observe_screen", "list_applications", "get_current_time",
    "get_system_time", "search_agent_knowledge", "agent_knowledge_stats",
    "recall_information", "semantic_memory_search", "research_web",
    "search_web", "get_memory_stats", "browser_list_tabs",
    "browser_get_active_tab", "browser_observe_dom", "browser_find",
    "browser_verify", "computer_observe", "computer_verify",
    "computer_list_windows", "computer_get_active_window",
    "msf_query_records",
})


def is_mutating(name: str) -> bool:
    return str(name) not in _READ_ONLY


def _reported_unknown(result: Any) -> bool:
    detail = getattr(result, "detail", "")
    try:
        data = json.loads(detail) if isinstance(detail, str) else detail
    except (TypeError, ValueError):
        data = {}
    return isinstance(data, dict) and (
        data.get("outcome_unknown") is True
        or data.get("verified") == "unknown"
        or data.get("error_code") == "outcome_unknown"
    )


def classify_api_failure(error: BaseException | str) -> str:
    """Diagnostic only; does not retry requests or expose credentials."""
    message = str(error).casefold()
    status = getattr(error, "status_code", None)
    if status == 429 or any(t in message for t in ("429", "rate limit", "too many requests", "quota")):
        return "rate_limit"
    if status in (401, 403) or any(t in message for t in ("401", "403", "unauthorized", "forbidden")):
        return "authentication"
    if status in (400, 404, 422) or any(t in message for t in ("invalid_request", "model_not_found", "400", "404")):
        return "non_retryable_client"
    if status in (500, 502, 503, 504, 529) or any(t in message for t in ("502", "503", "504", "529", "overloaded", "unavailable")):
        return "transient_provider"
    if any(t in message for t in ("timeout", "timed out", "connection reset", "connection aborted")):
        return "transient_transport"
    return "unknown"


class ActionLedger:
    """Pre-side-effect SQLite journal; no argument/body plaintext is stored."""

    def __init__(self, base_dir: str | Path | None = None):
        self.root = Path(base_dir) if base_dir else _root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "actions.sqlite3"
        self._lock = threading.RLock()
        self._key = self._load_key()
        with self._connect() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL,
                name TEXT NOT NULL,
                argument_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                success INTEGER,
                verified INTEGER NOT NULL DEFAULT 0,
                evidence_ref TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            cx.execute("CREATE INDEX IF NOT EXISTS actions_uncertain_idx ON actions(argument_digest, status)")

    def _load_key(self) -> bytes:
        path = self.root / "action_hmac.key"
        try:
            with path.open("xb") as f:
                key = secrets.token_bytes(32)
                f.write(key)
        except FileExistsError:
            pass
        key = path.read_bytes()
        if len(key) != 32:
            raise RuntimeError("action_integrity_key_invalid")
        return key

    @contextmanager
    def _connect(self):
        cx = sqlite3.connect(self.db, timeout=5.0)
        cx.row_factory = sqlite3.Row
        try:
            with cx:
                yield cx
        finally:
            cx.close()

    def signature(self, name: str, args: dict[str, Any]) -> str:
        raw = json.dumps(
            [str(name), dict(args or {})],
            ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._key, raw, hashlib.sha256).hexdigest()

    def begin(self, *, turn_id: str, name: str, arguments: dict[str, Any]) -> tuple[str | None, str]:
        """Atomic check-and-insert: fail closed for uncertain repeat."""
        signature = self.signature(name, arguments)
        with self._lock, self._connect() as cx:
            # The database lock must protect against concurrent processes too.
            cx.execute("BEGIN IMMEDIATE")
            if is_mutating(name):
                unknown = cx.execute(
                    "SELECT action_id FROM actions WHERE argument_digest=? AND status IN ('dispatched', 'unknown') LIMIT 1",
                    (signature,),
                ).fetchone()
                if unknown:
                    return None, str(unknown["action_id"])
            action_id = "act_" + uuid.uuid4().hex
            cx.execute(
                "INSERT INTO actions(action_id,turn_id,name,argument_digest,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (action_id, turn_id, str(name)[:120], signature, "dispatched", _now(), _now()),
            )
        return action_id, ""

    def finish(self, action_id: str, *, success: bool, unknown: bool = False, verified: bool = False) -> None:
        with self._lock, self._connect() as cx:
            cx.execute(
                "UPDATE actions SET status=?,success=?,verified=?,updated_at=? WHERE action_id=?",
                ("unknown" if unknown else ("succeeded" if success else "failed"),
                 int(success), int(verified and success and not unknown), _now(), action_id),
            )

    def resolve_unknown(self, action_id: str, *, independently_observed: str, evidence_ref: str) -> None:
        """Trusted external caller only; does not redo an action or verify the goal."""
        if independently_observed not in {"effect_observed", "no_effect", "cancel"}:
            raise ValueError("invalid_recovery_outcome")
        if not str(evidence_ref or "").strip():
            raise ValueError("independent_evidence_required")
        with self._lock, self._connect() as cx:
            cx.execute("BEGIN IMMEDIATE")
            row = cx.execute(
                "SELECT status FROM actions WHERE action_id=?", (str(action_id),)
            ).fetchone()
            if row is None:
                raise KeyError("action_not_found")
            if row["status"] not in ("unknown", "dispatched"):
                raise RuntimeError("action_not_uncertain")
            cx.execute(
                "UPDATE actions SET status='resolved',verified=?,evidence_ref=?,updated_at=? WHERE action_id=?",
                (int(independently_observed == "effect_observed"), str(evidence_ref)[:300], _now(), action_id),
            )

    def recent(self, *, turn_id: str) -> list[dict[str, Any]]:
        with self._connect() as cx:
            rows = cx.execute(
                "SELECT action_id,name,status,success,verified FROM actions WHERE turn_id=? ORDER BY created_at,action_id",
                (turn_id,),
            ).fetchall()
        return [dict(row) for row in rows]


class ReliabilityToolRegistry:
    """Transparent registry adapter used *inside* the already selected agent."""

    def __init__(self, delegate: Any, ledger: ActionLedger):
        self.delegate = delegate
        self.ledger = ledger
        self._local = threading.local()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def begin_turn(self) -> str:
        turn_id = "turn_" + uuid.uuid4().hex
        self._local.turn_id = turn_id
        self._local.failures = {}
        return turn_id

    def end_turn(self) -> None:
        self._local.turn_id = None
        self._local.failures = {}

    @staticmethod
    def _blocked(name: str, reason: str) -> AgentActionResult:
        return AgentActionResult(
            name=name, success=False,
            message="Action non répétée : état incertain ou aucun progrès vérifiable. Une nouvelle observation est nécessaire.",
            detail=json.dumps({"reliability_guard": reason, "verified": False}),
        )

    def execute(self, name: str, arguments: dict[str, Any], *, approved: bool = False):
        args = dict(arguments or {})
        digest = self.ledger.signature(name, args)
        failures = getattr(self._local, "failures", {})
        if failures.get(digest, 0) >= 2:
            return self._blocked(name, "repeated_exact_failure")
        turn_id = getattr(self._local, "turn_id", None) or "external_" + uuid.uuid4().hex
        try:
            action_id, unresolved = self.ledger.begin(
                turn_id=turn_id, name=name, arguments=args
            )
        except (OSError, sqlite3.Error, RuntimeError):
            # Never execute a side effect if durable pre-dispatch failed.
            return self._blocked(name, "checkpoint_unavailable")
        if action_id is None:
            return self._blocked(name, "prior_outcome_unknown:" + unresolved)

        try:
            result = self.delegate.execute(name, args, approved=approved)
        except Exception:
            try:
                self.ledger.finish(action_id, success=False, unknown=True)
            finally:
                # The caller may surface this exception; never re-execute here.
                raise

        unknown = _reported_unknown(result)
        success = bool(getattr(result, "success", False))
        detail = getattr(result, "detail", "")
        try:
            parsed = json.loads(detail) if isinstance(detail, str) else detail
        except (ValueError, TypeError):
            parsed = {}
        verified = isinstance(parsed, dict) and parsed.get("verified") is True

        try:
            self.ledger.finish(action_id, success=success, unknown=unknown, verified=verified)
        except (OSError, sqlite3.Error):
            # Actual outcome may have happened; report uncertainty, not success.
            return self._blocked(name, "result_checkpoint_failed_outcome_unknown")

        if unknown:
            # An existing tool might report success while explicitly saying
            # its effect is unknown. Never promote this to verified success.
            return self._blocked(name, "tool_reported_outcome_unknown")
        if not success:
            failures[digest] = failures.get(digest, 0) + 1
            self._local.failures = failures
        return result


class HermesReliabilityRuntime:
    """Turn-scoped diagnostics wrapper; preserves the precise delegate result."""

    def __init__(self, delegate: Any, tools: ReliabilityToolRegistry):
        self.delegate = delegate
        self.reliability_tools = tools
        self.last_turn_id: str | None = None
        self.last_api_error_category: str | None = None
        self._lock = threading.RLock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def _run(self, method: str, *args, **kwargs):
        with self._lock:
            turn_id = self.reliability_tools.begin_turn()
            self.last_turn_id = turn_id
            self.last_api_error_category = None
            try:
                return getattr(self.delegate, method)(*args, **kwargs)
            except Exception as exc:
                self.last_api_error_category = classify_api_failure(exc)
                raise
            finally:
                self.reliability_tools.end_turn()

    def run(self, user_text: str, *, log=None, phase=None):
        return self._run("run", user_text, log=log, phase=phase)

    def run_with_context(self, user_text: str, context: str, *, log=None, phase=None):
        method = "run_with_context" if callable(getattr(self.delegate, "run_with_context", None)) else "run"
        args = (user_text, context) if method == "run_with_context" else (user_text,)
        return self._run(method, *args, log=log, phase=phase)

    def recent_actions(self) -> list[dict[str, Any]]:
        return self.reliability_tools.ledger.recent(turn_id=self.last_turn_id) if self.last_turn_id else []

    def reset(self):
        self.reliability_tools.end_turn()
        return self.delegate.reset()

    def warm_up(self, *, log=None):
        return self.delegate.warm_up(log=log)
