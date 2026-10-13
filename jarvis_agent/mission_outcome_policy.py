"""Generic action-outcome classification for persistent mission recovery.

A failed *observation* has no external side effect, while a failed action
with an uncertain dispatch may already have modified the user's environment.
The mission controller must not confuse the two.
"""
from __future__ import annotations

import json
from typing import Any

# Read-only capabilities, independent of the visited website or application.
READ_ONLY_TOOLS = frozenset({
    "browser_list_tabs", "browser_get_active_tab", "browser_observe_dom",
    "browser_find", "browser_verify", "computer_observe", "computer_find",
    "computer_verify", "computer_list_windows", "computer_get_active_window",
    "list_windows", "inspect_active_window", "inspect_interface",
    "observe_screen", "verify_file_artifact", "find_file",
    "semantic_memory_inspect", "semantic_memory_search",
    "semantic_memory_events_on_date", "semantic_memory_events_in_range",
    "msf_describe_schema", "msf_count_records", "msf_query_records",
    "msf_readonly_sql",
})


def _detail(raw: Any) -> dict:
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def failure_requires_review(actions: tuple | list) -> bool:
    """True unless every failed call is provably free of external effects.

    Successful dispatched actions aren't replayed by this function. It is
    safe to *continue with read-only observation* after failures classified
    as non-mutating; it is never safe to infer the mission goal was proven.
    """
    for action in actions:
        name = str(getattr(action, "name", "") or "")
        detail = _detail(getattr(action, "detail", ""))
        if detail.get("outcome_unknown") is True:
            return True
        if bool(getattr(action, "success", False)):
            continue
        if detail.get("dispatched") is True:
            # Independently observed window still present after a close
            # attempt. The requested close did not complete, so a fresh
            # dialog observation is safe; re-dispatching the same close is NOT.
            # This exception requires the actual readback contract, not the
            # model's claim that a close probably failed.
            if (name in {"close_window", "close_tab"}
                    and detail.get("postcondition") == ("window_present" if name == "close_window" else "tab_present")
                    and detail.get("outcome_unknown") is False
                    and isinstance(detail.get("post_observation"), dict)
                    and detail["post_observation"].get("observation_id")):
                continue
            return True
        if name in READ_ONLY_TOOLS:
            continue
        # Legacy agent-level guard: its rejection is constructed BEFORE any
        # tool registry/Chrome dispatch. Preserve that known safe receipt
        # until all runtime guards emit structured dispatched:false metadata.
        # This is a *guard contract*, not a website/application rule.
        if (name == "open_url" and getattr(action, "detail", "") ==
                "open_url_blocked_for_existing_browser_context"):
            continue
        # Rejected locally before the tool was dispatched. No external effect.
        if detail.get("dispatched") is False and detail.get("outcome_unknown") is False:
            continue
        return True
    return False
