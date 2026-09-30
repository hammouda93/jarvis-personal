from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT


_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class MCPConnector:
    connector_id: str
    server_label: str
    server_description: str
    server_url: str | None = None
    tunnel_id: str | None = None
    authorization_env: str | None = None
    require_approval: Any = "always"
    allowed_tools: tuple[str, ...] = ()
    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "MCPConnector":
        connector_id = str(raw.get("id") or raw.get("server_label") or "").strip()
        server_label = str(raw.get("server_label") or connector_id).strip()
        description = str(
            raw.get("server_description")
            or raw.get("description")
            or f"Connector {server_label}"
        ).strip()
        server_url = str(raw.get("server_url") or "").strip() or None
        tunnel_id = str(raw.get("tunnel_id") or "").strip() or None
        authorization_env = (
            str(raw.get("authorization_env") or "").strip() or None
        )
        enabled = bool(raw.get("enabled", True))
        require_approval = raw.get("require_approval", "always")

        allowed_raw = raw.get("allowed_tools") or []
        if not isinstance(allowed_raw, list):
            raise ValueError("allowed_tools doit être une liste")
        allowed_tools = tuple(
            str(item).strip()
            for item in allowed_raw
            if str(item).strip()
        )

        if not connector_id or not _LABEL_RE.match(connector_id):
            raise ValueError("id de connecteur invalide")
        if not server_label or not _LABEL_RE.match(server_label):
            raise ValueError("server_label MCP invalide")
        if bool(server_url) == bool(tunnel_id):
            raise ValueError(
                "un connecteur MCP doit définir exactement server_url ou tunnel_id"
            )
        if server_url and not server_url.startswith("https://"):
            raise ValueError("server_url MCP doit utiliser https://")
        if authorization_env and not re.match(
            r"^[A-Za-z_][A-Za-z0-9_]*$",
            authorization_env,
        ):
            raise ValueError("authorization_env invalide")

        if isinstance(require_approval, str):
            if require_approval not in {"always", "never"}:
                raise ValueError(
                    "require_approval doit être 'always', 'never' ou un objet MCP"
                )
        elif not isinstance(require_approval, dict):
            raise ValueError(
                "require_approval doit être 'always', 'never' ou un objet MCP"
            )

        return cls(
            connector_id=connector_id,
            server_label=server_label,
            server_description=description,
            server_url=server_url,
            tunnel_id=tunnel_id,
            authorization_env=authorization_env,
            require_approval=require_approval,
            allowed_tools=allowed_tools,
            enabled=enabled,
        )

    def openai_tool(self) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        tool: dict[str, Any] = {
            "type": "mcp",
            "server_label": self.server_label,
            "server_description": self.server_description,
            "require_approval": self.require_approval,
        }
        if self.server_url:
            tool["server_url"] = self.server_url
        if self.tunnel_id:
            tool["tunnel_id"] = self.tunnel_id
        if self.allowed_tools:
            tool["allowed_tools"] = list(self.allowed_tools)

        if self.authorization_env:
            token = (os.getenv(self.authorization_env) or "").strip()
            if not token:
                # Do not expose a connector that the user explicitly marked as
                # authenticated but has not actually authorized on this PC.
                return None
            tool["authorization"] = token

        return tool


class ConnectorRegistry:
    """Local connector catalog.

    Secrets never live in the JSON file. A connector may reference an
    environment variable containing its OAuth/access token.
    """

    def __init__(self, path: Path | None = None) -> None:
        configured = (os.getenv("JARVIS_CONNECTORS_FILE") or "").strip()
        self.path = (
            Path(os.path.expandvars(os.path.expanduser(configured)))
            if configured
            else path or (PROJECT_ROOT / "connectors.local.json")
        )

    def load(self) -> tuple[MCPConnector, ...]:
        if not self.path.is_file():
            return ()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ()

        raw_connectors = (
            payload.get("connectors", [])
            if isinstance(payload, dict)
            else payload
        )
        if not isinstance(raw_connectors, list):
            return ()

        connectors: list[MCPConnector] = []
        for raw in raw_connectors:
            if not isinstance(raw, dict):
                continue
            try:
                connectors.append(MCPConnector.from_mapping(raw))
            except ValueError:
                continue
        return tuple(connectors)

    def openai_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for connector in self.load():
            tool = connector.openai_tool()
            if tool is not None:
                tools.append(tool)
        return tools

    def status(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for connector in self.load():
            auth_ready = True
            if connector.authorization_env:
                auth_ready = bool(
                    (os.getenv(connector.authorization_env) or "").strip()
                )
            result.append(
                {
                    "id": connector.connector_id,
                    "server_label": connector.server_label,
                    "enabled": connector.enabled,
                    "auth_ready": auth_ready,
                    "transport": (
                        "tunnel" if connector.tunnel_id else "remote"
                    ),
                    "require_approval": connector.require_approval,
                }
            )
        return result


CONNECTORS = ConnectorRegistry()
