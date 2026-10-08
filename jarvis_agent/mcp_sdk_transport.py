"""Optional official MCP SDK client, not an embedded Hermes runtime.

MCP server configurations are explicitly consented to by the user.
No connection, process spawn or network request occurs at import/startup.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any


class MCPUnavailable(RuntimeError):
    pass


class OfficialMCPTransport:
    """Short-lived MCP session; never retry a side effect after uncertainty."""

    @staticmethod
    async def _with_session(entry: dict, operation: str, *,
                            tool: str = "", arguments: dict | None = None) -> Any:
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise MCPUnavailable("optional_mcp_sdk_not_installed") from exc

        async def proceed(read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if operation == "discover":
                    listing = await session.list_tools()
                    found = list(listing.tools)
                    seen = set()
                    for _ in range(8):  # bounded paging
                        cursor = getattr(listing, "nextCursor", None)
                        if not cursor or cursor in seen or len(found) >= 150:
                            break
                        seen.add(cursor)
                        listing = await session.list_tools(cursor=cursor)
                        found.extend(listing.tools)
                    return [
                        {
                            "name": str(item.name),
                            "description": str(item.description or "")[:400],
                            "input_schema": getattr(item, "inputSchema", {}) or {},
                        }
                        for item in found[:150]
                    ]
                if operation == "call":
                    reply = await session.call_tool(tool, arguments=arguments or {})
                    # Tool success is NOT whole-mission verification.
                    content = []
                    for part in list(getattr(reply, "content", []) or [])[:8]:
                        text = getattr(part, "text", None)
                        if isinstance(text, str):
                            content.append(text[:1500])
                    structured = getattr(reply, "structuredContent", None)
                    if not isinstance(structured, dict):
                        structured = None
                    return {
                        "success": not bool(getattr(reply, "isError", False)),
                        "message": "\n".join(content)[:2800],
                        "data": structured,
                    }
                raise ValueError("invalid_mcp_operation")

        kind = str(entry.get("kind") or "")
        if kind == "stdio":
            # A stdio command is ONLY launched after explicit discovery or
            # an already approved tool call. Never interpolate with a shell.
            if entry.get("command") != "hermes" or entry.get("args") != ["mcp", "serve"]:
                raise MCPUnavailable("stdio_profile_not_trusted")
            params = StdioServerParameters(
                command="hermes", args=["mcp", "serve"], env=None,
            )
            async with stdio_client(params) as (read, write):
                return await proceed(read, write)
        if kind == "http":
            from .mcp_server_registry import _valid_remote
            url = str(entry.get("url") or "")
            if not _valid_remote(url):
                raise MCPUnavailable("invalid_mcp_remote_url")
            # Env credentials are not stored in mcp_servers.json or UI.
            server_id = str(entry.get("id") or "")
            env_key = "JARVIS_MCP_BEARER_" + server_id.upper()
            bearer = os.getenv(env_key, "").strip()
            headers = {"Authorization": "Bearer " + bearer} if bearer else None
            async with streamablehttp_client(url, headers=headers) as streams:
                return await proceed(streams[0], streams[1])
        raise MCPUnavailable("unknown_mcp_transport")

    def invoke(self, entry: dict, operation: str, *,
               tool: str = "", arguments: dict | None = None) -> Any:
        timeout = max(3, min(45, int(os.getenv("JARVIS_MCP_TIMEOUT_SECONDS", "18"))))
        try:
            return asyncio.run(asyncio.wait_for(
                self._with_session(entry, operation, tool=tool,
                                   arguments=arguments), timeout=timeout
            ))
        except (TimeoutError, OSError, RuntimeError) as exc:
            # No automatic retry. For a *call* the remote side effect may
            # already have happened, even if a response was lost.
            raise MCPUnavailable(type(exc).__name__) from exc

    def discover(self, entry: dict) -> list[dict]:
        return self.invoke(entry, "discover")

    def call_tool(self, entry: dict, tool: str, arguments: dict) -> dict:
        return self.invoke(entry, "call", tool=tool, arguments=arguments)
