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
        "store_bearer", "store_api_key", "forget_credentials", "oauth_authorize", "cancel_oauth", "set_quotas",
        "runtime_set", "connect", "reconnect", "disconnect", "review_outcome"
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
        if op not in self.OPS or len(val) > (16000 if op in {"add_stdio", "store_bearer", "store_api_key", "oauth_authorize"} else 600):
            return False
        if op == "runtime_set":
            if sid or val not in {"true", "false"}:
                return False
        elif op == "add_hermes":
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
        elif op == "store_api_key":
            try:
                api_key_options(val)
            except ValueError:
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
        elif op == "review_outcome":
            if not re.fullmatch(r"[1-9][0-9]{0,18}", val):
                return False
        elif op == "oauth_authorize":
            try:
                from .mcp_oauth import oauth_options
                oauth_options(val)
            except (ValueError, TypeError):
                return False
        elif op != "runtime_set" and val:
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


def api_key_options(value: str) -> dict:
    try:
        packet = json.loads(value)
        if (not isinstance(packet, dict) or set(packet) != {"header", "key"}
                or packet["header"] not in {"X-API-Key", "X-Goog-Api-Key"}
                or not isinstance(packet["key"], str) or not 1 <= len(packet["key"]) <= 12000
                or any(c in packet["key"] for c in "\r\n\x00")):
            raise ValueError
        return packet
    except (ValueError, TypeError, KeyError):
        raise ValueError("invalid_mcp_api_key") from None


def connection_failure_state(exc: Exception) -> str:
    """Classify status without retaining remote bodies, URLs, or secrets."""
    from .mcp_oauth import OAuthConsentRequired
    from .mcp_credentials import CredentialUnavailable
    pending, seen = [exc], set()
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if (isinstance(item, (OAuthConsentRequired, CredentialUnavailable))
                or getattr(getattr(item, "response", None), "status_code", None) in {401, 403}
                or isinstance(item, RuntimeError) and str(item) == "mcp_secure_credential_missing"):
            return "authorization_required"
        pending.extend(getattr(item, "exceptions", ()))
        if item.__cause__ is not None:
            pending.append(item.__cause__)
    return "transport_error"


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
    if op in {"store_bearer", "store_api_key", "forget_credentials", "remove_server", "oauth_authorize"}:
        from .mcp_credentials import CredentialVault
        vault = vault or CredentialVault(registry.path.parent / "mcp_credentials")
    if op == "runtime_set":
        if sid or command.value not in {"true", "false"}:
            raise ValueError("invalid_mcp_runtime_flag")
        registry.set_runtime_enabled(command.value == "true")
    elif op == "review_outcome":
        registry.activity().review_outcome(sid, int(command.value))
        return {"success": True, "operation": op, "server_id": sid,
                "message": "Revue utilisateur enregistree ; aucun appel MCP relance, aucune preuve finale ajoutee."}
    elif op == "store_api_key":
        packet = api_key_options(command.value)
        entry = registry.get_server(sid)
        if not entry or entry.get("kind") != "http":
            raise ValueError("http_server_required_for_api_key")
        # A credential change invalidates earlier tool consent and health.
        registry.set_enabled(sid, False)
        registry.revoke_tools(sid)
        registry.note_connection_state(sid, "disconnected")
        vault.write(sid, entry["url"], packet["key"], "api_key")
        registry.set_api_key_header(sid, packet["header"])
    elif op == "store_bearer":
        entry = registry.get_server(sid)
        if not entry or entry.get("kind") != "http":
            raise ValueError("http_server_required_for_bearer")
        if not command.value or any(c in command.value for c in "\r\n\x00"):
            raise ValueError("invalid_mcp_bearer")
        registry.set_enabled(sid, False)
        registry.revoke_tools(sid)
        registry.note_connection_state(sid, "disconnected")
        vault.write(sid, entry["url"], command.value)
        registry.set_credential_source(sid, "vault")
    elif op == "forget_credentials":
        entry = registry.get_server(sid)
        if not entry or entry.get("kind") != "http":
            raise ValueError("http_server_required_for_bearer")
        registry.set_enabled(sid, False)
        registry.revoke_tools(sid)
        registry.note_connection_state(sid, "disconnected")
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
        if entry and entry.get("credential_source") in {"vault", "api_key", "oauth", "none"}:
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
        registry.note_connection_state(sid, "tested_session_closed")
        return {"success": True, "operation": op, "server_id": sid,
                "message": "Consentement OAuth stocke. Serveur et outils restent desactives."}
    elif op == "enable":
        registry.set_enabled(sid, True)
    elif op in {"disable", "disconnect"}:
        registry.set_enabled(sid, False)
        registry.note_connection_state(sid, "disconnected")
        if op == "disconnect":
            registry.revoke_tools(sid)
    elif op in {"allow_tool", "deny_tool"}:
        registry.allow_tool(sid, command.value, op == "allow_tool")
    elif op in {"discover", "connect", "reconnect"}:
        entry = registry.get_server(sid)
        if entry is None:
            raise KeyError("mcp_server_not_found")
        if op in {"connect", "reconnect"}:
            registry.set_enabled(sid, True)
            entry = registry.get_server(sid)
        elif entry.get("enabled") is not True:
            raise RuntimeError("enable_server_before_discovery")
        provider = transport or OfficialMCPTransport()
        activity = registry.activity()
        attempt = None
        try:
            attempt = activity.reserve(sid, "discover", entry)
            target = {**entry, "id": sid, "_credential_root": str(registry.path.parent / "mcp_credentials")}
            inventory = provider.inventory(target) if callable(getattr(provider, "inventory", None)) else {
                "tools": provider.discover(target), "capabilities": {"tools": True}}
            count = registry.discover(sid, inventory["tools"])
            registry.note_inventory(sid, inventory)
            registry.note_successful_discovery(sid)
            registry.note_connection_state(sid, "tested_session_closed")
        except Exception as exc:
            # Stale inventory must not remain available after a failed test.
            registry.set_enabled(sid, False)
            registry.note_connection_state(sid, connection_failure_state(exc))
            if attempt is not None:
                try:
                    activity.finish(attempt, "failed")
                except Exception:
                    pass
            raise
        activity.finish(attempt, "success")
        return {
            "success": True, "operation": op, "server_id": sid,
            "tool_count": count,
            "message": "Connexion testee ; session fermee. Capacites decouvertes, nouveaux outils non autorises.",
        }
    else:
        raise ValueError("unknown_mcp_command")
    return {
        "success": True, "operation": op, "server_id": sid,
        "message": "Configuration mise à jour. Aucun outil MCP exécuté.",
    }
