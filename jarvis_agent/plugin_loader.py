from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .plugin_manifest import PluginManifest


@dataclass(frozen=True)
class PluginRecord:
    manifest: PluginManifest
    manifest_path: str


class PluginCatalog:
    """Loads and validates plugin metadata only.

    It deliberately does not import or execute the manifest entrypoint.
    Trusted Jarvis code must separately register an executable builder in the
    AgentFactory before any plugin agent can run.
    """

    def __init__(self):
        self._records: dict[str, PluginRecord] = {}

    def add_manifest(
        self,
        manifest: PluginManifest,
        *,
        manifest_path: str = "",
    ) -> None:
        existing = self._records.get(manifest.plugin_id)
        if existing is not None:
            if existing.manifest.version == manifest.version:
                raise ValueError("duplicate_plugin_version")
        self._records[manifest.plugin_id] = PluginRecord(
            manifest=manifest,
            manifest_path=str(manifest_path or ""),
        )

    def load_json_file(self, path: str | Path) -> PluginManifest:
        file_path = Path(path)
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("plugin_manifest_must_be_object")
        manifest = PluginManifest.from_dict(raw)
        self.add_manifest(
            manifest,
            manifest_path=str(file_path),
        )
        return manifest

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._records.get(str(plugin_id))

    def all(self) -> list[PluginRecord]:
        return sorted(
            self._records.values(),
            key=lambda item: item.manifest.plugin_id,
        )

    def enabled_for_agent(self, agent_id: str) -> list[PluginManifest]:
        return [
            record.manifest
            for record in self.all()
            if record.manifest.agent_id == str(agent_id)
        ]
