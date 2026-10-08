"""Opt-in catalog and per-server authorization for Jarvis MCP.

Inspired by the server/tool isolation of NousResearch/hermes-agent (MIT).
This is original Jarvis code and has no Hermes imports.
No ChatGPT connector token/session is reused or inferred.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
from typing import Any

from .connectors import ConnectorRegistry as LegacyConnectorRegistry
from .connectors import MCPConnector

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_TOOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    category: str
    details: str
    prerequisites: str

    def as_dict(self) -> dict[str, str]:
        return vars(self).copy()


PRESETS: tuple[Preset, ...] = (
    Preset("gmail", "Gmail", "Communication", "Recherche, lecture, brouillons, envoi avec accord.", "MCP autorisé / OAuth Gmail"),
    Preset("google_sheets", "Google Sheets", "Documents", "Lecture et modification de feuilles par outil autorisé.", "MCP Sheets / autorisation Google"),
    Preset("google_maps", "Google Maps", "Cartographie", "Lieux, géocodage et itinéraires selon outils exposés.", "MCP Maps / clé ou OAuth selon fournisseur"),
    Preset("google_drive", "Google Drive", "Documents", "Recherche, lecture et opérations Drive autorisées.", "MCP Drive / autorisation Google"),
    Preset("whatsapp", "WhatsApp", "Communication", "Messages via MCP fournisseur; Cloud Business séparé du compte personnel.", "MCP autorisé ou WhatsApp Cloud Business"),
    Preset("github", "GitHub", "Développement", "Dépôts, issues et changements soumis aux permissions.", "MCP GitHub / OAuth ou jeton à portée réduite"),
)


def check_https_endpoint(value: str) -> str:
    raw = str(value or "").strip()
    if len(raw) > 1200 or any(ch.isspace() for ch in raw):
        raise ValueError("mcp_url_invalid")
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https" or not parsed.hostname or
        parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise ValueError("mcp_https_origin_required")
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("mcp_local_endpoint_not_supported")
    # IP-addressed/private endpoints are intentionally out of scope for V5,
    # which does not ship a safe local-process/stdio manager.
    import ipaddress
    try:
        addr = ipaddress.ip_address(parsed.hostname.strip("[]"))
    except ValueError:
        addr = None
    if addr is not None and not addr.is_global:
        raise ValueError("mcp_private_endpoint_not_supported")
    return raw


def _validate_connector(value: MCPConnector) -> None:
    if not _ID.fullmatch(value.connector_id):
        raise ValueError("mcp_id_invalid")
    if not _ID.fullmatch(value.server_label):
        raise ValueError("mcp_label_invalid")
    if not value.server_url or value.tunnel_id:
        raise ValueError("mcp_remote_https_only")
    check_https_endpoint(value.server_url)
    if not value.allowed_tools:
        raise ValueError("mcp_tool_allowlist_required")
    if len(value.allowed_tools) > 64 or len(set(value.allowed_tools)) != len(value.allowed_tools):
        raise ValueError("mcp_tool_allowlist_invalid")
    if any(not _TOOL.fullmatch(t) for t in value.allowed_tools):
        raise ValueError("mcp_tool_name_invalid")
    if value.authorization_env and not _ENV.fullmatch(value.authorization_env):
        raise ValueError("mcp_authorization_ref_invalid")
    # Nothing registered through this hub can silently skip model approval.
    if value.require_approval != "always":
        raise ValueError("mcp_approval_always_required")


class MCPHub:
    """Configuration and policy manager. No transport initialization on load."""

    def __init__(self, config_path: str | Path | None = None):
        self.registry = LegacyConnectorRegistry(
            path=Path(config_path) if config_path is not None else None
        )
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.registry.path

    def _read(self) -> list[dict[str, Any]]:
        path = self.path
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("connectors") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValueError("mcp_catalog_invalid")
        if any(not isinstance(item, dict) for item in items):
            raise ValueError("mcp_catalog_item_invalid")
        # Older unsupported/tunnel connector entries are preserved by saves,
        # but are never automatically activated from the safe remote-only UI.
        return items

    def _write(self, items: list[dict[str, Any]]) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"connectors": items}, indent=2, ensure_ascii=False) + "\n"
        tmp = path.with_name(path.name + ".new")
        try:
            with tmp.open("x", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def catalog(self) -> list[dict[str, Any]]:
        return [p.as_dict() for p in PRESETS]

    def list_servers(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = self._read()
        outcome: list[dict[str, Any]] = []
        for raw in entries:
            try:
                connector = MCPConnector.from_mapping(raw)
                _validate_connector(connector)
                eligible = True
            except (ValueError, TypeError):
                eligible = False
                connector = None
            env_name = str(raw.get("authorization_env") or "")
            auth_ready = bool(os.getenv(env_name)) if env_name else True
            outcome.append({
                "id": str(raw.get("id") or "")[:64],
                "label": str(raw.get("server_label") or "")[:64],
                "endpoint": str(raw.get("server_url") or "")[:200],
                "enabled": bool(raw.get("enabled", False)),
                "eligible": eligible,
                "auth_env": env_name[:80],  # name, never token value
                "auth_ready": auth_ready,
                "tool_allowlist": list(connector.allowed_tools) if eligible else [],
                "approval_required": True,
                "connected": False,  # no network connection on catalog load
                "status": (
                    "configuration_invalide" if not eligible else
                    "désactivé" if not raw.get("enabled") else
                    "autorisation_manquante" if not auth_ready else
                    "configuré_non_testé"
                ),
            })
        return outcome

    def add_remote(
        self, *, server_id: str, label: str, endpoint: str,
        allowed_tools: tuple[str, ...], authorization_env: str = "",
    ) -> None:
        """Explicit local configuration only; newly added servers stay OFF."""
        if not _ID.fullmatch(str(server_id or "")):
            raise ValueError("mcp_id_invalid")
        if not _ID.fullmatch(str(label or "")):
            raise ValueError("mcp_label_invalid")
        connector = MCPConnector(
            connector_id=server_id,
            server_label=label,
            server_description="Jarvis MCP : " + label,
            server_url=check_https_endpoint(endpoint),
            authorization_env=authorization_env or None,
            allowed_tools=tuple(allowed_tools),
            require_approval="always",
            enabled=False,
        )
        _validate_connector(connector)
        with self._lock:
            rows = self._read()
            if any(
                row.get("id") == server_id or row.get("server_label") == label
                for row in rows
            ):
                raise ValueError("mcp_server_or_label_already_exists")
            rows.append({
                "id": connector.connector_id,
                "server_label": connector.server_label,
                "server_description": connector.server_description,
                "server_url": connector.server_url,
                "authorization_env": connector.authorization_env,
                "allowed_tools": list(connector.allowed_tools),
                "require_approval": "always",
                "enabled": False,
            })
            self._write(rows)

    def set_enabled(self, server_id: str, *, enabled: bool) -> None:
        """Never connect by itself; refuse untrusted/malformed server configs."""
        with self._lock:
            rows = self._read()
            matching = [item for item in rows if item.get("id") == server_id]
            if len(matching) != 1:
                raise KeyError("mcp_server_not_found_or_ambiguous")
            raw = matching[0]
            connector = MCPConnector.from_mapping(raw)
            _validate_connector(connector)
            if enabled and connector.authorization_env and not os.getenv(connector.authorization_env):
                raise PermissionError("mcp_auth_missing")
            raw["enabled"] = bool(enabled)
            raw["require_approval"] = "always"
            self._write(rows)

    def describe(self, server_id: str) -> MCPConnector:
        with self._lock:
            rows = self._read()
            matches = [item for item in rows if item.get("id") == server_id]
        if len(matches) != 1:
            raise KeyError("mcp_server_not_found_or_ambiguous")
        value = MCPConnector.from_mapping(matches[0])
        _validate_connector(value)
        if not value.enabled:
            raise PermissionError("mcp_server_disabled")
        if value.authorization_env and not os.getenv(value.authorization_env):
            raise PermissionError("mcp_auth_missing")
        return value

    def authorize_call(
        self, server_id: str, tool: str, *, approved: bool = False
    ) -> MCPConnector:
        value = self.describe(server_id)
        if tool not in value.allowed_tools:
            raise PermissionError("mcp_tool_not_allowlisted")
        if not approved:
            raise PermissionError("mcp_user_approval_required")
        return value


HUB = MCPHub()
