from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .tools import ToolIntent, ToolResult, execute, normalize


@dataclass(frozen=True)
class AgentActionResult:
    name: str
    success: bool
    message: str
    detail: str = ""
    end_session: bool = False
    should_exit: bool = False

    def as_json(self) -> str:
        return json.dumps(
            {
                "tool": self.name,
                "success": self.success,
                "message": self.message,
                "detail": self.detail,
                "end_session": self.end_session,
                "should_exit": self.should_exit,
            },
            ensure_ascii=False,
        )


class NativeToolRegistry:
    """Small set of generic capabilities exposed to the AI model.

    The model receives verbs, not a catalog of hard-coded phrases. Targets such
    as VLC, Baristas or a future application/folder remain arguments.
    """

    def ollama_tools(self) -> list[dict[str, Any]]:
        return [
            self._ollama(
                "open_application",
                "Trouve et ouvre une application installée sur Windows par son nom.",
                {
                    "name": {
                        "type": "string",
                        "description": "Nom de l'application, par ex. VLC Media Player, Chrome, Cursor.",
                    }
                },
                ["name"],
            ),
            self._ollama(
                "open_folder",
                "Trouve et ouvre un dossier par son nom. Utilise within si le dossier est dans un parent connu.",
                {
                    "name": {
                        "type": "string",
                        "description": "Nom du dossier à ouvrir.",
                    },
                    "within": {
                        "type": "string",
                        "description": "Nom optionnel du dossier parent dans lequel chercher.",
                    },
                },
                ["name"],
            ),
            self._ollama(
                "open_url",
                "Ouvre une URL HTTP ou HTTPS dans le navigateur.",
                {
                    "url": {
                        "type": "string",
                        "description": "URL complète commençant par http:// ou https://.",
                    }
                },
                ["url"],
            ),
            self._ollama(
                "search_web",
                "Lance une recherche web visible dans le navigateur de l'utilisateur.",
                {
                    "query": {
                        "type": "string",
                        "description": "Requête de recherche.",
                    }
                },
                ["query"],
            ),
            self._ollama(
                "get_current_time",
                "Lit l'heure actuelle de l'ordinateur.",
                {},
                [],
            ),
            self._ollama(
                "return_to_standby",
                "Met fin à la conversation active et remet Jarvis en veille.",
                {},
                [],
            ),
        ]

    def openai_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for item in self.ollama_tools():
            fn = item["function"]
            tools.append(
                {
                    "type": "function",
                    "name": fn["name"],
                    "description": fn["description"],
                    "parameters": fn["parameters"],
                    "strict": True,
                }
            )
        return tools

    @staticmethod
    def _ollama(
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str],
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    def execute(self, name: str, arguments: dict[str, Any] | None) -> AgentActionResult:
        args = dict(arguments or {})

        if name == "open_application":
            target = str(args.get("name", "")).strip()
            if not self._safe_target(target):
                return self._error(name, "Le nom de l'application est trop vague.")

            normalized = normalize(target)
            known = {
                "chrome": "chrome",
                "google chrome": "chrome",
                "spotify": "spotify",
                "cursor": "cursor",
                "vs code": "vscode",
                "vscode": "vscode",
                "visual studio code": "vscode",
                "capture ecran": "snippingtool",
                "outil capture": "snippingtool",
                "snipping tool": "snippingtool",
            }
            if normalized in known:
                result = execute(ToolIntent("app.open", {"app": known[normalized]}))
            else:
                result = execute(ToolIntent("app.open_named", {"query": target}))
            return self._convert(name, result)

        if name == "open_folder":
            target = str(args.get("name", "")).strip()
            within = str(args.get("within", "")).strip()
            if not self._safe_target(target):
                return self._error(name, "Le nom du dossier est trop vague.")

            normalized = normalize(target)
            if normalized in {"downloads", "telechargements", "telechargement"}:
                result = execute(ToolIntent("folder.open", {"folder": "downloads"}))
            else:
                payload: dict[str, Any] = {"query": target}
                if within and self._safe_target(within):
                    payload["within"] = within
                result = execute(ToolIntent("folder.open_named", payload))
            return self._convert(name, result)

        if name == "open_url":
            url = str(args.get("url", "")).strip()
            if not url.startswith(("https://", "http://")):
                return self._error(name, "URL non autorisée ou invalide.")
            return self._convert(
                name,
                execute(ToolIntent("browser.open_url", {"url": url})),
            )

        if name == "search_web":
            query = str(args.get("query", "")).strip()
            if len(query) < 2:
                return self._error(name, "La recherche est vide.")
            return self._convert(
                name,
                execute(ToolIntent("browser.search", {"query": query})),
            )

        if name == "get_current_time":
            return self._convert(name, execute(ToolIntent("system.time")))

        if name == "return_to_standby":
            return self._convert(name, execute(ToolIntent("assistant.sleep")))

        return self._error(name, "Cette capacité n'existe pas dans Jarvis.")

    @staticmethod
    def _safe_target(value: str) -> bool:
        target = normalize(value)
        compact = target.replace(" ", "")
        blocked = {
            "",
            "app",
            "application",
            "programme",
            "program",
            "dossier",
            "folder",
            "com",
            "exe",
        }
        return target not in blocked and len(compact) >= 3

    @staticmethod
    def _convert(name: str, result: ToolResult) -> AgentActionResult:
        return AgentActionResult(
            name=name,
            success=result.success,
            message=result.message,
            detail=result.detail,
            end_session=result.end_session,
            should_exit=result.should_exit,
        )

    @staticmethod
    def _error(name: str, message: str) -> AgentActionResult:
        return AgentActionResult(
            name=name,
            success=False,
            message=message,
        )


NATIVE_TOOLS = NativeToolRegistry()
