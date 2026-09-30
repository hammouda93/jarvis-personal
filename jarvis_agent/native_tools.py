from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .memory import LOCAL_MEMORY
from .tools import ToolIntent, ToolResult, execute, normalize
from .windows_perception import (
    activate_window,
    click_ui_element,
    close_window,
    inspect_active_window,
    list_windows,
    press_key,
    write_ui_element,
)


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
                "Trouve et ouvre une application de bureau installée sur Windows par son nom. Ne pas utiliser pour ouvrir un site ou service web: utiliser open_url directement.",
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
                "list_windows",
                "Liste les fenêtres visibles actuellement sur Windows. Outil de perception en lecture seule.",
                {},
                [],
            ),
            self._ollama(
                "inspect_active_window",
                "Observe la fenêtre de travail active ou une fenêtre nommée et retourne une vue compacte de ses contrôles. Les contrôles ont des refs e1, e2... réutilisables immédiatement pour cliquer ou écrire, même sans libellé.",
                {
                    "title": {
                        "type": "string",
                        "description": "Titre optionnel de la fenêtre à inspecter. Sans titre, inspecte la fenêtre de travail active en ignorant l'interface Jarvis.",
                    }
                },
                [],
            ),
            self._ollama(
                "activate_window",
                "Met au premier plan une fenêtre déjà ouverte en la recherchant par son titre.",
                {
                    "title": {
                        "type": "string",
                        "description": "Titre ou partie distinctive du titre de la fenêtre.",
                    }
                },
                ["title"],
            ),
            self._ollama(
                "click_ui_element",
                "Clique/active un contrôle observé. Utilise ref après inspect_active_window si le contrôle est sans nom ou si la cible est ambiguë.",
                {
                    "name": {
                        "type": "string",
                        "description": "Texte visible ou automation_id du contrôle.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Référence e1, e2... fournie par la dernière inspection.",
                    },
                    "control_type": {
                        "type": "string",
                        "description": "Type UIA optionnel, par ex. Button, Hyperlink, MenuItem.",
                    },
                },
                [],
            ),
            self._ollama(
                "close_window",
                "Ferme explicitement une fenêtre Windows visible. Sans title, ferme la fenêtre active. Utilise seulement si l'utilisateur a demandé de fermer cette fenêtre.",
                {
                    "title": {
                        "type": "string",
                        "description": "Titre optionnel de la fenêtre à fermer.",
                    }
                },
                [],
            ),
            self._ollama(
                "write_ui_element",
                "Écrit du texte dans un champ observé sans valider automatiquement. Utilise ref après inspection pour les champs sans libellé, comme certaines barres de recherche web.",
                {
                    "name": {
                        "type": "string",
                        "description": "Libellé ou automation_id du champ.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Référence e1, e2... fournie par la dernière inspection.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Texte à saisir dans le champ.",
                    },
                },
                ["text"],
            ),
            self._ollama(
                "press_key",
                "Envoie une touche de navigation sûre à la fenêtre active: Enter, Escape, Tab, flèches, PageUp/PageDown, Home/End.",
                {
                    "key": {
                        "type": "string",
                        "description": "Nom de la touche à envoyer.",
                    }
                },
                ["key"],
            ),
            self._ollama(
                "get_current_time",
                "Lit l'heure actuelle de l'ordinateur.",
                {},
                [],
            ),
            self._ollama(
                "remember_information",
                "Enregistre localement une information que l'utilisateur demande explicitement à Jarvis de retenir.",
                {
                    "content": {
                        "type": "string",
                        "description": "Information exacte à mémoriser.",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Quelques mots-clés optionnels.",
                    },
                },
                ["content"],
            ),
            self._ollama(
                "recall_information",
                "Recherche dans la mémoire locale personnelle de Jarvis.",
                {
                    "query": {
                        "type": "string",
                        "description": "Ce que l'utilisateur veut retrouver ou rappeler.",
                    }
                },
                ["query"],
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
                    "strict": False,
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

        if name == "list_windows":
            result = list_windows()
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "inspect_active_window":
            title = str(args.get("title", "")).strip() or None
            result = inspect_active_window(title=title)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "activate_window":
            title = str(args.get("title", "")).strip()
            result = activate_window(title)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "click_ui_element":
            target = str(args.get("name", "")).strip()
            ref = str(args.get("ref", "")).strip()
            control_type = str(args.get("control_type", "")).strip() or None
            result = click_ui_element(
                target,
                ref=ref,
                control_type=control_type,
            )
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "close_window":
            title = str(args.get("title", "")).strip() or None
            result = close_window(title)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "write_ui_element":
            target = str(args.get("name", "")).strip()
            ref = str(args.get("ref", "")).strip()
            text = str(args.get("text", ""))
            result = write_ui_element(target, text, ref=ref)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "press_key":
            key = str(args.get("key", "")).strip()
            result = press_key(key)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "get_current_time":
            return self._convert(name, execute(ToolIntent("system.time")))

        if name == "remember_information":
            content = str(args.get("content", "")).strip()
            tags = str(args.get("tags", "")).strip()
            if not content:
                return self._error(name, "L'information à mémoriser est vide.")
            item = LOCAL_MEMORY.remember(content, tags=tags)
            return AgentActionResult(
                name=name,
                success=True,
                message="Information mémorisée localement.",
                detail=f"memory_id={item.id}",
            )

        if name == "recall_information":
            query = str(args.get("query", "")).strip()
            if not query:
                return self._error(name, "La recherche mémoire est vide.")
            items = LOCAL_MEMORY.search(query, limit=5)
            if not items:
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="Aucun souvenir correspondant.",
                    detail="[]",
                )
            payload = [
                {
                    "id": item.id,
                    "content": item.content,
                    "tags": item.tags,
                    "created_at": item.created_at,
                }
                for item in items
            ]
            return AgentActionResult(
                name=name,
                success=True,
                message="Souvenirs retrouvés.",
                detail=json.dumps(payload, ensure_ascii=False),
            )

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
