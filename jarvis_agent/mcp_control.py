"""Serialized explicit MCP configuration commands for the Jarvis worker.

The GUI never spawns an MCP server or opens an HTTP session itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
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
    value: str = field(default="", repr=False)


class MCPControlInbox:
    OPS = frozenset({
        "add_http", "add_hermes", "discover", "enable", "disable",
        "allow_tool", "deny_tool", "add_stdio", "revoke_tools", "remove_server",
        "store_bearer", "forget_credentials", "oauth_authorize", "cancel_oauth", "set_quotas"
    })

    def __init__(self):
        self._queue: queue.Queue[MCPCommand] = queue.Queue(maxsize=16)
        self.changed = threading.Event()
        self.stop_requested = threading.Event()
        self._authorization_lock = threading.Lock()
        self._authorization_server = ""

    def submit(self, operation: str, server_id: str = "", value: str = "") -> bool:
        op = str(operation or "").strip()
        sid = str(server_id or "").strip().lower()
        val = str(value or "").strip()
        if op not in self.OPS or len(val) > (12000 if op in {"add_stdio", "store_bearer", "oauth_authorize"} else 600):
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
        elif op == "add_stdio":
            try:
                packet = json.loads(val)
                if not isinstance(packet, dict) or set(packet) - {"command", "args", "trusted", "env_refs"}:
                    return False
            except (ValueError, TypeError):
                return False
        elif op == "store_bearer":
            if not val or any(c in val for c in "\r\n\x00"):
                return False
        elif op == "set_quotas":
            try:
                from .mcp_activity import quota_limits
                packet = json.loads(val)
                if not isinstance(packet, dict) or set(packet) != {"sessions_per_hour", "calls_per_hour"}:
                    return False
                quota_limits({"quotas": packet})
            except (ValueError, TypeError):
                return False
        elif op == "oauth_authorize":
            try:
                from .mcp_oauth import oauth_options
                oauth_options(val)
            except (ValueError, TypeError):
                return False
        elif val:
            return False
        with self._authorization_lock:
            if op == "cancel_oauth":
                if sid != self._authorization_server:
                    return False
                self.stop_requested.set()
                return True
            if self._authorization_server:
                return False
            try:
                self._queue.put_nowait(MCPCommand(op, sid, val))
            except queue.Full:
                return False
            if op == "oauth_authorize":
                self.stop_requested.clear()
                self._authorization_server = sid
        self.changed.set()
        return True

    def finish_authorization(self):
        with self._authorization_lock:
            self._authorization_server = ""

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
    vault: Any | None = None,
    stop_event=None,
) -> dict[str, Any]:
    """No model calls; mutations only on explicit UI commands."""
    op = command.operation
    sid = command.server_id
    if op in {"store_bearer", "forget_credentials", "remove_server", "oauth_authorize"}:
        from .mcp_credentials import CredentialVault
        vault = vault or CredentialVault(registry.path.parent / "mcp_credentials")
    if op == "store_bearer":
        entry = registry.get_server(sid)
        if not entry or entry.get("kind") != "http":
            raise ValueError("http_server_required_for_bearer")
        if not command.value or any(c in command.value for c in "\r\n\x00"):
            raise ValueError("invalid_mcp_bearer")
        vault.write(sid, entry["url"], command.value)
        registry.set_credential_source(sid, "vault")
    elif op == "forget_credentials":
        entry = registry.get_server(sid)
        if not entry or entry.get("kind") != "http":
            raise ValueError("http_server_required_for_bearer")
        registry.set_enabled(sid, False)
        registry.revoke_tools(sid)
        registry.set_credential_source(sid, "none")
        vault.forget(sid)
    elif op == "add_http":
        registry.add_http(sid, command.value)
    elif op == "add_hermes":
        registry.add_hermes_local()
    elif op == "add_stdio":
        packet = json.loads(command.value)
        registry.add_stdio(sid, packet["command"], packet["args"],
                           trusted=packet.get("trusted") is True, env_refs=packet.get("env_refs"))
    elif op == "revoke_tools":
        registry.revoke_tools(sid)
    elif op == "set_quotas":
        registry.set_quotas(sid, json.loads(command.value))
    elif op == "remove_server":
        entry = registry.get_server(sid)
        if entry and entry.get("credential_source") in {"vault", "oauth", "none"}:
            registry.set_enabled(sid, False)
            registry.revoke_tools(sid)
            vault.forget(sid)
        registry.remove_server(sid)
    elif op == "oauth_authorize":
        from .mcp_oauth import oauth_options
        options = oauth_options(command.value)
        entry = registry.get_server(sid)
        if not entry or entry.get("kind") != "http":
            raise ValueError("http_server_required_for_oauth")
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("oauth_cancelled")
        # Authentication never grants tools or enables a server implicitly.
        registry.set_enabled(sid, False)
        registry.revoke_tools(sid)
        provider = transport or OfficialMCPTransport()
        activity = registry.activity()
        attempt = activity.reserve(sid, "oauth_authorize", entry)
        try:
            discovered = provider.authorize({**entry, "id": sid,
                "_credential_root": str(registry.path.parent / "mcp_credentials")},
                vault=vault, stop_event=stop_event, options=options)
        except Exception:
            activity.finish(attempt, "cancelled" if stop_event is not None and stop_event.is_set() else "failed")
            raise
        activity.finish(attempt, "success")
        registry.set_credential_source(sid, "oauth")
        registry.discover(sid, discovered)
        return {"success": True, "operation": op, "server_id": sid,
                "message": "Consentement OAuth stocke. Serveur et outils restent desactives."}
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
        activity = registry.activity()
        attempt = activity.reserve(sid, "discover", entry)
        try:
            discovered = provider.discover({**entry, "id": sid,
                                          "_credential_root": str(registry.path.parent / "mcp_credentials")})
        except Exception:
            activity.finish(attempt, "failed")
            raise
        activity.finish(attempt, "success")
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
