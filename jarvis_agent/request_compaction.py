"""Lossless request-only deduplication of successful read observations.

Keep transcript, user/system text, schemas, mutations, failures and protocol
envelopes intact. No summarizer, memory writes, permissions or extra model call.
"""
from __future__ import annotations

import json

_READS = frozenset({"workspace_read", "inspect_active_window", "inspect_interface", "inspect_browser_page",
    "browser_observe_dom", "browser_find", "browser_list_tabs", "msf_query_records",
    "msf_describe_schema", "msf_capabilities", "msf_list_routes", "msf_search_code", "list_applications"})


def bounded_conversation_history(messages, *, maximum_turns, internal_prefixes=(),
                                 maximum_tool_groups=8):
    """Retain recent source receipts as tool messages with complete call pairs.

    Old tool groups are dropped together, never recast as user instructions.
    No summarizer, persistent memory, remote calls or permission changes.
    """
    if not messages:
        return []
    eligible = []
    for index, item in enumerate(messages[1:], 1):
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role == "tool" or (role == "assistant" and item.get("tool_calls")):
            eligible.append(index)
        elif content and not any(content.startswith(prefix) for prefix in internal_prefixes):
            eligible.append(index)
    users = [i for i in eligible if messages[i].get("role") == "user"]
    start = users[-maximum_turns] if len(users) >= maximum_turns else 1
    eligible = {i for i in eligible if i >= start}
    groups, active = [], None
    for index, item in enumerate(messages[1:], 1):
        if item.get("role") == "assistant" and item.get("tool_calls"):
            ids = {str(call.get("id") or "") for call in item["tool_calls"]}
            active = {"indices": {index}, "ids": ids, "replied": set()}
            groups.append(active)
        elif item.get("role") == "tool" and active is not None:
            identity = str(item.get("tool_call_id") or "")
            if identity in active["ids"]:
                active["indices"].add(index)
                active["replied"].add(identity)
    kept = set()
    complete = [group for group in groups
                if group["ids"] == group["replied"] and group["indices"] <= eligible]
    for group in complete[-maximum_tool_groups:]:
        kept.update(group["indices"])
    return [messages[0], *[
        messages[i] for i in sorted(eligible)
        if (messages[i].get("role") != "tool"
            and not (messages[i].get("role") == "assistant" and messages[i].get("tool_calls")))
        or i in kept
    ]]


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
