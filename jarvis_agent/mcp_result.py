"""Contract for Jarvis MCP transport envelopes, not raw protocol CallToolResult."""
from __future__ import annotations


def transport_outcome(result) -> tuple[bool, bool]:
    """Return (success, uncertain) after dispatch; never infer atomic rollback."""
    if not isinstance(result, dict) or type(result.get("success")) is not bool:
        return False, True
    if "outcome_unknown" in result and type(result["outcome_unknown"]) is not bool:
        return False, True
    if result["success"] is not True or result.get("outcome_unknown") is True:
        # A server error can occur after a partial mutation. Only transport
        # preflight exceptions can establish that execution did not start.
        return False, True
    return True, False
