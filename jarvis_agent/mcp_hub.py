"""Optional MCP hub for Jarvis: per-server admission, tool allowlists and isolation.

The ChatGPT account's connectors are NOT Jarvis credentials. Remote sessions
use independent explicit provider tokens. No local stdio shell launch here.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import urllib.parse
from typing import Any

from .connectors import CONNECTORS, MCPConnector, ConnectorRegistry


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_TOOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_LOCK = threading.RLock()


class MCPHubError(RuntimeError):
    """Safe operator-visible error code, never secrets or remote exception text."""


def _validate_remote(url: str) -> None:
    parsed = urllib.parse.urlsplit(str(url))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise MCPHubError("https_endpoint_required")
    if parsed.fragment or parsed.query or parsed.port not in (None, 443):
        raise MCPHubError("endpoint_parameters_not_allowed")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise MCPHubError("private_endpoint_rejected")
    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
        if not ip.is_global:
            raise MCPHubError("private_endpoint_rejected")
    except ValueError:
        if "." not in hostname:
            raise MCPHubError("public_fqdn_required")


def _read_file(path: Path) -> dict:
    try:
        if not path.is_file():
            return {"connectors": []}
        if path.stat().st_size > 131072:
            raise MCPHubError("catalog_too_large")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("connectors"), list):
            raise MCPHubError("catalog_invalid")
        return raw
    except (OSError, ValueError, TypeError) as exc:
        raise MCPHubError("catalog_unavailable") from exc


class MCPHub:
    """No network from status/config operations; all server connections opt-in."""

    def __init__(self, registry: ConnectorRegistry | None = None):
        self.registry = registry or CONNECTORS

    def status(self) -> list[dict[str, Any]]:
        # All fields are nonsecret. Each MCP remains separately selectable.
        return [
            {
                "id": x.connector_id, "name": x.server_label,
                "enabled": x.enabled,
                "auth_ready": (
                    not x.authorization_env
                    or bool((os.getenv(x.authorization_env) or "").strip())
                ),
                "endpoint_kind": "tunnel" if x.tunnel_id else "remote",
                "approved_tools": list(x.allowed_tools[:24]),
                "approved_tool_count": len(x.allowed_tools),
                "approval": x.require_approval,
            }
            for x in self.registry.load()[:24]
        ]

    def save_server(
        self, *, server_id: str, server_url: str,
        allowed_tools: list[str], authorization_env: str = "",
    ) -> dict:
        """User-supplied metadata only; no tokens or arbitrary subprocess cmds."""
        sid = str(server_id or "").strip()
        if not _ID.fullmatch(sid):
            raise MCPHubError("invalid_server_id")
        _validate_remote(server_url)
        allowed = list(dict.fromkeys(str(name).strip() for name in allowed_tools))
        if not allowed or len(allowed) > 24 or any(not _TO.fullmatch(x) for x in allowed):
            raise MCPHubError("explicit_tool_allowlist_required")
        env_name = str(authorization_env or "").strip()
        if env_name and not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", env_name):
            raise MCPHubError("invalid_authorization_env")
        spec = MCPConnector.from_mapping({
            "id": sid, "server_label": sid, "server_url": server_url,
            "allowed_tools": allowed, "authorization_env": env_name or None,
            "require_approval": "always", "enabled": False,
        })
        with _LOCK:
            path = self.registry.path
            raw = _read_file(path)
            items = [item for item in raw["connectors"]
                     if not isinstance(item, dict) or item.get("id") != sid]
            if len(items) >= 24:
                raise MCPHubError("catalog_capacity_reached")
            items.append({
                "id": spec.connector_id,
                "server_label": spec.server_label,
                "server_url": spec.server_url,
                "authorization_env": spec.authorization_env,
                "allowed_tools": list(spec.allowed_tools),
                "require_approval": "always", "enabled": False,
            })
            raw["connectors"] = items
            self._write_atomic(path, raw)
        return {"id": sid, "enabled": False, "status": "saved_disabled"}

    def set_enabled(self, server_id: str, enabled: bool) -> dict:
        sid = str(server_id or "").strip()
        if not _ID.fullmatch(sid):
            raise MCPHubError("invalid_server_id")
        with _LOCK:
            path = self.registry.path
            raw = _read_file(path)
            found = False
            for item in raw["connectors"]:
                if isinstance(item, dict) and item.get("id") == sid:
                    parsed = MCPConnector.from_mapping(item)
                    if parsed.server_url:
                        _validate_remote(parsed.server_url)
                    if not parsed.allowed_tools:
                        raise MCPHubError("explicit_tool_allowlist_required")
                    if parsed.authorization_env and enabled and not os.getenv(parsed.authorization_env):
                        raise MCPHubError("authentication_required")
                    item["enabled"] = bool(enabled)
                    # Every newly-managed server requires approval by default.
                    item["require_approval"] = "always"
                    found = True
            if not found:
                raise MCPHubError("server_not_found")
            self._write_atomic(path, raw)
        return {"id": sid, "enabled": bool(enabled), "status": "updated"}

    @staticmethod
    def _write_atomic(path: Path, payload: dict) -> None:
        """Keep current config intact if disk write fails; no credentials."""
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(path.parent),
                prefix=".jarvis-mcp-", suffix=".tmp", delete=False,
            ) as stream:
                tmp_name = stream.name
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)
        finally:
            if tmp_name is not None and os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _spec(self, server_id: str) -> MCPConnector:
        for item in self.registry.load():
            if item.connector_id == server_id:
                if not item.enabled:
                    raise MCPHubError("server_disabled")
                if item.tunnel_id:
                    raise MCPHubError("tunnel_requires_openai_responses")
                if not item.server_url:
                    raise MCPHubError("endpoint_missing")
                _validate_remote(item.server_url)
                if not item.allowed_tools:
                    raise MCPHubError("explicit_tool_allowlist_required")
                if item.authorization_env and not os.getenv(item.authorization_env):
                    raise MCPHubError("authentication_required")
                return item
        raise MCPHubError("server_not_found")

    async def _connected(self, spec: MCPConnector, operation, *args):
        try:
            from mcp import Client
        except ImportError as exc:
            raise MCPHubError("install_optional_mcp_sdk") from exc
        # SDK v2: the Client is async and requires a context manager.
        # Authentication is NOT copied from ChatGPT; individual provider token.
        try:
            if spec.authorization_env:
                import httpx2
                from mcp.client.streamable_http import streamable_http_client
                async with httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + os.environ[spec.authorization_env]},
                    timeout=httpx2.Timeout(12.0, read=20.0),
                    follow_redirects=False,
                    trust_env=False,
                ) as http_client:
                    async with Client(
                        streamable_http_client(spec.server_url, http_client=http_client)
                    ) as client:
                        return await operation(client, *args)
            async with Client(spec.server_url) as client:
                return await operation(client, *args)
        except MCPHubError:
            raise
        except Exception as exc:
            # A tool might have changed something before timeout. Never retry.
            raise MCPHubError("remote_result_unknown") from exc

    async def _inspect(self, client, selected: set[str]) -> list[dict]:
        result = await client.list_tools()
        tools = result.tools if hasattr(result, "tools") else result
        out = []
        for tool in tools:
            name = str(getattr(tool, "name", ""))
            if name not in selected:
                continue
            annotations = getattr(tool, "annotations", None)
            out.append({
                "name": name,
                "description": str(getattr(tool, "description", "") or "")[:260],
                # Hints are NEVER trust or proof, merely UI information.
                "claimed_read_only": (
                    getattr(annotations, "readOnlyHint", None)
                    if annotations is not None else None
                ),
            })
            if len(out) >= 24:
                break
        return out

    def list_tools(self, server_id: str) -> dict:
        spec = self._spec(server_id)
        async def operation(client):
            return await self._inspect(client, set(spec.allowed_tools))
        try:
            found = asyncio.run(asyncio.wait_for(
                self._connected(spec, operation), timeout=22.0
            ))
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise MCPHubError("remote_timeout") from exc
        return {"server_id": server_id, "tools": found,
                "found": len(found), "allowed": len(spec.allowed_tools)}

    def call_tool(
        self, server_id: str, tool_name: str, arguments: dict,
        *, approved: bool = False,
    ) -> dict:
        # Never let model override stored tool allowlist, even with approval.
        if not approved:
            raise MCPHubError("explicit_user_approval_required")
        spec = self._spec(str(server_id))
        name = str(tool_name or "")
        if name not in spec.allowed_tools:
            raise MCPHubError("tool_not_allowlisted")
        if not isinstance(arguments, dict):
            raise MCPHubError("arguments_must_be_object")
        if len(json.dumps(arguments, ensure_ascii=False)) > 16384:
            raise MCPHubError("arguments_too_large")
        async def operation(client):
            found = await self._inspect(client, {name})
            if not found:
                raise MCPHubError("tool_not_advertised_by_server")
            result = await client.call_tool(name, arguments)
            if hasattr(result, "model_dump"):
                data = result.model_dump(mode="json", exclude_none=True)
            else:
                data = {"isError": getattr(result, "is_error", True)}
            return {
                "success": not bool(data.get("isError", True)),
                "server_id": spec.connector_id,
                "tool": name,
                "response": json.dumps(data, ensure_ascii=False)[:3500],
                # SUCCESS of MCP call ≠ verified user goal.
                "goal_verified": False,
            }
        try:
            return asyncio.run(asyncio.wait_for(
                self._connected(spec, operation), timeout=25.0,
            ))
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise MCPHubError("remote_outcome_unknown") from exc


MCP_HUB = MCPHub()
