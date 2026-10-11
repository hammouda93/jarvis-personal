"""General conversational mission recovery rules; no app- or site-specific code.

This module consumes only the original user's request and trusted tool
receipts. It neither invents a mission plan nor dispatches external actions.
All safety guards, approvals and tool boundaries remain authoritative.
"""
from __future__ import annotations

import json
import re
import unicodedata

from .instruction_clauses import positive_instruction_clauses


def _plain(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(c for c in raw if not unicodedata.combining(c))


def recoverable_discovery(actions) -> dict | None:
    """A failed tool may still discover valid names; failure != no capability."""
    if not actions:
        return None
    last = actions[-1]
    if bool(getattr(last, "success", False)):
        return None
    try:
        candidates = json.loads(str(getattr(last, "detail", "") or ""))
    except (ValueError, TypeError):
        return None
    if not isinstance(candidates, list):
        return None
    grounded = [dict(item) for item in candidates
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"].strip()]
    if not grounded:
        return None
    # Do not choose a candidate from a title or score; let the reasoning model
    # compare the actual user intent with observed names. Ambiguity => ask.
    return {
        "source_tool": str(getattr(last, "name", "")),
        "candidates": grounded[:5],
        "next_step": "Compare observed candidate names with the user's original goal. "
                     "If a single candidate unambiguously matches, use the exact "
                     "observed name with the already available generic tool, "
                     "without forcing a new instance. If ambiguous, ask a short "
                     "clarification. Discovery is not execution or verification.",
    }


_MUTATION_TOOLS = frozenset({
    "open_application", "open_file", "open_url", "close_window", "close_tab",
    "click_ui_element", "write_ui_element", "press_key",
    "browser_navigate", "browser_click", "browser_press", "browser_write",
    "browser_select", "browser_close_tab", "browser_back",
    "computer_click", "computer_press", "computer_write", "computer_shortcut",
})
_OBSERVATION_TOOLS = frozenset({
    "list_windows", "inspect_active_window", "inspect_interface",
    "observe_screen", "browser_observe_dom", "browser_list_tabs",
    "browser_verify", "computer_observe", "computer_verify",
})


def _last_step_requires_observation(user_text: str) -> bool:
    """Only an explicit final verification clause activates this guard."""
    # Use a provisional outline for *intent*, not proof or authorization.
    from .mission_trace_v10h import requested_steps
    clauses = requested_steps(user_text)
    if len(clauses) < 2:
        return False
    last = _plain(clauses[-1]).strip(" '.,;")
    return bool(re.match(
        r"^(?:verifie|verifier|controle|controler|inspecte|inspecter|"
        r"check|verify|confirm|confirme|confirmer)\b",
        last,
    ))


def post_mutation_observation_missing(user_text: str, actions) -> bool:
    """True only if user requested a *last* verification after a mutation.

    An earlier observation is not evidence about a subsequent change.
    A trustworthy structured post-observation is enough except when the final
    request explicitly asks for the updated list of windows.
    """
    if not _last_step_requires_observation(user_text):
        return False
    previous = [(i, a) for i, a in enumerate(actions)
                if getattr(a, "success", False)
                and getattr(a, "name", "") in _MUTATION_TOOLS]
    if not previous:
        return False
    last_index, last_action = previous[-1]
    later = actions[last_index + 1:]
    if any(getattr(a, "success", False)
           and getattr(a, "name", "") in _OBSERVATION_TOOLS
           for a in later):
        return False
    # When the request names a window inventory, only a fresh window read
    # can answer whether the window disappeared from that inventory.
    if re.search(r"\b(?:fenetres|windows|window list|liste des fenetres)\b",
                 _plain(user_text)):
        return True
    try:
        detail = json.loads(str(getattr(last_action, "detail", "") or "{}"))
    except (ValueError, TypeError):
        detail = {}
    if (isinstance(detail, dict)
            and detail.get("verified") is True
            and isinstance(detail.get("post_observation"), dict)):
        return False
    return True
