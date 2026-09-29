from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tools import ToolIntent, normalize


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    description: str
    required_args: tuple[str, ...] = ()
    optional_args: tuple[str, ...] = ()
    risk: str = "low"
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedTool:
    intent: ToolIntent | None
    error: str = ""


class ToolRegistry:
    """Single source of truth for tools the agent is allowed to plan."""

    def __init__(self) -> None:
        self._specs: dict[str, CapabilitySpec] = {
            "browser.open_url": CapabilitySpec(
                "browser.open_url",
                "Ouvrir une URL HTTP/HTTPS connue.",
                ("url",),
                examples=("https://www.youtube.com",),
            ),
            "browser.search": CapabilitySpec(
                "browser.search",
                "Lancer une recherche web avec une requête précise.",
                ("query",),
            ),
            "app.open": CapabilitySpec(
                "app.open",
                "Ouvrir une application rapide connue: chrome, spotify, cursor, vscode, snippingtool.",
                ("app",),
            ),
            "app.open_named": CapabilitySpec(
                "app.open_named",
                "Trouver puis ouvrir une application installée par son nom. À utiliser pour VLC et toute application non présente dans app.open.",
                ("query",),
                examples=("VLC Media Player", "Notepad++"),
            ),
            "folder.open": CapabilitySpec(
                "folder.open",
                "Ouvrir un dossier système connu. Actuellement: downloads.",
                ("folder",),
            ),
            "folder.open_named": CapabilitySpec(
                "folder.open_named",
                "Trouver puis ouvrir un dossier par son nom. Peut limiter la recherche avec 'within'.",
                ("query",),
                ("within",),
                examples=("baristas", "media dans baristas"),
            ),
            "system.time": CapabilitySpec(
                "system.time",
                "Lire l'heure actuelle du système.",
            ),
            "assistant.sleep": CapabilitySpec(
                "assistant.sleep",
                "Terminer la conversation active et retourner en veille.",
            ),
        }

    def specs(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._specs.values())

    def catalog_for_prompt(self) -> str:
        lines: list[str] = []
        for spec in self.specs():
            args = list(spec.required_args)
            args.extend(f"{name}?" for name in spec.optional_args)
            arg_text = ", ".join(args) if args else "aucun"
            lines.append(
                f"- {spec.name} | args: {arg_text} | {spec.description}"
            )
        return "\n".join(lines)

    def prepare(self, tool: str, args: dict[str, Any] | None) -> PreparedTool:
        tool = str(tool or "").strip()
        values = dict(args or {})
        spec = self._specs.get(tool)
        if spec is None:
            return PreparedTool(None, f"capability_unknown:{tool}")

        for required in spec.required_args:
            if not str(values.get(required, "")).strip():
                return PreparedTool(None, f"missing_arg:{required}")

        if tool == "browser.open_url":
            url = str(values["url"]).strip()
            if not url.startswith(("https://", "http://")):
                return PreparedTool(None, "invalid_url")
            return PreparedTool(ToolIntent(tool, {"url": url}))

        if tool == "browser.search":
            query = str(values["query"]).strip()
            return PreparedTool(ToolIntent(tool, {"query": query}))

        if tool == "app.open":
            app = normalize(str(values["app"]))
            aliases = {
                "google chrome": "chrome",
                "chrome": "chrome",
                "spotify": "spotify",
                "cursor": "cursor",
                "vs code": "vscode",
                "vscode": "vscode",
                "visual studio code": "vscode",
                "snipping tool": "snippingtool",
                "capture ecran": "snippingtool",
            }
            if app in aliases:
                return PreparedTool(
                    ToolIntent("app.open", {"app": aliases[app]})
                )

            # A planner may reasonably call app.open("VLC") even though VLC is
            # not a fast-path app. Convert that to generic discovery instead
            # of treating it as an unsupported hard-coded command.
            if self._safe_named_target(app):
                return PreparedTool(
                    ToolIntent("app.open_named", {"query": str(values["app"]).strip()})
                )
            return PreparedTool(None, "invalid_app_target")

        if tool == "app.open_named":
            query = str(values["query"]).strip()
            if not self._safe_named_target(query):
                return PreparedTool(None, "invalid_app_target")
            return PreparedTool(ToolIntent(tool, {"query": query}))

        if tool == "folder.open":
            folder = normalize(str(values["folder"]))
            if folder in {"downloads", "telechargements"}:
                return PreparedTool("folder.open" and ToolIntent("folder.open", {"folder": "downloads"}))
            if self._safe_named_target(folder):
                return PreparedTool(
                    ToolIntent("folder.open_named", {"query": str(values["folder"]).strip()})
                )
            return PreparedTool(None, "invalid_folder_target")

        if tool == "folder.open_named":
            query = str(values["query"]).strip()
            if not self._safe_named_target(query):
                return PreparedTool(None, "invalid_folder_target")
            prepared: dict[str, Any] = {"query": query}
            within = str(values.get("within", "")).strip()
            if within and self._safe_named_target(within):
                prepared["within"] = within
            return PreparedTool(ToolIntent(tool, prepared))

        if tool in {"system.time", "assistant.sleep"}:
            return PreparedTool(ToolIntent(tool))

        return PreparedTool(None, f"capability_unhandled:{tool}")

    @staticmethod
    def _safe_named_target(value: str) -> bool:
        target = normalize(value)
        compact = target.replace(" ", "")
        blocked = {
            "app",
            "application",
            "programme",
            "program",
            "browser",
            "navigateur",
            "com",
            "exe",
            "dossier",
            "folder",
        }
        return bool(target) and len(compact) >= 4 and target not in blocked


DEFAULT_TOOL_REGISTRY = ToolRegistry()
