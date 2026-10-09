"""Lossless request-only deduplication of successful read observations.

Keep transcript, user/system text, schemas, mutations, failures and protocol
envelopes intact. No summarizer, memory writes, permissions or extra model call.
"""
from __future__ import annotations

import json

_READS = frozenset({"inspect_active_window", "inspect_interface", "inspect_browser_page",
    "browser_observe_dom", "browser_find", "browser_list_tabs", "msf_query_records",
    "msf_describe_schema", "msf_capabilities", "msf_list_routes", "msf_search_code", "list_applications"})


def compact_duplicate_observations(messages: list[dict]) -> tuple[list[dict], int]:
    copies = list(messages)
    latest = {}
    saved = 0
    for index in range(len(copies) - 1, -1, -1):
        item = copies[index]
        name = item.get("name")
        content = item.get("content")
        call_id = item.get("tool_call_id")
        if item.get("role") != "tool" or name not in _READS or not call_id or not isinstance(content, str) or len(content) < 800:
            continue
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            continue
        if (not isinstance(payload, dict) or payload.get("success") is not True
                or payload.get("outcome_unknown") or payload.get("approval_required")):
            continue
        # Exact equality only: even a changed timestamp/ref/value prevents reuse.
        key = (name, content)
        if key not in latest:
            latest[key] = str(call_id)
            continue
        marker = json.dumps({"tool": name, "success": True, "request_deduplicated": True,
            "identical_observation_at_tool_call_id": latest[key],
            "note": "Identical read data retained later in this request. Not an action, permission, or new verification."},
            separators=(",", ":"))
        if len(marker) < len(content):
            copies[index] = {**item, "content": marker}
            saved += len(content) - len(marker)
    return copies, saved
