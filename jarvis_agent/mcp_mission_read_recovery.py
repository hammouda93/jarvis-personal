"""Bounded generic MCP read continuation based on operator-granted capabilities.

Observations and local receipts are *data*, never proof of a whole mission.
This module does not dispatch tools, infer rights, inspect remote descriptions,
guess account IDs or write into persistent Memory V5 / Skills.
"""
from __future__ import annotations

import json
import re
import unicodedata


def _tokens(value: str) -> set[str]:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    words = re.findall(r"[a-z0-9]+", plain)
    return {word[:-1] if word.endswith("s") and len(word) > 4 else word
            for word in words if len(word) >= 4}


def discovery_followup(actions) -> dict | None:
    """Find an actual local refusal that advertises already-approved read tools."""
    if not actions:
        return None
    action = actions[-1]
    name = str(getattr(action, "name", "") or "")
    if not name.startswith("mcp__") or getattr(action, "success", False):
        return None
    try:
        detail = json.loads(getattr(action, "detail", "") or "")
    except (ValueError, TypeError):
        return None
    if (not isinstance(detail, dict)
            or detail.get("guard") not in {
                "unresolved_argument_template", "missing_required_arguments",
                "invalid_argument_schema",
            }
            or detail.get("dispatched") is not False
            or detail.get("outcome_unknown") is not False):
        return None
    parts = name.split("__", 2)
    if len(parts) != 3:
        return None
    prefix = "mcp__" + parts[1] + "__"
    suggestions = detail.get("discovery_candidates")
    if not isinstance(suggestions, list):
        return None
    choices = [candidate for candidate in suggestions[:8]
               if isinstance(candidate, str)
               and candidate.startswith(prefix)
               and re.fullmatch(r"mcp__[a-z][a-z0-9_]{0,35}__[A-Za-z0-9_.-]{1,100}", candidate)
               and candidate != name]
    if not choices:
        return None
    # The gateway has already applied the user's server-scoped read grants.
    # These names are advisory: an actual call still passes every usual gate.
    return {
        "failed_tool": name,
        "approved_read_candidates": list(dict.fromkeys(choices))[:5],
        "instruction": (
            "Before requesting an identifier from the human, use an already "
            "authorized parameter-free observation if relevant. Select the "
            "capability through request_tool_capabilities if hidden. Never "
            "invent or substitute an account owner; authenticated identity "
            "may differ from the resource owner. If a read fails, report it."
        ),
    }


def unqueried_requested_lists(goal: str, actions, catalog) -> tuple[str, ...]:
    """Find *requested* read inventories not successfully queried yet.

    This is a bounded lexical coverage hint, NOT a semantic claim that a list
    contains anything. Tools are taken only from the local authorized catalog.
    A singular get_* call does not prove an inventory read was performed.
    """
    invoked_servers = {
        str(getattr(action, "name", "")).split("__", 2)[1]
        for action in actions
        if str(getattr(action, "name", "")).startswith("mcp__")
        and len(str(getattr(action, "name", "")).split("__", 2)) == 3
    }
    if not invoked_servers:
        return ()
    goal_terms = _tokens(goal)
    if not goal_terms:
        return ()
    successful = {str(getattr(action, "name", "") or "")
                  for action in actions if getattr(action, "success", False)}
    candidates = []
    for definition in catalog or ():
        func = definition.get("function") if isinstance(definition, dict) else {}
        name = str((func or {}).get("name") or "")
        parts = name.split("__", 2)
        if len(parts) != 3 or parts[1] not in invoked_servers:
            continue
        tool = parts[2]
        if not tool.startswith("list_"):
            continue
        subject = _tokens(tool.removeprefix("list_"))
        # Catalog hints must have a concrete user-requested subject. Never
        # demand every available list tool merely because the server exists.
        if not subject or not (subject & goal_terms):
            continue
        if name not in successful:
            candidates.append(name)
    return tuple(dict.fromkeys(candidates))[:8]
