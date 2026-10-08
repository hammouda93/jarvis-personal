"""Serialized explicit MCP configuration commands for the Jarvis worker.

The GUI never spawns an MCP server or opens an HTTP session itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import queue
import re
import threading
from typing import Any

from .mcp_server_registry import MCPRegistry
from .mcp_sdk_transport import OfficialMCPTransport


@dataclass(frozen=True)
class MCPCommand:
    operation: str
    server_id: str = ""
    value: str = ""


class MCPControlInbox:
    OPS = frozenset({
        "add_http", "add_hermes", "discover", "enable", "disable",
        "allow_tool", "deny_tool"
    })

    def __init__(self):
        self._queue: queue.Queue[MCPCommand] = queue.Queue(maxsize=16)
        self.changed = threading.Event()

    def submit(self, operation: str, server_id: str = "", value: str = "") -> bool:
        op = str(operation or "").strip()
        sid = str(server_id or "").strip().lower()
        val = str(value or "").strip()
        if op not in self.OPS or len(val) > 600:
            return False
        if op == "add_hermes":
            if sid not in ("", "hermes") or val:
                return False
            sid = "hermes"
        elif not re.fullmatch(r"[a-z][a-z0-9_]{0,35}", sid):
            return False
        if op in {"allow_tool", "deny_tool"}:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", val):
                return False
        elif op == "add_http":
            if not val:
                return False
        elif val:
            return False
        try:
            self._queue.put_nowait(MCPCommand(op, sid, val))
        except queue.Full:
            return False
        self.changed.set()
        return True

    def pop_nowait(self) -> MCPCommand | None:
        try:
            value = self._queue.get_nowait()
        except queue.Empty:
            self.changed.clear()
            return None
        if self._queue.empty():
            self.changed.clear()
        return value


def perform_mcp_command(
    registry: MCPRegistry,
    command: MCPCommand,
    *,
    transport: Any | None = None,
) -> dict[str, Any]:
    """No model calls; mutations only on explicit UI commands."""
    op = command.operation
    sid = command.server_id
    if op == "add_http":
        registry.add_http(sid, command.value)
    elif op == "add_hermes":
        registry.add_hermes_local()
    elif op == "enable":
        registry.set_enabled(sid, True)
    elif op == "disable":
        registry.set_enabled(sid, False)
    elif op in {"allow_tool", "deny_tool"}:
        registry.allow_tool(sid, command.value, op == "allow_tool")
    elif op == "discover":
        entry = registry.get_server(sid)
        if entry is None:
            raise KeyError("mcp_server_not_found")
        if entry.get("enabled") is not True:
            raise RuntimeError("enable_server_before_discovery")
        provider = transport or OfficialMCPTransport()
        discovered = provider.discover({"id": sid, **entry})
        count = registry.discover(sid, discovered)
        registry.note_successful_discovery(sid)
        return {
            "success": True, "operation": op, "server_id": sid,
            "tool_count": count,
            "message": "Outils détectés, tous nouveaux outils non autorisés.",
        }
    else:
        raise ValueError("unknown_mcp_command")
    return {
        "success": True, "operation": op, "server_id": sid,
        "message": "Configuration mise à jour. Aucun outil MCP exécuté.",
    }
