from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .kernel_contracts import KnowledgeScope, RiskLevel


_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][a-zA-Z0-9_.-]+)?$")


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    agent_id: str
    entrypoint: str
    required_capabilities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_connectors: tuple[str, ...] = ()
    allowed_domains: tuple[str, ...] = ()
    allowed_network_hosts: tuple[str, ...] = ()
    allowed_file_roots: tuple[str, ...] = ()
    memory_scopes: tuple[KnowledgeScope, ...] = ()
    risk_level: RiskLevel = RiskLevel.READ
    requires_confirmation: bool = False
    test_pack: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PluginManifest":
        data = dict(raw or {})
        manifest = cls(
            plugin_id=str(data.get("plugin_id") or "").strip(),
            name=str(data.get("name") or "").strip(),
            version=str(data.get("version") or "").strip(),
            agent_id=str(data.get("agent_id") or "").strip(),
            entrypoint=str(data.get("entrypoint") or "").strip(),
            required_capabilities=tuple(
                str(item).strip()
                for item in data.get("required_capabilities") or ()
                if str(item).strip()
            ),
            allowed_tools=tuple(
                str(item).strip()
                for item in data.get("allowed_tools") or ()
                if str(item).strip()
            ),
            allowed_connectors=tuple(
                str(item).strip()
                for item in data.get("allowed_connectors") or ()
                if str(item).strip()
            ),
            allowed_domains=tuple(
                str(item).strip().lower()
                for item in data.get("allowed_domains") or ()
                if str(item).strip()
            ),
            allowed_network_hosts=tuple(
                str(item).strip().lower()
                for item in data.get("allowed_network_hosts") or ()
                if str(item).strip()
            ),
            allowed_file_roots=tuple(
                str(item).strip()
                for item in data.get("allowed_file_roots") or ()
                if str(item).strip()
            ),
            memory_scopes=tuple(
                KnowledgeScope(str(item))
                for item in data.get("memory_scopes") or ()
            ),
            risk_level=RiskLevel(
                str(data.get("risk_level") or RiskLevel.READ.value)
            ),
            requires_confirmation=bool(
                data.get("requires_confirmation", False)
            ),
            test_pack=tuple(
                str(item).strip()
                for item in data.get("test_pack") or ()
                if str(item).strip()
            ),
            metadata=dict(data.get("metadata") or {}),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if not _ID_RE.match(self.plugin_id):
            raise ValueError("invalid_plugin_id")
        if not self.name:
            raise ValueError("missing_plugin_name")
        if not _VERSION_RE.match(self.version):
            raise ValueError("invalid_plugin_version")
        if not _ID_RE.match(self.agent_id):
            raise ValueError("invalid_agent_id")
        if ":" not in self.entrypoint:
            raise ValueError("entrypoint_must_be_module_colon_symbol")

        if self.risk_level in {
            RiskLevel.EXTERNAL_SIDE_EFFECT,
            RiskLevel.DESTRUCTIVE,
        } and not self.requires_confirmation:
            raise ValueError("high_risk_plugin_requires_confirmation")

        for host in self.allowed_network_hosts:
            if (
                "://" in host
                or "/" in host
                or host in {"*", "0.0.0.0"}
            ):
                raise ValueError("invalid_network_host")

        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("duplicate_allowed_tools")
        if len(set(self.allowed_connectors)) != len(
            self.allowed_connectors
        ):
            raise ValueError("duplicate_allowed_connectors")

    def as_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "agent_id": self.agent_id,
            "entrypoint": self.entrypoint,
            "required_capabilities": list(self.required_capabilities),
            "allowed_tools": list(self.allowed_tools),
            "allowed_connectors": list(self.allowed_connectors),
            "allowed_domains": list(self.allowed_domains),
            "allowed_network_hosts": list(self.allowed_network_hosts),
            "allowed_file_roots": list(self.allowed_file_roots),
            "memory_scopes": [
                scope.value for scope in self.memory_scopes
            ],
            "risk_level": self.risk_level.value,
            "requires_confirmation": self.requires_confirmation,
            "test_pack": list(self.test_pack),
            "metadata": dict(self.metadata),
        }
