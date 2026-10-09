"""Optional official MCP SDK client, not an embedded Hermes runtime.

MCP server configurations are explicitly consented to by the user.
No connection, process spawn or network request occurs at import/startup.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any


async def session_inventory(session, initialized, *, tools_only=False) -> dict:
    """Read bounded metadata only; never fetch a resource or execute a prompt."""
    from mcp.types import PaginatedRequestParams
    capabilities = getattr(initialized, "capabilities", None)
    declared = {name: getattr(capabilities, name, None) is not None
                for name in ("tools", "resources", "prompts")}
    if capabilities is None:
        declared["tools"] = True  # compatibility with older injected sessions
    result = {"tools": [], "resources": [], "prompts": [], "resource_templates": [],
              "capabilities": declared, "truncated": []}
    methods = [("tools", "list_tools", "tools")]
    if not tools_only:
        methods += [("resources", "list_resources", "resources"),
                    ("resources", "list_resource_templates", "resource_templates"),
                    ("prompts", "list_prompts", "prompts")]
    for capability, method_name, field in methods:
        if not declared[capability]:
            continue
        method = getattr(session, method_name)
        cursor, seen, found = None, set(), []
        for _ in range(9):
            listing = await method() if cursor is None else await method(params=PaginatedRequestParams(cursor=cursor))
            found.extend(getattr(listing, field, None) or
                         getattr(listing, "resourceTemplates", None) or [])
            next_cursor = getattr(listing, "next_cursor", None) or getattr(listing, "nextCursor", None)
            if not next_cursor:
                break
            if next_cursor in seen or len(found) >= 150:
                result["truncated"].append(field)
                break
            seen.add(next_cursor)
            cursor = next_cursor
        else:
            result["truncated"].append(field)
        if len(found) > 150 and field not in result["truncated"]:
            result["truncated"].append(field)
        for item in found[:150]:
            record = {"name": str(getattr(item, "name", ""))[:100],
                      "description": str(getattr(item, "description", "") or "")[:400]}
            if field == "tools":
                record["input_schema"] = getattr(item, "input_schema", None) or getattr(item, "inputSchema", {}) or {}
            elif field == "resources":
                record["uri"] = str(getattr(item, "uri", ""))[:1000]
            elif field == "resource_templates":
                record["uri_template"] = str(getattr(item, "uri_template", None) or getattr(item, "uriTemplate", ""))[:1000]
            elif field == "prompts":
                record["arguments"] = [{"name": str(getattr(a, "name", ""))[:100],
                                        "required": getattr(a, "required", False) is True}
                                       for a in (getattr(item, "arguments", []) or [])[:30]]
            result[field].append(record)
    return result


class MCPUnavailable(RuntimeError):
    def __init__(self, message: str, *, outcome_unknown: bool = False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


class OfficialMCPTransport:
    """Short-lived MCP session; never retry a side effect after uncertainty."""

    @staticmethod
    async def _with_session(entry: dict, operation: str, *,
                            tool: str = "", arguments: dict | None = None,
                            oauth_flow=None, vault=None) -> Any:
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise MCPUnavailable("optional_mcp_sdk_not_installed") from exc

        async def proceed(read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                if operation in {"discover", "inventory"}:
                    inventory = await session_inventory(session, initialized, tools_only=operation == "discover")
                    return inventory["tools"] if operation == "discover" else inventory
                if operation == "call":
                    reply = await session.call_tool(tool, arguments=arguments or {})
                    # Tool success is NOT whole-mission verification.
                    content = []
                    for part in list(getattr(reply, "content", []) or [])[:8]:
                        text = getattr(part, "text", None)
                        if isinstance(text, str):
                            content.append(text[:1500])
                    structured = (
                        getattr(reply, "structured_content", None)
                        or getattr(reply, "structuredContent", None)
                    )
                    if not isinstance(structured, dict):
                        structured = None
                    return {
                        "success": not bool(
                            getattr(reply, "is_error", None)
                            if getattr(reply, "is_error", None) is not None
                            else getattr(reply, "isError", False)
                        ),
                        "message": "\n".join(content)[:2800],
                        "data": structured,
                    }
                raise ValueError("invalid_mcp_operation")

        kind = str(entry.get("kind") or "")
        if kind == "stdio":
            # A stdio command is ONLY launched after explicit discovery or
            # an already approved tool call. Never interpolate with a shell.
            from .mcp_security import stdio_command, stdio_environment
            try:
                env = stdio_environment(entry)
                command = stdio_command(entry)
            except ValueError as exc:
                raise MCPUnavailable(str(exc)) from exc
            # Avoid giving a server the repository's .env through its cwd or
            # leaking arbitrary server stderr into the conversation/log files.
            with tempfile.TemporaryDirectory(prefix="jarvis_mcp_") as workdir, open(os.devnull, "w") as errlog:
                params = StdioServerParameters(command=command, args=entry["args"], env=env, cwd=workdir)
                async with stdio_client(params, errlog=errlog) as (read, write):
                    return await proceed(read, write)
        if kind == "http":
            from .mcp_server_registry import _valid_remote
            url = str(entry.get("url") or "")
            if not _valid_remote(url):
                raise MCPUnavailable("invalid_mcp_remote_url")
            # Env credentials are not stored in mcp_servers.json or UI.
            server_id = str(entry.get("id") or "")
            env_key = "JARVIS_MCP_BEARER_" + server_id.upper()
            source = entry.get("credential_source", "environment")
            auth = None
            headers = {}
            if source == "vault":
                from .mcp_credentials import CredentialUnavailable, CredentialVault
                try:
                    bearer = CredentialVault(entry.get("_credential_root")).read(server_id, url)
                except CredentialUnavailable as exc:
                    raise MCPUnavailable(str(exc)) from exc
                if not bearer:
                    raise MCPUnavailable("mcp_secure_credential_missing")
            elif source == "environment":
                bearer = os.getenv(env_key, "").strip()
            elif source == "none":
                bearer = ""
            elif source == "api_key":
                from .mcp_credentials import CredentialVault
                header = entry.get("api_key_header")
                if header not in {"X-API-Key", "X-Goog-Api-Key"}:
                    raise MCPUnavailable("invalid_mcp_api_key_header")
                value = CredentialVault(entry.get("_credential_root")).read(server_id, url, "api_key")
                if not value or any(c in value for c in "\r\n\x00"):
                    raise MCPUnavailable("mcp_secure_credential_missing")
                headers[header] = value
                bearer = ""
            elif source == "oauth":
                from .mcp_oauth import build_oauth_provider
                auth, storage = build_oauth_provider(entry, flow=oauth_flow, vault=vault)
                if oauth_flow is not None:
                    oauth_flow.storage = storage
                bearer = ""
            else:
                raise MCPUnavailable("unknown_mcp_credential_source")
            if any(c in bearer for c in "\r\n\x00"):
                raise MCPUnavailable("invalid_mcp_bearer")
            # The SDK follows selected same-origin redirects independently of
            # httpx's follow_redirects. The request hook gates those too.
            import httpx2
            from .mcp_oauth import guarded_http_request
            async def guard(request):
                await guarded_http_request(request, url, oauth=auth is not None)
            if bearer:
                headers["Authorization"] = "Bearer " + bearer
            async with httpx2.AsyncClient(headers=headers, auth=auth, trust_env=False,
                    follow_redirects=False, event_hooks={"request": [guard]}) as http_client:
                async with streamable_http_client(url, http_client=http_client) as streams:
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
        except MCPUnavailable:
            raise
        except (TimeoutError, OSError, RuntimeError) as exc:
            # No automatic retry. For a *call* the remote side effect may
            # already have happened, even if a response was lost.
            raise MCPUnavailable(type(exc).__name__, outcome_unknown=operation == "call") from exc

    def discover(self, entry: dict) -> list[dict]:
        return self.invoke(entry, "discover")

    def inventory(self, entry: dict) -> dict:
        return self.invoke(entry, "inventory")

    def call_tool(self, entry: dict, tool: str, arguments: dict) -> dict:
        return self.invoke(entry, "call", tool=tool, arguments=arguments)

    def authorize(self, entry: dict, *, stop_event=None, vault=None, opener=None, options=None) -> list[dict]:
        from .mcp_oauth import LoopbackOAuth, until_cancelled
        async def connect():
            async with LoopbackOAuth(entry["url"], opener=opener, options=options) as flow:
                result = await self._with_session({**entry, "credential_source": "oauth"},
                    "discover", oauth_flow=flow, vault=vault)
                flow.storage.commit()
                return result
        return asyncio.run(until_cancelled(connect(), stop_event))
