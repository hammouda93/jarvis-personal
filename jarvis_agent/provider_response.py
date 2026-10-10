"""Validate a complete local-tool response before any tool in it is dispatched."""
from __future__ import annotations

import json


def _unique_fields(pairs):
    fields = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError("duplicate_tool_argument")
        fields[key] = value
    return fields


def _reject_constant(value):
    raise ValueError("nonfinite_tool_argument")


def response_integrity_error(choice) -> str:
    if getattr(choice, "finish_reason", None) in {"length", "content_filter", "error", "cancelled"}:
        return "provider_response_incomplete"
    message = getattr(choice, "message", None)
    if message is None:
        return "provider_response_incomplete"
    seen = set()
    for call in getattr(message, "tool_calls", None) or ():
        identity = getattr(call, "id", None)
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        if (not isinstance(identity, str) or not identity.strip() or identity in seen
                or not isinstance(name, str) or not name.strip()):
            return "provider_response_invalid_tool_identity"
        seen.add(identity)
        arguments = getattr(function, "arguments", None)
        try:
            parsed = json.loads(arguments, object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
        except (TypeError, ValueError):
            return "provider_response_invalid_tool_arguments"
        if not isinstance(parsed, dict):
            return "provider_response_invalid_tool_arguments"
    return ""
