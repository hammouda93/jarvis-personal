"""Opt-in MCP clients for Jarvis and Hermes channel bridge.

Official MCP wire protocol via the optional 'mcp' Python SDK.
Hermes itself stays a SEPARATE process (when locally installed by the user);
Jarvis keeps the single authoritative planning/execution runtime.

See: https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp
Hermes Agent is MIT-licensed; this bridge is original Jarvis code.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import re
import secrets
from typing import Any

from .mcp_hub import MCPHub, HUB


HERMES_CHANNEL_TOOLS = frozenset({
    "conversations_list", "conversation_get", "messages_read",
    "attachments_fetch", "events_poll", "events_wait", "messages_send",
    "channels_list", "permissions_list_open", "permissions_respond",
})
# External mutations must never run unattended. Hermes also enforces its
# own approvals; Jarvis must not delegate away this boundary.
HERMES_MUTATING_TOOLS = frozenset({
    "messages_send", "permissions_respond",
})

def safe_hermes_environment() -> dict[str, str]:
    """Only safe process-basics; DO NOT inherit all Jarvis API credentials."""
    names = (
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
        "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG",
        "PYTHONIOENCODING", "VIRTUAL_ENV",
    )
    return {key: os.environ[key] for key in names if os.getenv(key)}


@asynccontextmanager
async def remote_mcp_session(connector: Any):
    """Connect to configured HTTPS Streamable HTTP MCP, lazy on probe only."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:
        raise RuntimeError("mcp_sdk_not_installed") from exc
    token = ""
    if getattr(connector, "authorization_env", None):
        token = str(os.getenv(connector.authorization_env) or "").strip()
        if not token:
            raise PermissionError("mcp_auth_missing")
    if token:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx_not_installed") from exc
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(15.0, read=45.0),
            follow_redirects=False,
            trust_env=False,  # avoid leaking bearer token to proxy env
        ) as http:
            async with streamable_http_client(
                connector.server_url, http_client=http
            ) as transport:
                async with ClientSession(transport[0], transport[1]) as session:
                    await session.initialize()
                    yield session
    else:
        async with streamable_http_client(connector.server_url) as transport:
            async with ClientSession(transport[0], transport[1]) as session:
                await session.initialize()
                yield session


@asynccontextmanager
async def hermes_stdio_session(executable: str):
    """Hermes's documented 'hermes mcp serve' messaging bridge only."""
    path = str(executable or "").strip()
    if not path or "\x00" in path or any(ch in path for ch in "\n\r"):
        raise ValueError("hermes_executable_required")
    # Do not accept arbitrary shell scripts or interpolated command lines.
    # A PATH-resolved "hermes" binary is permitted; any other executable
    # must be an existing absolute .exe/launcher path explicitly selected.
    if path != "hermes":
        selected = Path(path)
        if (not selected.is_absolute() or not selected.is_file()
                or selected.suffix.lower() not in {".exe", ".cmd", ".bat"}):
            raise ValueError("hermes_executable_must_be_local_program")
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise RuntimeError("mcp_sdk_not_installed") from exc
    params = StdioServerParameters(
        command=path, args=["mcp", "serve"], env=safe_hermes_environment(),
    )
    async with stdio_client(params) as transport:
        async with ClientSession(transport[0], transport[1]) as session:
            await session.initialize()
            yield session


def _visible_tools(raw: Any, allowed: set[str]) -> list[dict[str, str]]:
    entries = getattr(raw, "tools", None) or []
    found: list[dict[str, str]] = []
    for item in list(entries)[:256]:
        name = str(getattr(item, "name", "") or "")
        if name in allowed:
            found.append({
                "name": name[:128],
                "description": str(getattr(item, "description", "") or "")[:180],
            })
    return found[:64]


class MCPClientBridge:
    """Two compatible transports: Jarvis direct remote, Hermes messaging stdio.

    No implicit auth, discovery or tool calls when a Jarvis UI opens.
    """

    def __init__(self, *, hub: MCPHub | None = None,
                 remote_factory=None, hermes_factory=None,
                 ledger=None):
        self.hub = hub if hub is not None else HUB
        self._remote = remote_factory or remote_mcp_session
        self._hermes = hermes_factory or hermes_stdio_session
        self._ledger = ledger

    async def discover_remote(self, server_id: str) -> dict[str, Any]:
        connector = self.hub.describe(server_id)
        async with self._remote(connector) as session:
            raw = await asyncio.wait_for(session.list_tools(), timeout=45.0)
        tools = _visible_tools(raw, set(connector.allowed_tools))
        return {
            "source": "direct_mcp", "server_id": server_id,
            "status": "online_probed", "tools": tools,
            "discovered_count": len(tools),
            "verified_goal": False,
        }

    async def discover_hermes(self, *, executable: str = "hermes") -> dict[str, Any]:
        async with self._hermes(executable) as session:
            raw = await asyncio.wait_for(session.list_tools(), timeout=45.0)
        tools = _visible_tools(raw, set(HERMES_CHANNEL_TOOLS))
        return {
            "source": "hermes_messaging_stdio", "server_id": "hermes_channels",
            "status": "online_probed", "tools": tools,
            "discovered_count": len(tools), "verified_goal": False,
            "scope": "messaging_only_not_all_hermes_connectors",
        }

    async def call_remote(self, *, server_id: str, tool: str,
                          arguments: dict[str, Any] | None = None,
                          approved: bool = False) -> dict[str, Any]:
        connector = self.hub.authorize_call(server_id, tool, approved=approved)
        return await self._guarded_call(
            "mcp:"+connector.connector_id+":"+tool,
            self._remote, connector, tool, dict(arguments or {}),
        )

    async def call_hermes(self, *, tool: str,
                          arguments: dict[str, Any] | None = None,
                          approved: bool = False,
                          executable: str = "hermes") -> dict[str, Any]:
        if tool not in HERMES_CHANNEL_TOOLS:
            raise PermissionError("hermes_tool_not_exposed")
        if not approved:
            raise PermissionError("mcp_explicit_approval_required")
        return await self._guarded_call(
            "hermes_mcp:"+tool, self._hermes,
            executable, tool, dict(arguments or {}),
        )

    async def _guarded_call(self, action_name: str, client_factory,
                            context_value, tool: str, arguments: dict[str, Any]):
        # Wire delivery may have an external side effect even if the TCP
        # connection disappears. Journal BEFORE the first external attempt.
        if self._ledger is None:
            from .hermes_reliability import ActionLedger
            self._ledger = ActionLedger()
        ledger = self._ledger
        action_id, unresolved = ledger.begin(
            turn_id="mcp_manual_"+secrets.token_hex(8),
            name=action_name,
            arguments=arguments,
        )
        if not action_id:
            return {
                "success": False, "verified": False,
                "error": "prior_outcome_unknown",
                "action_ref": unresolved,
            }
        try:
            async with client_factory(context_value) as session:
                raw = await asyncio.wait_for(
                    session.call_tool(tool, arguments), timeout=60.0
                )
        except Exception:
            ledger.finish(action_id, success=False, unknown=True)
            return {
                "success": False, "verified": False,
                "error": "mcp_outcome_unknown_requires_review",
                "action_ref": action_id,
            }
        success = not bool(getattr(raw, "isError", False))
        ledger.finish(action_id, success=success, verified=False)
        # Deliberately only structural metadata; full tool results can expose
        # private messages, contacts or third-party instructions.
        return {
            "success": success, "verified": False,
            "error": "" if success else "mcp_tool_reported_error",
            "action_ref": action_id,
            "result_type": type(raw).__name__[:60],
        }
