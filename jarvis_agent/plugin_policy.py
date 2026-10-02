from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .plugin_manifest import PluginManifest


@dataclass(frozen=True)
class PluginPermissionDecision:
    allowed: bool
    reason: str


class PluginPermissionPolicy:
    """Fail-closed permission checks for future plugin execution."""

    @staticmethod
    def tool(
        manifest: PluginManifest,
        tool_name: str,
    ) -> PluginPermissionDecision:
        if str(tool_name) not in set(manifest.allowed_tools):
            return PluginPermissionDecision(
                False,
                "plugin_tool_not_allowed",
            )
        return PluginPermissionDecision(True, "allowed")

    @staticmethod
    def connector(
        manifest: PluginManifest,
        connector_id: str,
    ) -> PluginPermissionDecision:
        if str(connector_id) not in set(manifest.allowed_connectors):
            return PluginPermissionDecision(
                False,
                "plugin_connector_not_allowed",
            )
        return PluginPermissionDecision(True, "allowed")

    @staticmethod
    def network_url(
        manifest: PluginManifest,
        url: str,
    ) -> PluginPermissionDecision:
        try:
            parsed = urlparse(str(url))
        except Exception:
            return PluginPermissionDecision(False, "invalid_url")
        host = (parsed.hostname or "").lower()
        if not host:
            return PluginPermissionDecision(False, "missing_network_host")
        allowed = {
            item.lower()
            for item in manifest.allowed_network_hosts
        }
        if host not in allowed:
            return PluginPermissionDecision(
                False,
                "plugin_network_host_not_allowed",
            )
        return PluginPermissionDecision(True, "allowed")

    @staticmethod
    def file_path(
        manifest: PluginManifest,
        path: str | Path,
    ) -> PluginPermissionDecision:
        raw = Path(path).expanduser()
        try:
            target = raw.resolve(strict=False)
        except Exception:
            return PluginPermissionDecision(False, "invalid_file_path")

        for root_text in manifest.allowed_file_roots:
            try:
                root = Path(root_text).expanduser().resolve(strict=False)
                target.relative_to(root)
                return PluginPermissionDecision(True, "allowed")
            except (ValueError, OSError):
                continue
        return PluginPermissionDecision(
            False,
            "plugin_file_path_not_allowed",
        )
