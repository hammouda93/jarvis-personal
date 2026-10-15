"""Contract for Jarvis MCP transport envelopes, not raw protocol CallToolResult."""
from __future__ import annotations

import re
import json


def receipt_metadata(raw, *, name="") -> dict:
    """Small local status for diagnostics; external content stays in context."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        return {"content_omitted": True}
    result = {"content_omitted": True}
    for key in ("verified", "dispatched", "outcome_unknown", "arguments_empty",
                "blocked_by_previous_attempt", "reviewable", "output_truncated",
                "archive_truncated", "historical_observation", "permissions_changed"):
        if type(data.get(key)) is bool:
            result[key] = data[key]
    for key in ("previous_attempt_id", "retry_after_seconds", "offset", "next_offset", "total_chars"):
        if type(data.get(key)) is int and 0 <= data[key] < 10 ** 12:
            result[key] = data[key]
    for key in ("guard", "budget_reason", "previous_outcome"):
        if isinstance(data.get(key), str) and re.fullmatch(r"[a-z_]{1,100}", data[key]):
            result[key] = data[key]
    source = data.get("source")
    if isinstance(source, dict):
        server, tool = source.get("server_id"), source.get("tool_name")
        alias = ("mcp__" + str(server) + "__"
                 + re.sub(r"[^A-Za-z0-9_]", "_", str(tool)))
        if source_matches_alias(name if str(name).startswith("mcp__") else alias, source):
            result["source"] = {"server_id": server, "tool_name": tool,
                                "trust": "external_unverified"}
            attempt = source.get("attempt_id")
            if type(attempt) is int and 0 < attempt < 10 ** 12:
                result["source"]["attempt_id"] = attempt
    return result


def source_matches_alias(name, source) -> bool:
    """Match a local gateway source to its sanitized model-facing alias."""
    if not isinstance(source, dict) or source.get("trust") != "external_unverified":
        return False
    server, tool = source.get("server_id"), source.get("tool_name")
    if (not isinstance(server, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,35}", server)
            or not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", tool)):
        return False
    return name == "mcp__" + server + "__" + re.sub(r"[^A-Za-z0-9_]", "_", tool)


def transport_outcome(result, *, confirmed_read_only: bool = False) -> tuple[bool, bool]:
    """Return (success, uncertain) without mistaking a tool error for a timeout.

    An explicit MCP isError response is a *known failed tool call*. Yet a
    mutation may have partly succeeded before the error, so retain the
    conservative unknown-effect lock unless the operator had approved this
    capability as a fingerprint-pinned observational read.
    """
    if not isinstance(result, dict) or type(result.get("success")) is not bool:
        return False, True
    if "outcome_unknown" in result and type(result["outcome_unknown"]) is not bool:
        return False, True
    if result.get("outcome_unknown") is True:
        return False, True
    if result["success"] is False:
        return False, not confirmed_read_only
    return True, False
