"""Operator-authored role settings, applied only to controlled delegations.

Profiles never register tools, grant MCP access or create a new executor.
Absent settings preserve existing roles; explicit tool lists can narrow them.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from .capability_registry import DEFAULT_CAPABILITY_REGISTRY

_LOCK = threading.RLock()
_TOOL = re.compile(r"^[A-Za-z0-9_.-]+(?:__[A-Za-z0-9_.-]+)*$")
_FIELDS = {"name", "purpose", "competencies", "tools", "context_tokens", "model_calls", "actions", "seconds"}
_LIMITS = {"context_tokens": (1500, 16000, 12000), "model_calls": (1, 64, 12),
           "actions": (1, 128, 32), "seconds": (10, 3600, 900)}


def profile_path() -> Path:
    custom = os.getenv("JARVIS_AGENT_PROFILES_PATH", "").strip()
    if custom:
        return Path(custom)
    base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".jarvis_personal") / "JarvisPersonal" / "agent_profiles.json"


def defaults(agent_id: str) -> dict:
    manifest = DEFAULT_CAPABILITY_REGISTRY.get_agent(agent_id)
    if manifest is None:
        raise ValueError("unknown_agent_profile")
    return {"name": manifest.display_name, "purpose": manifest.description,
            "competencies": list(manifest.capabilities), "tools": None,
            **{key: values[2] for key, values in _LIMITS.items()}}


def validate(agent_id: str, value: dict) -> dict:
    defaults(agent_id)
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError("invalid_agent_profile_fields")
    result = deepcopy(value)
    for key, maximum in (("name", 100), ("purpose", 2000)):
        if (not isinstance(value[key], str) or not value[key].strip()
                or len(value[key]) > maximum or "\x00" in value[key]):
            raise ValueError("invalid_agent_profile:" + key)
        result[key] = value[key].strip()
    skills = value["competencies"]
    if (not isinstance(skills, list) or len(skills) > 30
            or any(not isinstance(s, str) or not 1 <= len(s.strip()) <= 200 or "\x00" in s for s in skills)):
        raise ValueError("invalid_agent_competencies")
    result["competencies"] = list(dict.fromkeys(s.strip() for s in skills))
    tools = value["tools"]
    if tools is not None:
        if (not isinstance(tools, list) or len(tools) > 200
                or any(not isinstance(t, str) or len(t) > 180 or not _TOOL.fullmatch(t) for t in tools)
                or len(set(tools)) != len(tools)):
            raise ValueError("invalid_agent_profile_tools")
        result["tools"] = sorted(tools)
    for key, (low, high, _) in _LIMITS.items():
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError("invalid_agent_profile_budget:" + key)
    return result


class AgentProfileStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else profile_path()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "profiles": {}}
        if self.path.stat().st_size > 262144:
            raise ValueError("agent_profiles_too_large")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"version", "profiles"} or data["version"] != 1:
            raise ValueError("invalid_agent_profile_store")
        if not isinstance(data["profiles"], dict) or len(data["profiles"]) > 30:
            raise ValueError("invalid_agent_profile_records")
        for role, row in data["profiles"].items():
            if (not isinstance(row, dict) or set(row) != {"revision", "settings"}
                    or type(row["revision"]) is not int or row["revision"] < 1):
                raise ValueError("invalid_agent_profile_revision")
            validate(role, row["settings"])
        return data

    def get(self, agent_id: str) -> dict:
        base = defaults(agent_id)
        with _LOCK:
            row = self._load()["profiles"].get(agent_id)
        return {"id": agent_id, "revision": row["revision"] if row else 0,
                "settings": deepcopy(row["settings"] if row else base)}

    def save(self, agent_id: str, settings: dict, *, expected_revision: int) -> dict:
        clean = validate(agent_id, settings)
        with _LOCK:
            data = self._load()
            row = data["profiles"].get(agent_id)
            revision = row["revision"] if row else 0
            if type(expected_revision) is not int or expected_revision != revision:
                raise ValueError("agent_profile_changed_reload_required")
            data["profiles"][agent_id] = {"revision": revision + 1, "settings": clean}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".agent_profiles_", suffix=".tmp", dir=self.path.parent)
            try:
                if os.name != "nt":
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(data, out, ensure_ascii=False, indent=2, sort_keys=True)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return self.get(agent_id)


def effective_tools(agent_id: str, base: frozenset[str], profile: dict | None = None) -> frozenset[str]:
    """Intersect with the already validated role/assignment and live grants."""
    saved = profile or AgentProfileStore().get(agent_id)
    selected = saved["settings"]["tools"]
    return base if selected is None else base & frozenset(selected)


def profile_context(profile: dict) -> dict:
    """Bounded human configuration; never permission or a goal certificate."""
    return {"id": profile["id"], "revision": profile["revision"],
            "name": profile["settings"]["name"], "purpose": profile["settings"]["purpose"],
            "competencies": profile["settings"]["competencies"],
            "permissions_changed": False, "independent_worker": False}
