"""Trusted in-process responsibilities using the existing runtime once per turn.

No subprocess, second model, arbitrary builder, tool dispatcher or retry is
introduced. Explicit tool scopes can narrow, never widen, runtime permissions.
"""
from __future__ import annotations

from typing import Any

from .agent_factory import AgentFactory
from .capability_registry import DEFAULT_CAPABILITY_REGISTRY


_BROWSER = frozenset({
    "open_url", "browser_list_tabs", "browser_get_active_tab", "browser_activate_tab",
    "browser_navigate", "browser_observe_dom", "browser_find", "browser_click", "browser_write",
    "browser_select", "browser_press", "browser_scroll", "browser_back", "browser_forward",
    "browser_close_tab", "browser_verify", "browser_download", "browser_list_downloads",
    "browser_get_download", "inspect_browser_page", "find_browser_element", "write_browser_element",
    "click_browser_element", "press_browser_element", "scroll_browser_element",
})
_COMPUTER = frozenset({
    "list_windows", "inspect_active_window", "inspect_interface", "open_application", "open_file",
    "click_ui_element", "write_ui_element", "press_key", "close_tab", "close_window",
    "observe_screen", "click_visual_target", "write_visual_target", "computer_list_windows",
    "computer_get_active_window", "computer_observe", "computer_find", "computer_focus_probe",
    "computer_click", "computer_write", "computer_press", "computer_shortcut", "computer_verify",
})


def responsibility_catalog() -> list[dict]:
    return [{"id": m.agent_id, "name": m.display_name, "description": m.description,
             "executor": "legacy_runtime", "max_concurrency": 1}
            for m in DEFAULT_CAPABILITY_REGISTRY.agents()]


def available_tools(registry: Any) -> frozenset[str]:
    return frozenset(str(t["function"]["name"]) for t in registry.ollama_tools())


def role_tools(agent_id: str, available: frozenset[str], explicit: list[str] | None = None) -> frozenset[str]:
    manifest = DEFAULT_CAPABILITY_REGISTRY.get_agent(agent_id)
    if manifest is None:
        raise ValueError("unknown_delegation_responsibility")
    native = frozenset(t for t in available if not t.startswith("mcp__"))
    if agent_id == "interaction":
        base = native
    elif agent_id == "research":
        base = native & (_BROWSER | {"search_web", "research_web"})
    elif agent_id == "browser":
        base = native & _BROWSER
    elif agent_id == "windows":
        base = native & _COMPUTER
    elif agent_id in {"documents", "data", "communications", "reports"}:
        base = native & (_BROWSER | _COMPUTER)
    else:
        base = native & frozenset(manifest.allowed_tools)
    if explicit is None:
        return base
    if (not isinstance(explicit, list) or len(explicit) > 100
            or any(not isinstance(t, str) or t not in available for t in explicit)):
        raise ValueError("delegation_tool_not_currently_available")
    requested = frozenset(explicit)
    if requested - base - frozenset(t for t in available if t.startswith("mcp__")):
        raise ValueError("delegation_tool_outside_responsibility")
    # Remote tool descriptions never grant permissions or choose this scope.
    return requested


def validate_assignments(plan: dict, assignments: dict, registry: Any) -> dict:
    if not isinstance(assignments, dict):
        raise ValueError("invalid_delegation_assignments")
    step_ids = {s["step_id"] for s in plan["steps"]}
    if set(assignments) - step_ids:
        raise ValueError("delegation_step_not_in_plan")
    available = available_tools(registry) if assignments else frozenset()
    clean = {}
    for step_id, entry in assignments.items():
        if not isinstance(entry, dict) or set(entry) - {"agent", "tools"} or "agent" not in entry:
            raise ValueError("invalid_delegation_assignment")
        role_tools(entry["agent"], available, entry.get("tools"))
        clean[step_id] = {"agent": entry["agent"]}
        if "tools" in entry:
            clean[step_id]["tools"] = list(dict.fromkeys(entry["tools"]))
    return clean


class _RuntimeResponsibility:
    def __init__(self, supervisor: Any, agent_id: str):
        self.supervisor, self.agent_id = supervisor, agent_id

    def run(self, task: dict) -> dict:
        from .active_mission_supervisor import execution_scope_active
        if (not execution_scope_active() or task["mission_id"] != self.supervisor.mission_id
                or task["step_id"] != self.supervisor.step["step_id"]
                or self.agent_id != self.supervisor.agent_id):
            raise PermissionError("delegation_context_mismatch")
        self.supervisor._load()  # Owner must still match immediately before execution.
        turn = self.supervisor.runtime._run(task["request"], context=task["context"],
                                          log=task.get("log"), phase=task.get("phase"))
        # Private turn stays in the supervisor; factory results contain no bodies.
        self.supervisor.turn_result = turn
        return {"agent": self.agent_id, "executor": "legacy_runtime",
                "turn_returned": True, "goal_verified": False}


def build_responsibility_factory(supervisor: Any) -> AgentFactory:
    factory = AgentFactory()
    for item in responsibility_catalog():
        role = item["id"]
        factory.register_builder(role, lambda role=role: _RuntimeResponsibility(supervisor, role))
    return factory
