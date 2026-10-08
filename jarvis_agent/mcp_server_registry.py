"""Local, explicit MCP server registry. Config is not a credential store.

A configured server is initially disabled and ALL discovered tools initially
unexposed. Discovery does not grant model access or call any server tool.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

_ID = re.compile(r"^[a-z][a-z0-9_]{0,35}$")
_TOOL = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


def config_path() -> Path:
    custom = os.getenv("JARVIS_MCP_CONFIG_PATH", "").strip()
    if custom:
        return Path(custom)
    base = os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".jarvis_personal") / "JarvisPersonal" / "mcp_servers.json"


def _valid_remote(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return (
            bool(host)
            and not parsed.username and not parsed.password
            and not parsed.fragment
            and (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and host in ("localhost", "127.0.0.1", "::1"))
            )
        )
    except ValueError:
        return False


class MCPRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else config_path()
        self._lock = threading.RLock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "servers": {}}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("servers"), dict):
            raise ValueError("invalid_mcp_registry")
        if raw.get("version") != 1:
            raise ValueError("unsupported_mcp_registry_version")
        return raw

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".mcp_", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False, indent=2, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def list_servers(self) -> list[dict]:
        with self._lock:
            servers = self._load()["servers"]
        # Never return raw env values, tokens, or internal connection handles.
        return [
            {
                "id": name,
                "kind": entry.get("kind"),
                "endpoint": entry.get("url", "")
                if entry.get("kind") == "http"
                else str(entry.get("command") or ""),
                "enabled": entry.get("enabled") is True,
                "discovered": len(entry.get("tools") or {}),
                "allowed": sum(
                    (tool.get("allowed") is True)
                    for tool in (entry.get("tools") or {}).values()
                ),
                "tools": [
                    {"name": tool_name, "allowed": record.get("allowed") is True,
                     "description": str(record.get("description") or "")[:150]}
                    for tool_name, record in (entry.get("tools") or {}).items()
                ][:80],
            }
            for name, entry in sorted(servers.items())
            if isinstance(entry, dict)
        ][:40]

    def get_server(self, server_id: str) -> dict | None:
        with self._lock:
            value = self._load()["servers"].get(str(server_id))
        return copy.deepcopy(value) if isinstance(value, dict) else None

    @staticmethod
    def _check_id(name: str) -> str:
        candidate = str(name or "").strip().lower()
        if not _ID.fullmatch(candidate):
            raise ValueError("invalid_mcp_server_id")
        return candidate

    def add_http(self, server_id: str, url: str) -> None:
        server_id = self._check_id(server_id)
        url = str(url or "").strip()
        if not _valid_remote(url) or len(url) > 600:
            raise ValueError("mcp_remote_requires_https_or_localhost")
        with self._lock:
            data = self._load()
            if server_id in data["servers"]:
                raise ValueError("mcp_server_already_exists")
            if len(data["servers"]) >= 30:
                raise ValueError("mcp_server_limit")
            data["servers"][server_id] = {
                "kind": "http", "url": url, "enabled": False, "tools": {},
            }
            self._write(data)

    def add_hermes_local(self) -> None:
        """Only CONFIGURE the optional server; NEVER run/download Hermes."""
        with self._lock:
            data = self._load()
            if "hermes" in data["servers"]:
                raise ValueError("mcp_server_already_exists")
            data["servers"]["hermes"] = {
                "kind": "stdio", "command": "hermes",
                "args": ["mcp", "serve"], "enabled": False, "tools": {},
            }
            self._write(data)

    def set_enabled(self, server_id: str, enabled: bool) -> None:
        with self._lock:
            data = self._load()
            entry = data["servers"].get(self._check_id(server_id))
            if not isinstance(entry, dict):
                raise KeyError("mcp_server_not_found")
            entry["enabled"] = bool(enabled)
            self._write(data)

    def discover(self, server_id: str, tools: list[dict]) -> int:
        """Import metadata only. New tools are DENIED until user enables each."""
        name = self._check_id(server_id)
        if len(tools) > 150:
            raise ValueError("mcp_discovery_limit")
        with self._lock:
            data = self._load()
            entry = data["servers"].get(name)
            if not isinstance(entry, dict):
                raise KeyError("mcp_server_not_found")
            existing = entry.get("tools") or {}
            updated = {}
            for tool in tools:
                tool_name = str(tool.get("name") or "")
                if not _TOOL.fullmatch(tool_name):
                    continue
                schema = tool.get("input_schema") or {}
                if not isinstance(schema, dict) or len(json.dumps(schema)) > 15000:
                    continue
                old = existing.get(tool_name) or {}
                updated[tool_name] = {
                    "description": str(tool.get("description") or "")[:400],
                    "input_schema": schema,
                    "allowed": old.get("allowed") is True,
                }
            entry["tools"] = updated
            # Even after discovering, enabling and tool selection are separate.
            self._write(data)
            return len(updated)

    def allow_tool(self, server_id: str, tool_name: str, allowed: bool) -> None:
        with self._lock:
            data = self._load()
            entry = data["servers"].get(self._check_id(server_id))
            if not isinstance(entry, dict):
                raise KeyError("mcp_server_not_found")
            tool = (entry.get("tools") or {}).get(str(tool_name))
            if not isinstance(tool, dict):
                raise KeyError("mcp_tool_not_discovered")
            tool["allowed"] = bool(allowed)
            self._write(data)

    def exposed_tools(self) -> list[dict]:
        with self._lock:
            servers = self._load()["servers"]
        result = []
        for server_id, entry in sorted(servers.items()):
            if not isinstance(entry, dict) or entry.get("enabled") is not True:
                continue
            for name, tool in sorted((entry.get("tools") or {}).items()):
                if tool.get("allowed") is not True:
                    continue
                result.append({
                    "server": server_id, "tool": name,
                    "description": str(tool.get("description") or "")[:400],
                    "input_schema": copy.deepcopy(tool.get("input_schema") or {}),
                })
        return result[:200]

    def is_allowed(self, server_id: str, name: str) -> bool:
        entry = self.get_server(server_id)
        return bool(entry and entry.get("enabled") is True and
                    (entry.get("tools") or {}).get(name, {}).get("allowed") is True)
