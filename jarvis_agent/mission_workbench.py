"""Operator-issued mission controls, serialized on the EXISTING Jarvis worker.

No model calls, desktop tools, persistence, or hidden action dispatcher here.
"""
from __future__ import annotations

from dataclasses import dataclass
import queue
import re
import threading


@dataclass(frozen=True)
class MissionCommand:
    operation: str
    value: str = ""


class MissionControlInbox:
    """FIFO commands, consumed by the worker rather than Qt's UI thread."""

    _OPS = frozenset({"begin", "resume", "detach", "review"})

    def __init__(self) -> None:
        self._items: queue.Queue[MissionCommand] = queue.Queue(maxsize=32)
        self.changed = threading.Event()

    def submit(self, operation: str, value: str = "") -> bool:
        op = str(operation or "").strip().lower()
        raw = str(value or "").strip()
        if op not in self._OPS:
            return False
        if op == "begin" and not (1 <= len(raw) <= 2000):
            return False
        if op in {"resume", "review"} and raw and not re.fullmatch(r"live_[a-f0-9]{32}", raw):
            return False
        if op == "resume" and not raw:
            return False
        if op == "detach":
            if raw:
                return False
        try:
            self._items.put_nowait(MissionCommand(op, raw))
        except queue.Full:
            return False
        self.changed.set()
        return True

    def pop_nowait(self) -> MissionCommand | None:
        try:
            packet = self._items.get_nowait()
        except queue.Empty:
            self.changed.clear()
            return None
        if self._items.empty():
            self.changed.clear()
        return packet

    def pending(self) -> bool:
        return not self._items.empty()


def perform_mission_command(agent, command: MissionCommand) -> dict:
    """Only on worker thread. Does not replay actions or assert goal proof."""
    op = command.operation
    if op == "begin":
        method = getattr(agent, "begin_mission", None)
        if not callable(method):
            return {"success": False, "operation": op,
                    "reason": "convergence_not_enabled"}
        mission_id = method(command.value)
        return {"success": True, "operation": op, "mission_id": mission_id,
                "status": "mission_started_not_verified"}
    if op == "resume":
        method = getattr(agent, "resume_mission", None)
        if not callable(method):
            return {"success": False, "operation": op,
                    "reason": "convergence_not_enabled"}
        state = method(command.value)
        # Blocked or uncertain states MUST NOT automatically dispatch anything.
        status = getattr(getattr(state, "status", None), "value", "")
        return {"success": True, "operation": op,
                "mission_id": command.value, "status": status,
                "manual_review_required": status == "blocked"}
    if op == "review":
        method = getattr(agent, "review_mission", None)
        if not callable(method):
            return {"success": False, "operation": op,
                    "reason": "convergence_not_enabled"}
        report = method(command.value or None)
        return {
            "success": True, "operation": op,
            "mission_id": report.get("mission_id", ""),
            "status": report.get("state", "review_available"),
            "manual_review_required": report.get("state") == "manual_review",
            "review": report,
        }
    if op == "detach":
        method = getattr(agent, "detach_mission", None)
        if not callable(method):
            return {"success": False, "operation": op,
                    "reason": "convergence_not_enabled"}
        active = str(getattr(agent, "active_mission_id", "") or "")
        if not active:
            return {"success": False, "operation": op,
                    "reason": "no_active_mission"}
        method()
        return {"success": True, "operation": op,
                "mission_id": active, "status": "detached_not_completed"}
    raise ValueError("invalid_mission_command")
