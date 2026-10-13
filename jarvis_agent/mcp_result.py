"""Contract for Jarvis MCP transport envelopes, not raw protocol CallToolResult."""
from __future__ import annotations


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
