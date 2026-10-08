"""Typed MCP management messages; worker-only, not an LLM tool."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import re
import threading
from typing import Any

from .mcp_hub import MCP_HUB, MCPHub, MCPHubError


@dataclass(frozen=True)
class MCPControl:
    operation: str
    parameters: dict[str, Any]


class MCPControlInbox:
    def __init__(self):
        self._queue: queue.Queue[MCPControl] = queue.Queue(maxsize=20)

    def submit(self, operation: str, params: dict | None = None) -> bool:
        op = str(operation or "").strip().lower()
        args = dict(params or {})
        if op not in {"save", "enable", "disable", "discover"}:
            return False
        if len(str(args)) > 3000:
            return False
        if op != "save" and not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_.-]{0,63}", str(args.get("id") or "")
        ):
            return False
        try:
            self._queue.put_nowait(MCPControl(op, args))
        except queue.Full:
            return False
        return True

    def pop_nowait(self) -> MCPControl | None:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def pending(self) -> bool:
        return not self._queue.empty()


def perform_mcp_control(command: MCPControl, hub: MCPHub | None = None) -> dict:
    """UI management only. Never invokes any arbitrary MCP tool."""
    target = hub or MCP_HUB
    op = command.operation
    p = command.parameters
    sid = str(p.get("id") or "")
    try:
        if op == "save":
            outcome = target.save_server(
                server_id=sid,
                server_url=str(p.get("url") or ""),
                authorization_env=str(p.get("auth_env") or ""),
                allowed_tools=list(p.get("tools") or []),
            )
        elif op in ("enable", "disable"):
            outcome = target.set_enabled(sid, op == "enable")
        elif op == "discover":
            outcome = target.list_tools(sid)
        else:
            return {"success": False, "operation": op, "reason": "invalid_command"}
        return {"success": True, "operation": op, "server_id": sid,
                "result": outcome}
    except MCPHubError as exc:
        return {"success": False, "operation": op, "server_id": sid,
                "reason": str(exc)[:100]}
    except Exception:
        # Never surface token, backend URL or remote exception body.
        return {"success": False, "operation": op, "server_id": sid,
                "reason": "mcp_operation_failed"}
