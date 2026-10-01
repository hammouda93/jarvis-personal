from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .memory import LOCAL_MEMORY
from .ms_football_bridge import MS_FOOTBALL_BRIDGE
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
                "Ouvre une page de recherche web visible dans le navigateur de l'utilisateur. Cet outil ne lit pas les résultats et ne fournit aucune preuve factuelle à lui seul.",
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
                "msf_capabilities",
                "Découvre les applications, modèles et capacités exposés par MS Football. À utiliser pour comprendre ce que l'application sait faire.",
                {},
                [],
            ),
            self._ollama(
                "msf_describe_schema",
                "Inspecte dynamiquement le schéma Django de MS Football: modèles, champs, relations et choix. Utilise-le avant une requête lorsque le modèle ou les champs ne sont pas certains.",
                {
                    "search": {
                        "type": "string",
                        "description": "Filtre optionnel sur un nom de modèle ou champ.",
                    },
                    "limit_models": {
                        "type": "integer",
                        "description": "Nombre maximum de modèles à retourner.",
                    },
                },
                [],
            ),
            self._ollama(
                "msf_count_records",
                "Compte rapidement les enregistrements MS Football via l'ORM Django, avec filtres optionnels. À privilégier pour toute question 'combien' au lieu de charger des lignes ou d'inventer un nom de table SQL.",
                {
                    "model": {
                        "type": "string",
                        "description": "Modèle Django, par ex. Player, Video ou gestion_joueurs.Video.",
                    },
                    "filters": {
                        "type": "object",
                        "description": "Filtres Django ORM optionnels.",
                    },
                },
                ["model"],
            ),
            self._ollama(
                "msf_query_records",
                "Interroge les données MS Football via l'ORM Django sans écrire. Les filtres acceptent les lookups Django comme player__name__icontains, status, deadline__lt.",
                {
                    "model": {
                        "type": "string",
                        "description": "Modèle, par ex. Player, Video ou gestion_joueurs.Video.",
                    },
                    "filters": {
                        "type": "object",
                        "description": "Filtres Django ORM.",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Champs à retourner.",
                    },
                    "order_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tri, par ex. -video_creation_date.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre maximum de lignes.",
                    },
                },
                ["model"],
            ),
            self._ollama(
                "msf_readonly_sql",
                "Exécute une seule requête SQL SELECT/CTE en lecture seule sur la base MS Football. À utiliser pour une analyse complexe difficile à exprimer en ORM.",
                {
                    "sql": {
                        "type": "string",
                        "description": "Requête SELECT ou WITH...SELECT uniquement.",
                    },
                    "params": {
                        "type": "array",
                        "description": "Paramètres positionnels optionnels.",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Nombre maximum de lignes à retourner.",
                    },
                },
                ["sql"],
            ),
            self._ollama(
                "msf_search_code",
                "Recherche dans le code source local de MS Football pour comprendre une fonctionnalité existante, un modèle, une vue, une tâche ou un workflow.",
                {
                    "query": {
                        "type": "string",
                        "description": "Texte ou symbole à rechercher dans le code.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Nombre maximum de résultats.",
                    },
                },
                ["query"],
            ),
            self._ollama(
                "msf_list_routes",
                "Liste les routes Django de MS Football pour découvrir les fonctionnalités et écrans existants.",
                {
                    "search": {
                        "type": "string",
                        "description": "Filtre optionnel sur route, nom ou vue.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre maximum de routes.",
                    },
                },
                [],
            ),
            self._ollama(
                "msf_resolve_route",
                "Résout une route Django MS Football existante vers son URL réelle. À utiliser après msf_list_routes pour ouvrir un écran ou workflow existant sans coder son URL.",
                {
                    "name": {
                        "type": "string",
                        "description": "Nom Django de la route.",
                    },
                    "kwargs": {
                        "type": "object",
                        "description": "Arguments nommés de la route, par ex. video_id.",
                    },
                    "query": {
                        "type": "object",
                        "description": "Paramètres query-string optionnels.",
                    },
                },
                ["name"],
            ),
            self._ollama(
                "msf_prepare_mutation",
                "Prépare sans l'exécuter une création, modification ou suppression générique dans MS Football. Retourne un aperçu et un change_id. Ne modifie jamais la base.",
                {
                    "model": {
                        "type": "string",
                        "description": "Modèle Django cible.",
                    },
                    "operation": {
                        "type": "string",
                        "enum": ["create", "update", "delete"],
                    },
                    "filters": {
                        "type": "object",
                        "description": "Filtres pour update/delete.",
                    },
                    "values": {
                        "type": "object",
                        "description": "Valeurs pour create/update.",
                    },
                },
                ["model", "operation"],
            ),
            self._ollama(
                "msf_commit_mutation",
                "Valide une mutation MS Football déjà préparée. Cette action est sensible et doit être explicitement approuvée par l'utilisateur avant exécution.",
                {
                    "change_id": {
                        "type": "string",
                        "description": "Identifiant retourné par msf_prepare_mutation.",
                    }
                },
                ["change_id"],
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
                "reset_conversation_context",
                "Efface le contexte temporaire de la conversation en cours quand l'utilisateur exprime naturellement l'intention de repartir de zéro, d'oublier ce qui vient d'être discuté ou de commencer une nouvelle discussion. Ne supprime jamais la mémoire persistante personnelle.",
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

    def requires_confirmation(self, name: str) -> bool:
        return name in {"msf_commit_mutation"}

    def execute(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        *,
        approved: bool = False,
    ) -> AgentActionResult:
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
            base = execute(ToolIntent("browser.search", {"query": query}))
            return AgentActionResult(
                name=name,
                success=base.success,
                message=(
                    "La page de recherche a été ouverte dans le navigateur. "
                    "Les résultats n'ont pas été lus."
                    if base.success
                    else base.message
                ),
                detail=json.dumps(
                    {
                        "opened_url": base.detail,
                        "query": query,
                        "results_read": False,
                        "factual_evidence": False,
                    },
                    ensure_ascii=False,
                ),
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

        if name == "msf_capabilities":
            result = MS_FOOTBALL_BRIDGE.call("list_capabilities")
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_describe_schema":
            payload = {
                "search": str(args.get("search", "")).strip(),
                "limit_models": int(args.get("limit_models") or 80),
            }
            result = MS_FOOTBALL_BRIDGE.call("describe_schema", payload)
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_count_records":
            payload = {
                "model": str(args.get("model", "")).strip(),
                "filters": args.get("filters") or {},
            }
            result = MS_FOOTBALL_BRIDGE.call("count_records", payload)
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_query_records":
            payload = {
                "model": str(args.get("model", "")).strip(),
                "filters": args.get("filters") or {},
                "fields": args.get("fields") or None,
                "order_by": args.get("order_by") or None,
                "limit": int(args.get("limit") or 50),
            }
            result = MS_FOOTBALL_BRIDGE.call("query_records", payload)
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_readonly_sql":
            payload = {
                "sql": str(args.get("sql", "")).strip(),
                "params": args.get("params") or [],
                "max_rows": int(args.get("max_rows") or 100),
            }
            result = MS_FOOTBALL_BRIDGE.call("run_readonly_sql", payload)
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_search_code":
            payload = {
                "query": str(args.get("query", "")).strip(),
                "max_results": int(args.get("max_results") or 20),
            }
            result = MS_FOOTBALL_BRIDGE.call("search_code", payload)
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_list_routes":
            payload = {
                "search": str(args.get("search", "")).strip(),
                "limit": int(args.get("limit") or 120),
            }
            result = MS_FOOTBALL_BRIDGE.call("list_routes", payload)
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_resolve_route":
            payload = {
                "name": str(args.get("name", "")).strip(),
                "kwargs": args.get("kwargs") or {},
                "query": args.get("query") or {},
            }
            result = MS_FOOTBALL_BRIDGE.call("resolve_route", payload)
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_prepare_mutation":
            payload = {
                "model": str(args.get("model", "")).strip(),
                "operation": str(args.get("operation", "")).strip(),
                "filters": args.get("filters") or {},
                "values": args.get("values") or {},
            }
            result = MS_FOOTBALL_BRIDGE.call("prepare_mutation", payload)
            return AgentActionResult(name, result.success, result.message, result.detail)

        if name == "msf_commit_mutation":
            if not approved:
                return AgentActionResult(
                    name=name,
                    success=False,
                    message="Cette modification MS Football exige une confirmation explicite.",
                    detail="approval_required",
                )
            payload = {
                "change_id": str(args.get("change_id", "")).strip(),
            }
            result = MS_FOOTBALL_BRIDGE.call(
                "commit_mutation",
                payload,
                approved=True,
            )
            return AgentActionResult(name, result.success, result.message, result.detail)

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

        if name == "reset_conversation_context":
            return AgentActionResult(
                name=name,
                success=True,
                message="Le contexte temporaire de la conversation doit être réinitialisé.",
                detail="reset_conversation_context_requested",
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
