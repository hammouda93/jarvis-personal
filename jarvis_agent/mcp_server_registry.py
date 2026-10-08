"""Local, explicit MCP server registry. Config is not a credential store.

A configured server is initially disabled and ALL discovered tools initially
unexposed. Discovery does not grant model access or call any server tool.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
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
            and not parsed.fragment and not parsed.query
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
        for server, entry in raw["servers"].items():
            if (not _ID.fullmatch(str(server)) or not isinstance(entry, dict)
                    or entry.get("kind") not in {"http", "stdio"}
                    or not isinstance(entry.get("tools", {}), dict)
                    or not all(isinstance(tool, dict) for tool in entry.get("tools", {}).values())):
                raise ValueError("invalid_mcp_server_record")
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
        result = [
            {
                "id": name,
                "kind": entry.get("kind"),
                "endpoint": entry.get("url", "")
                if entry.get("kind") == "http"
                else str(entry.get("command") or ""),
                "enabled": entry.get("enabled") is True,
                "last_discovery_success_utc": str(
                    entry.get("last_discovery_success_utc") or ""
                )[:40],
                "connected_now": False,
                "credential_source": entry.get("credential_source", "environment") if entry.get("kind") == "http" else "scoped_environment",
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
        from .mcp_activity import quota_limits
        for row in result:
            try:
                row["quotas"] = quota_limits(servers[row["id"]])
                row["activity"] = self.activity().summary(row["id"])
            except Exception:
                row["activity"] = {"unavailable": True}
        return result

    def activity(self):
        from .mcp_activity import MCPActivityStore
        return MCPActivityStore(self.path.with_suffix(".activity.sqlite3"))

    def set_quotas(self, server_id: str, quotas: dict):
        from .mcp_activity import quota_limits
        if not isinstance(quotas, dict) or set(quotas) != {"sessions_per_hour", "calls_per_hour"}:
            raise ValueError("invalid_mcp_quota")
        validated = quota_limits({"quotas": quotas})
        with self._lock:
            data = self._load()
            entry = data["servers"].get(self._check_id(server_id))
            if not isinstance(entry, dict):
                raise KeyError("mcp_server_not_found")
            entry["quotas"] = validated
            self._write(data)

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
            if len(data["servers"]) >= 30:
                raise ValueError("mcp_server_limit")
            data["servers"]["hermes"] = {
                "kind": "stdio", "command": "hermes",
                "args": ["mcp", "serve"], "enabled": False, "tools": {},
            }
            self._write(data)

    def add_stdio(self, server_id: str, command: str, args: list[str], *,
                  trusted: bool = False, env_refs: dict | None = None) -> None:
        from .mcp_security import validate_stdio
        server_id = self._check_id(server_id)
        entry = {"kind": "stdio", "command": command, "args": args,
                 "trusted_stdio": trusted is True, "env_refs": {} if env_refs is None else env_refs,
                 "enabled": False, "tools": {}}
        validate_stdio(entry)
        with self._lock:
            data = self._load()
            if server_id in data["servers"]:
                raise ValueError("mcp_server_already_exists")
            if len(data["servers"]) >= 30:
                raise ValueError("mcp_server_limit")
            data["servers"][server_id] = entry
            self._write(data)

    def revoke_tools(self, server_id: str) -> None:
        with self._lock:
            data = self._load()
            entry = data["servers"].get(self._check_id(server_id))
            if not isinstance(entry, dict):
                raise KeyError("mcp_server_not_found")
            for tool in (entry.get("tools") or {}).values():
                tool["allowed"] = False
            self._write(data)

    def set_credential_source(self, server_id: str, source: str) -> None:
        if source not in {"environment", "vault", "none", "oauth"}:
            raise ValueError("invalid_mcp_credential_source")
        with self._lock:
            data = self._load()
            entry = data["servers"].get(self._check_id(server_id))
            if not isinstance(entry, dict) or entry.get("kind") != "http":
                raise ValueError("http_server_required_for_bearer")
            entry["credential_source"] = source
            self._write(data)

    def remove_server(self, server_id: str) -> None:
        with self._lock:
            data = self._load()
            if data["servers"].pop(self._check_id(server_id), None) is None:
                raise KeyError("mcp_server_not_found")
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
                description = str(tool.get("description") or "")[:400]
                definition = json.dumps(
                    {"name": tool_name, "description": description,
                     "input_schema": schema},
                    sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                ).encode("utf-8")
                fingerprint = hashlib.sha256(definition).hexdigest()
                old = existing.get(tool_name) or {}
                # A server can replace a formerly benign tool under the same
                # name. Any schema/description change REVOKES past consent.
                allowed = (
                    old.get("allowed") is True
                    and old.get("fingerprint") == fingerprint
                )
                updated[tool_name] = {
                    "description": description,
                    "input_schema": schema,
                    "fingerprint": fingerprint,
                    "allowed": allowed,
                }
            entry["tools"] = updated
            # Even after discovering, enabling and tool selection are separate.
            self._write(data)
            return len(updated)

    def note_successful_discovery(self, server_id: str) -> None:
        """Record last successful *transport discovery*, not current connectivity."""
        with self._lock:
            data = self._load()
            entry = data["servers"].get(self._check_id(server_id))
            if not isinstance(entry, dict):
                raise KeyError("mcp_server_not_found")
            if entry.get("enabled") is not True:
                raise RuntimeError("mcp_server_disabled")
            entry["last_discovery_success_utc"] = (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
            self._write(data)

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
