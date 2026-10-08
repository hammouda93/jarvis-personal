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

    _OPS = frozenset({"begin", "begin_only", "resume", "detach", "review", "plan", "route", "auto_plan"})

    def __init__(self) -> None:
        self._items: queue.Queue[MissionCommand] = queue.Queue(maxsize=32)
        self.changed = threading.Event()

    def submit(self, operation: str, value: str = "") -> bool:
        op = str(operation or "").strip().lower()
        raw = str(value or "").strip()
        if op not in self._OPS:
            return False
        if op in {"begin", "begin_only"} and not (1 <= len(raw) <= 2000):
            return False
        if op == "plan" and not (1 <= len(raw) <= 500):
            return False
        if op == "auto_plan" and raw:
            return False
        if op in {"resume", "review", "route"} and raw and not re.fullmatch(r"live_[a-f0-9]{32}", raw):
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
    if op in {"begin", "begin_only"}:
        method = getattr(agent, "begin_mission", None)
        if not callable(method):
            return {"success": False, "operation": op,
                    "reason": "convergence_not_enabled"}
        mission_id = method(command.value)
        return {"success": True, "operation": op, "mission_id": mission_id,
                "status": (
                    "mission_created_no_execution" if op == "begin_only"
                    else "mission_started_not_verified"
                ), "tool_execution": False, "goal_verified": False}
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
    if op == "plan":
        from .mission_semantics import MissionContract, MissionStep

        getter = getattr(agent, "mission_snapshot", None)
        recorder = getattr(agent, "register_semantic_plan", None)
        mission_id = str(getattr(agent, "active_mission_id", "") or "")
        if not callable(getter) or not callable(recorder):
            return {"success": False, "operation": op,
                    "reason": "convergence_not_enabled"}
        if not mission_id:
            return {"success": False, "operation": op,
                    "reason": "no_active_mission"}
        requirements = tuple(dict.fromkeys(
            item.strip() for item in command.value.split(",")
            if item.strip()
        ))
        if not (1 <= len(requirements) <= 8):
            return {"success": False, "operation": op,
                    "reason": "one_to_eight_criteria_required"}
        if any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]{0,62}", item)
               for item in requirements):
            return {"success": False, "operation": op,
                    "reason": "criteria_must_be_identifiers"}
        target = getter(mission_id)
        goal = str(target.get("user_goal") or "").strip()
        if not goal:
            raise RuntimeError("mission_goal_required")
        contract = MissionContract(
            source_text=goal,
            objective=goal,
            steps=(MissionStep(
                step_id="verify_goal", intent="verify_user_objective",
                required_evidence=requirements,
            ),),
            metadata={"origin": "user_defined_verification_criteria"},
        )
        # Registering a plan is NOT registering a proof. The model/tool cannot
        # auto-approve these conditions or verify its own external side effect.
        recorder(contract)
        return {
            "success": True, "operation": op,
            "mission_id": mission_id, "status": "criteria_registered_unverified",
            "criteria_count": len(requirements),
        }
    if op == "auto_plan":
        generator = getattr(agent, "generate_semantic_plan", None)
        if not callable(generator):
            return {"success": False, "operation": op,
                    "reason": "planning_requires_convergence"}
        report = generator()
        return {
            "success": True, "operation": op,
            "mission_id": report.get("mission_id", ""),
            "status": "llm_plan_registered_unverified",
            "step_count": report.get("step_count", 0),
            "evidence_count": report.get("evidence_count", 0),
            "unresolved_count": report.get("unresolved_count", 0),
            "model_request_count": report.get("model_request_count", 1),
            "tool_execution": False,
            "goal_verified": False,
        }
    if op == "route":
        method = getattr(agent, "propose_mission_capabilities", None)
        if not callable(method):
            return {"success": False, "operation": op,
                    "reason": "capability_planner_not_enabled"}
        report = method(command.value or None)
        return {
            "success": True, "operation": op,
            "mission_id": report.get("mission_id", ""),
            "status": report.get("status", "routes_proposed"),
            "authoritative": False, "will_execute": False,
            "proposal": report,
        }
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
