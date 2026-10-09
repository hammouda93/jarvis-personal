"""Explicit local Skill management, serialized on the existing worker.

These commands are not model tools and never dispatch an application action.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import re


@dataclass(frozen=True)
class SkillCommand:
    operation: str
    payload: dict


class SkillControlInbox:
    OPS = frozenset({"list", "view", "enable", "disable", "edit", "restore"})

    def __init__(self):
        self._queue: queue.Queue[SkillCommand] = queue.Queue(maxsize=16)

    @classmethod
    def validate(cls, operation: str, payload: dict) -> None:
        if operation not in cls.OPS or not isinstance(payload, dict):
            raise ValueError("invalid_skill_command")
        if operation == "list":
            if payload:
                raise ValueError("invalid_skill_command")
            return
        keys = {"name"}
        if operation != "view":
            keys.add("expected_version")
        if operation == "restore":
            keys.add("version")
        if operation == "edit":
            keys.update({"goal", "app_scope", "procedure", "success_checks", "failure_patterns"})
        if set(payload) != keys or not re.fullmatch(r"[a-z0-9_\u00e0-\u00ff]{3,100}", str(payload.get("name", ""))):
            raise ValueError("invalid_skill_command")
        for key in ("expected_version", "version"):
            if key in payload and (type(payload[key]) is not int or payload[key] < 1):
                raise ValueError("invalid_skill_version")
        if operation == "edit":
            if any(not isinstance(payload[k], str) or len(payload[k]) > cap
                   for k, cap in (("goal", 500), ("app_scope", 120))):
                raise ValueError("invalid_skill_edit")
            for key in ("procedure", "success_checks", "failure_patterns"):
                items = payload[key]
                if (not isinstance(items, list) or len(items) > 24
                        or any(not isinstance(item, str) or len(item) > 700 for item in items)):
                    raise ValueError("invalid_skill_edit")

    def submit(self, operation: str, value: str = "{}") -> bool:
        if not isinstance(value, str) or len(value) > 55000:
            return False
        try:
            payload = json.loads(value)
            self.validate(operation, payload)
            self._queue.put_nowait(SkillCommand(operation, payload))
        except (ValueError, TypeError, queue.Full):
            return False
        return True

    def pop_nowait(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None


def perform_skill_command(store, command: SkillCommand) -> dict:
    op, args = command.operation, command.payload
    SkillControlInbox.validate(op, args)
    name = args.get("name", "")
    if op in {"enable", "disable"}:
        store.set_skill_active(name, op == "enable", expected_version=args["expected_version"])
    elif op == "restore":
        store.restore_skill(name, args["version"], expected_version=args["expected_version"])
    elif op == "edit":
        store.revise_skill(**args)
    result = {"success": True, "operation": op, "skills": [
        {"name": s.name, "goal": s.goal, "version": s.version, "active": s.active, "source": s.source}
        for s in store.list_skills(include_inactive=True)], "external_action_dispatched": False}
    if name:
        result["skill"] = store.skill_to_dict(store.get_skill(name, include_inactive=True))
        result["history"] = store.skill_history(name)
    return result
