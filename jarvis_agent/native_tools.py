from __future__ import annotations

import json
import os
import time
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Any

from .agent_knowledge import AGENT_KNOWLEDGE
from .background_web_research import BACKGROUND_WEB_RESEARCH
from .config import settings
from .cua_driver_bridge import CUA_DRIVER
from .memory import LOCAL_MEMORY
from .ms_football_bridge import MS_FOOTBALL_BRIDGE
from .screen_vision import (
    click_visual_target,
    observe_screen,
    write_visual_target,
)
from .tools import (
    ToolIntent,
    ToolResult,
    _contextual_site_search_url,
    _explicit_site_search_url,
    _remember_browser_url,
    execute,
    normalize,
)
from .windows_perception import (
    activate_window,
    click_ui_element,
    close_tab,
    close_window,
    inspect_active_window,
    invalidate_ui_snapshot,
    list_windows,
    press_key,
    type_text_active_window,
    ui_ref_descriptor,
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


_DECLARED_APP_SEARCH_SHORTCUTS = {
    # WhatsApp Help Center (Windows): Extended search = Alt+K.
    # Keep application-specific accelerators isolated here rather than
    # hard-coding them in generic perception or planner logic.
    "whatsapp": "altk",
}


class NativeToolRegistry:
    """Small set of generic capabilities exposed to the AI model.

    The model receives verbs, not a catalog of hard-coded phrases. Targets such
    as VLC, Baristas or a future application/folder remain arguments.
    """

    def __init__(self, knowledge=None) -> None:
        self.knowledge = knowledge or AGENT_KNOWLEDGE
        self._last_app_hint = ""
        self._last_observed_window_title = ""
        self._last_web_title_hint = ""
        self._browser = None

    @staticmethod
    def can_search_application(title: str) -> bool:
        normalized_title = normalize(str(title or ""))
        return any(
            app_key in normalized_title
            for app_key in _DECLARED_APP_SEARCH_SHORTCUTS
        )

    @staticmethod
    def _web_window_title_hint(url: str) -> str:
        try:
            host = urlparse(str(url or "")).netloc.casefold()
        except Exception:
            host = ""
        if not host:
            return ""
        if host.endswith("youtube.com"):
            label = "YouTube"
        elif host == "web.whatsapp.com":
            label = "WhatsApp"
        elif "google." in host:
            label = "Google"
        else:
            parts = [part for part in host.split(".") if part and part != "www"]
            label = (parts[0] if parts else host).replace("-", " ").title()
        return f"{label} - Google Chrome"

    def _resolve_web_window_title(self, requested: str | None) -> str | None:
        hint = str(self._last_web_title_hint or "").strip()
        value = str(requested or "").strip()
        if not hint:
            return value or None
        if not value:
            return hint
        normalized_value = normalize(value)
        normalized_hint = normalize(hint)
        site_label = normalize(hint.split(" - ", 1)[0])
        if (
            normalized_value == site_label
            or normalized_value in normalized_hint
            or (
                "google chrome" not in normalized_value
                and site_label
                and site_label in normalized_value
            )
        ):
            return hint
        return value

    @staticmethod
    def _compact_observation(payload: dict[str, Any], limit: int = 24) -> dict[str, Any]:
        value = dict(payload or {})
        controls = [
            dict(item)
            for item in list(value.get("controls") or [])
            if isinstance(item, dict)
        ]
        value["controls"] = controls[:limit]
        if len(controls) > limit:
            value["controls_omitted"] = len(controls) - limit
        capabilities = dict(value.get("capabilities") or {})
        for key, cap in (("writable", 12), ("actionable", 16)):
            items = [
                dict(item)
                for item in list(capabilities.get(key) or [])
                if isinstance(item, dict)
            ]
            capabilities[key] = items[:cap]
        if capabilities:
            value["capabilities"] = capabilities
        if isinstance(value.get("visible_text"), list):
            value["visible_text"] = value["visible_text"][:40]
        value.pop("accessibility_tree", None)
        return value

    def _attach_windows_post_observation(
        self,
        action: AgentActionResult,
    ) -> AgentActionResult:
        if not action.success:
            return action
        try:
            payload = json.loads(action.detail or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return action
        if not isinstance(payload, dict) or payload.get("requires_fresh_inspection") is not True:
            return action

        observed = inspect_active_window(title=None)
        if not observed.success:
            payload["post_observation_error"] = observed.detail or observed.message
            return AgentActionResult(
                action.name,
                action.success,
                action.message,
                json.dumps(payload, ensure_ascii=False),
                action.end_session,
                action.should_exit,
            )
        self._record_inspected_app(observed)
        try:
            post = json.loads(observed.detail or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            post = {}
        if isinstance(post, dict) and post:
            payload["post_observation"] = self._compact_observation(post)
            payload["requires_fresh_inspection"] = False
            payload["auto_reinspection"] = True
        return AgentActionResult(
            action.name,
            action.success,
            action.message,
            json.dumps(payload, ensure_ascii=False),
            action.end_session,
            action.should_exit,
        )

    def ollama_tools(self) -> list[dict[str, Any]]:
        tools = [
            self._ollama(
                "open_application",
                "Trouve et ouvre une application de bureau installée sur Windows par son nom. Ne pas utiliser comme substitut à un contrôle déjà observé dans une application ouverte: si inspect_active_window montre la cible, agir sur sa ref. Ne pas utiliser pour ouvrir un site web: utiliser open_url.",
                {
                    "name": {
                        "type": "string",
                        "description": "Nom de l'application, par ex. VLC Media Player, Chrome, Cursor.",
                    },
                    "new_instance": {
                        "type": "boolean",
                        "description": "Mettre true uniquement si l'utilisateur demande explicitement une nouvelle instance. Sinon une fenêtre déjà ouverte est réutilisée.",
                    }
                },
                ["name"],
            ),
            self._ollama(
                "open_file",
                "Trouve et ouvre un fichier réel par son nom dans les emplacements utilisateur (Téléchargements, Bureau, Documents). À utiliser pour un fichier téléchargé, un installateur, un document ou un exécutable précis; ne pas détourner open_application.",
                {
                    "name": {
                        "type": "string",
                        "description": "Nom complet ou mots distinctifs du fichier, par ex. CursorUserSetup ou rapport.pdf.",
                    },
                    "within": {
                        "type": "string",
                        "description": "Dossier optionnel dans lequel chercher, par ex. Téléchargements.",
                    },
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
                "research_web",
                "Recherche et lit le web en arrière-plan sans ouvrir le navigateur de l'utilisateur. À utiliser pour obtenir des informations actuelles, vérifier une solution, consulter de la documentation ou résoudre un échec quand les preuves locales ne suffisent pas.",
                {
                    "query": {
                        "type": "string",
                        "description": "Question de recherche précise avec le contexte utile.",
                    }
                },
                ["query"],
            ),
            self._ollama(
                "open_web_search",
                "Ouvre une page de résultats web visible. À utiliser uniquement si l'utilisateur demande explicitement d'ouvrir ou de voir la recherche dans le navigateur.",
                {
                    "query": {
                        "type": "string",
                        "description": "Requête à afficher dans le navigateur.",
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
                "inspect_interface",
                "Inspection générique d'une application Windows: utilise UIA/Cua d'abord et, seulement si la couverture structurée est insuffisante, tente une seule observation visuelle locale. Préfère cet outil pour découvrir une interface de bureau inconnue.",
                {
                    "title": {
                        "type": "string",
                        "description": "Titre ou nom optionnel de la fenêtre.",
                    },
                    "focus": {
                        "type": "string",
                        "description": "Ce qu'il faut comprendre ou trouver dans l'interface.",
                    },
                },
                [],
            ),
            self._ollama(
                "inspect_active_window",
                "Observe la fenêtre active ou nommée et retourne des contrôles avec une ref opaque complète (par ex. obs3:e7). Pour agir, copie uniquement cette ref exactement telle quelle; observation_id est une métadonnée de diagnostic, pas un argument d’action. Après toute mutation, une ancienne ref expire.",
                {
                    "title": {
                        "type": "string",
                        "description": "Titre optionnel de la fenêtre à inspecter. Sans titre, inspecte la fenêtre de travail active en ignorant l'interface Jarvis.",
                    }
                },
                [],
            ),
            self._ollama(
                "observe_screen",
                "Fallback visuel local: capture la fenêtre réelle et la fait analyser par un modèle vision Ollama local. À utiliser seulement si inspect_active_window est ambigu/incomplet, pour distinguer des éléments visuels, des résultats ordonnés ou vérifier un état que UIA ne montre pas clairement.",
                {
                    "title": {
                        "type": "string",
                        "description": "Titre optionnel de la fenêtre à observer visuellement.",
                    },
                    "focus": {
                        "type": "string",
                        "description": "Question visuelle précise, sans données secrètes inutiles.",
                    },
                },
                [],
            ),
            self._ollama(
                "click_visual_target",
                "Fallback visuel local contrôlé: localise un élément clairement visible dans la fenêtre avec le modèle vision Ollama local puis clique son centre. Utiliser seulement lorsque UIA ne fournit pas une cible exploitable. Le résultat n'est jamais considéré comme vérifié: réinspecter ensuite.",
                {
                    "target": {
                        "type": "string",
                        "description": "Description précise de la cible visible, par ex. le champ Nom du fichier ou le bouton Enregistrer.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Titre optionnel de la fenêtre cible.",
                    },
                },
                ["target"],
            ),
            self._ollama(
                "write_visual_target",
                "Fallback visuel local contrôlé pour écrire: localise un champ clairement visible, clique ce champ puis saisit le texte. Utiliser seulement si UIA ne fournit aucun contrôle writable exploitable. Toujours réinspecter après.",
                {
                    "target": {
                        "type": "string",
                        "description": "Description précise du champ visible, par ex. champ Nom du fichier.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Texte à saisir dans cette cible.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Titre optionnel de la fenêtre cible.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "append", "insert"],
                        "description": "replace=remplacer; append=ajouter à la fin; insert=insérer au curseur.",
                    },
                },
                ["target", "text"],
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
                "Clique/active un contrôle observé. Copier exactement la ref opaque fournie par la dernière inspection. Toute action invalide les anciennes refs: réinspecter avant l’action suivante.",
                {
                    "name": {
                        "type": "string",
                        "description": "Texte visible ou automation_id du contrôle.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Référence opaque complète fournie par la dernière inspection, par ex. obs3:e7. La copier telle quelle.",
                    },
                    "delivery_mode": {
                        "type": "string",
                        "enum": ["background", "foreground"],
                        "description": "Laisser background par défaut. Utiliser foreground uniquement si un essai background a explicitement signalé qu'il ne pouvait pas agir.",
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
                "close_tab",
                "Ferme un onglet dans l'application active sans fermer volontairement toute la fenêtre. Utilise cet outil quand l'utilisateur parle d'un onglet/tab, pas close_window.",
                {
                    "name": {
                        "type": "string",
                        "description": "Nom ou partie distinctive de l'onglet à fermer. Laisser vide seulement si l'onglet actif est clairement la cible.",
                    }
                },
                [],
            ),
            self._ollama(
                "write_ui_element",
                "Écrit dans un contrôle éditable observé (Edit, Document ou ComboBox). Choisis mode=replace pour remplacer tout le contenu, append pour conserver le contenu existant et ajouter à la fin, insert pour écrire à la position actuelle du curseur. N'utilise jamais un Text, TabItem ou libellé statique.",
                {
                    "name": {
                        "type": "string",
                        "description": "Libellé ou automation_id du champ.",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Référence opaque complète fournie par la dernière inspection, par ex. obs3:e7. La copier telle quelle.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Texte à saisir dans le champ.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "append", "insert"],
                        "description": "replace=remplacer tout; append=ajouter en conservant l'existant; insert=insérer au curseur.",
                    },
                    "delivery_mode": {
                        "type": "string",
                        "enum": ["background", "foreground"],
                        "description": "Laisser background par défaut. Utiliser foreground uniquement après un refus explicite du mode background.",
                    },
                },
                ["text"],
            ),
            self._ollama(
                "type_text_active_window",
                "Fallback clavier générique quand inspect_active_window échoue ou retourne controls=[]/win32_window_only. Active la fenêtre demandée puis saisit le texte dans le contrôle actuellement au focus. À utiliser seulement si la cible de saisie est évidente; réinspecter ensuite si une preuve est nécessaire.",
                {
                    "text": {
                        "type": "string",
                        "description": "Texte à saisir.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Titre ou nom de la fenêtre cible, optionnel.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "append", "insert"],
                        "description": "replace=Ctrl+A puis saisie; append=fin puis saisie; insert=saisie au curseur.",
                    },
                },
                ["text"],
            ),
            self._ollama(
                "search_application",
                "Recherche dans une application de bureau en utilisant uniquement un raccourci de recherche déclaré pour cette application. Utilise ce mécanisme avant la vision lorsque UIA/Cua n'expose aucun champ de recherche. L'outil échoue s'il n'existe pas de raccourci déclaré, il ne devine jamais une combinaison clavier.",
                {
                    "title": {
                        "type": "string",
                        "description": "Nom ou titre de l'application cible.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Texte à rechercher dans l'application.",
                    },
                },
                ["title", "query"],
            ),
            self._ollama(
                "press_key",
                "Envoie une touche ou un raccourci clavier sûr à la fenêtre active: Enter, Escape, Tab, flèches, PageUp/PageDown, Home/End, Alt+Left/Alt+Right, Ctrl+S, Ctrl+Shift+S, Ctrl+F, Ctrl+L, Ctrl+C/V/A/Z/Y. Réinspecter ensuite si le raccourci peut modifier l'interface.",
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
                "search_agent_knowledge",
                "Recherche dans la mémoire opérationnelle locale de Jarvis: skills réutilisables, leçons de comportement et profils d'applications. Cette mémoire est distincte des souvenirs personnels.",
                {
                    "query": {
                        "type": "string",
                        "description": "Mission, application ou problème opérationnel à retrouver.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre maximum d'éléments de chaque catégorie.",
                    },
                },
                ["query"],
            ),
            self._ollama(
                "save_verified_skill",
                "Enregistre localement une procédure réutilisable uniquement après une réussite réellement vérifiée. Le contenu doit rester générique: aucune donnée personnelle, aucun message privé, aucune coordonnée fixe d'écran.",
                {
                    "name": {
                        "type": "string",
                        "description": "Nom générique stable du skill, par ex. messaging_send_message.",
                    },
                    "goal": {
                        "type": "string",
                        "description": "Objectif générique du skill sans nom de personne ni contenu privé.",
                    },
                    "app_scope": {
                        "type": "string",
                        "description": "Application ou portée optionnelle, par ex. messaging_app.",
                    },
                    "procedure": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Étapes abstraites et adaptables à l'interface réelle.",
                    },
                    "success_checks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Preuves observables requises avant d'annoncer le succès.",
                    },
                    "failure_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Erreurs génériques déjà rencontrées à éviter.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confiance entre 0 et 1.",
                    },
                },
                ["name", "goal", "procedure", "success_checks"],
            ),
            self._ollama(
                "save_feedback_lesson",
                "Enregistre une correction comportementale générique issue d'un feedback utilisateur clair. Ne stocke jamais le contenu privé de la conversation.",
                {
                    "scope": {
                        "type": "string",
                        "description": "Portée générique: global, ui, messaging, browser, etc.",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Situation générique qui déclenche la leçon.",
                    },
                    "rule": {
                        "type": "string",
                        "description": "Règle générale à appliquer la prochaine fois.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confiance entre 0 et 1.",
                    },
                },
                ["pattern", "rule"],
            ),
            self._ollama(
                "agent_knowledge_stats",
                "Retourne les compteurs de la mémoire opérationnelle locale de Jarvis.",
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
        if settings.browser_enabled and settings.browser_cdp_url:
            tools[4:4] = self._browser_ollama_tools()
        return tools

    def _browser_ollama_tools(self) -> list[dict[str, Any]]:
        return [
            self._ollama(
                "list_browser_pages",
                "Liste les pages du navigateur contrôlé via CDP.",
                {},
                [],
            ),
            self._ollama(
                "inspect_browser_page",
                "Inspecte le contenu d'une page web via DOM et rôles d'accessibilité. Préfère cet outil à UIA ou à la vision pour le contenu des sites.",
                {
                    "page_ref": {
                        "type": "string",
                        "description": "Référence optionnelle de page; omettre s'il n'y a qu'une page.",
                    }
                },
                [],
            ),
            self._ollama(
                "activate_browser_page",
                "Met au premier plan une page du navigateur contrôlé.",
                {"page_ref": {"type": "string", "description": "Référence exacte de page."}},
                ["page_ref"],
            ),
            self._ollama(
                "write_browser_element",
                "Écrit dans un champ DOM observé; la ref expire après mutation.",
                {
                    "page_ref": {"type": "string", "description": "Référence de page."},
                    "ref": {"type": "string", "description": "Ref opaque bobsN:eM."},
                    "text": {"type": "string", "description": "Texte à saisir."},
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "append", "insert"],
                        "description": "Mode d'écriture.",
                    },
                },
                ["ref", "text"],
            ),
            self._ollama(
                "click_browser_element",
                "Clique un élément DOM observé via sa ref opaque.",
                {
                    "page_ref": {"type": "string", "description": "Référence de page."},
                    "ref": {"type": "string", "description": "Ref opaque bobsN:eM."},
                },
                ["ref"],
            ),
            self._ollama(
                "press_browser_element",
                "Envoie une touche à un élément DOM observé, par exemple Enter.",
                {
                    "page_ref": {"type": "string", "description": "Référence de page."},
                    "ref": {"type": "string", "description": "Ref opaque bobsN:eM."},
                    "key": {"type": "string", "description": "Enter, Tab, Escape, etc."},
                },
                ["ref", "key"],
            ),
            self._ollama(
                "scroll_browser_element",
                "Fait défiler autour d'un élément DOM observé.",
                {
                    "page_ref": {"type": "string", "description": "Référence de page."},
                    "ref": {"type": "string", "description": "Ref opaque bobsN:eM."},
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Sens du défilement.",
                    },
                },
                ["ref", "direction"],
            ),
        ]

    def _browser_adapter(self):
        if not settings.browser_enabled or not settings.browser_cdp_url:
            return None
        if self._browser is None:
            from .browser_adapter import BrowserAdapter
            self._browser = BrowserAdapter(
                settings.browser_cdp_url,
                timeout_s=settings.browser_timeout_s,
            )
        return self._browser

    def execute_direct_browser_intent(self, intent: ToolIntent) -> ToolResult:
        """Execute deterministic browser fast-paths through CDP when enabled.

        The legacy Windows/browser path remains untouched when CDP is disabled.
        This prevents a CDP session from accidentally sending keyboard input to
        whichever non-browser window currently owns focus.
        """
        browser = self._browser_adapter()
        if browser is None:
            return execute(intent)

        try:
            if intent.name == "browser.open_url":
                url = str(intent.args.get("url") or "").strip()
                payload = browser.navigate(url)
                _remember_browser_url(str(payload.get("url") or url))
                return ToolResult(True, "C'est fait.", json.dumps(payload, ensure_ascii=False))

            if intent.name == "browser.search":
                query = str(intent.args.get("query") or "").strip()
                if not query:
                    return ToolResult(
                        True,
                        "Que voulez-vous rechercher ?",
                        "En attente du sujet de recherche",
                        follow_up="search_query",
                    )
                scope = str(intent.args.get("scope") or "context").strip().casefold()
                url = ""
                site_name = ""
                if scope != "web":
                    url, site_name = _contextual_site_search_url(query)
                if not url:
                    import urllib.parse
                    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
                    site_name = "Google"
                payload = browser.navigate(url)
                _remember_browser_url(str(payload.get("url") or url))
                return ToolResult(
                    True,
                    f"Je recherche {query} sur {site_name}.",
                    json.dumps(payload, ensure_ascii=False),
                )

            if intent.name == "browser.search_site":
                site = str(intent.args.get("site") or "").strip()
                query = str(intent.args.get("query") or "").strip()
                url, site_name = _explicit_site_search_url(site, query)
                if not url:
                    return ToolResult(
                        False,
                        f"Je n'ai pas de recherche directe fiable pour {site or 'ce site'}.",
                        site,
                    )
                payload = browser.navigate(url)
                _remember_browser_url(str(payload.get("url") or url))
                return ToolResult(
                    True,
                    f"Je recherche {query} sur {site_name}.",
                    json.dumps(payload, ensure_ascii=False),
                )

            if intent.name == "browser.back":
                payload = browser.back()
                _remember_browser_url(str(payload.get("url") or ""))
                return ToolResult(
                    True,
                    "Je reviens à la page précédente.",
                    json.dumps(payload, ensure_ascii=False),
                )

            if intent.name == "browser.close_tab":
                target = normalize(str(intent.args.get("name") or ""))
                pages = browser.pages()
                chosen = None
                if target:
                    matches = [
                        item for item in pages
                        if target in normalize(
                            f"{item.get('title','')} {item.get('url','')}"
                        )
                    ]
                    if len(matches) == 1:
                        chosen = matches[0]
                if chosen is None and len(pages) == 1:
                    chosen = pages[0]
                if chosen is None:
                    return ToolResult(
                        False,
                        "Je ne peux pas identifier un onglet unique à fermer.",
                        json.dumps({"pages": pages}, ensure_ascii=False),
                    )
                payload = browser.close_page(str(chosen.get("page_ref") or ""))
                return ToolResult(
                    True,
                    "L'onglet a été fermé.",
                    json.dumps(payload, ensure_ascii=False),
                )
        except Exception as exc:
            return ToolResult(
                False,
                "Le navigateur contrôlé n'a pas pu exécuter cette action.",
                f"{type(exc).__name__}: {exc}",
            )

        return execute(intent)

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
            new_instance = bool(args.get("new_instance", False))
            self._last_web_title_hint = ""

            observed_title = self._last_observed_window_title
            if (
                not new_instance
                and observed_title
                and self._safe_target(target)
                and (
                    normalize(target) in normalize(observed_title)
                    or normalize(observed_title) in normalize(target)
                    or (
                        self._last_app_hint
                        and (
                            normalize(target)
                            == normalize(self._last_app_hint)
                            or normalize(target)
                            in normalize(self._last_app_hint)
                            or normalize(self._last_app_hint)
                            in normalize(target)
                        )
                    )
                )
            ):
                existing = activate_window(observed_title)
                if existing.success:
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message=f"Application déjà ouverte; fenêtre réutilisée: {observed_title}.",
                        detail=json.dumps(
                            {
                                "reused_existing_window": True,
                                "target": target,
                                "window": observed_title,
                                "activation": existing.detail,
                            },
                            ensure_ascii=False,
                        ),
                    )

            learned = self._open_from_learned_profile(target)
            if learned is not None:
                return self._convert(name, learned)

            suffix = Path(target).suffix.lower()
            if suffix in {".exe", ".msi", ".bat", ".cmd", ".ps1"}:
                result = execute(
                    ToolIntent(
                        "file.open_named",
                        {"query": target, "within": "Téléchargements"},
                    )
                )
                return self._convert(name, result)
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
                "bloc notes": "notepad",
                "bloc-notes": "notepad",
                "notepad": "notepad",
                "capture ecran": "snippingtool",
                "outil capture": "snippingtool",
                "snipping tool": "snippingtool",
            }
            if normalized in known:
                result = execute(ToolIntent("app.open", {"app": known[normalized]}))
                if not result.success:
                    cua_launch = CUA_DRIVER.launch_application(target)
                    if cua_launch.success:
                        self._last_app_hint = target
                        invalidate_ui_snapshot()
                        return AgentActionResult(
                            name=name,
                            success=True,
                            message=cua_launch.message,
                            detail=json.dumps(
                                cua_launch.detail,
                                ensure_ascii=False,
                            ),
                        )
                    result = execute(
                        ToolIntent("app.open_named", {"query": target})
                    )
            else:
                cua_launch = CUA_DRIVER.launch_application(target)
                if cua_launch.success:
                    self._last_app_hint = target
                    invalidate_ui_snapshot()
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message=cua_launch.message,
                        detail=json.dumps(
                            cua_launch.detail,
                            ensure_ascii=False,
                        ),
                    )
                result = execute(ToolIntent("app.open_named", {"query": target}))
            self._record_app_launch(target, result)
            converted = self._convert(name, result)
            if converted.success:
                invalidate_ui_snapshot()
            return converted

        if name == "open_file":
            target = str(args.get("name", "")).strip()
            within = str(args.get("within", "")).strip()
            if not self._safe_target(target):
                return self._error(name, "Le nom du fichier est trop vague.")
            payload: dict[str, Any] = {"query": target}
            if within and self._safe_target(within):
                payload["within"] = within
            converted = self._convert(
                name,
                execute(ToolIntent("file.open_named", payload)),
            )
            if converted.success:
                invalidate_ui_snapshot()
            return converted

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
            converted = self._convert(name, result)
            if converted.success:
                invalidate_ui_snapshot()
            return converted

        if name == "open_url":
            url = str(args.get("url", "")).strip()
            if not url.startswith(("https://", "http://")):
                return self._error(name, "URL non autorisée ou invalide.")

            browser = self._browser_adapter()
            if browser is not None:
                try:
                    payload = browser.navigate(url)
                    _remember_browser_url(str(payload.get("url") or url))
                    self._last_web_title_hint = self._web_window_title_hint(url)
                    self._last_app_hint = "Google Chrome"
                    invalidate_ui_snapshot()
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="Page ouverte dans le navigateur contrôlé.",
                        detail=json.dumps(payload, ensure_ascii=False),
                    )
                except Exception as exc:
                    return AgentActionResult(
                        name=name,
                        success=False,
                        message="Le navigateur DOM/CDP n'a pas pu ouvrir cette page.",
                        detail=f"{type(exc).__name__}: {exc}",
                    )

            converted = self._convert(
                name,
                execute(ToolIntent("browser.open_url", {"url": url})),
            )
            if converted.success:
                self._last_web_title_hint = self._web_window_title_hint(url)
                self._last_app_hint = "Google Chrome"
                invalidate_ui_snapshot()
            return converted

        if name in {
            "list_browser_pages",
            "inspect_browser_page",
            "activate_browser_page",
            "write_browser_element",
            "click_browser_element",
            "press_browser_element",
            "scroll_browser_element",
        }:
            browser = self._browser_adapter()
            if browser is None:
                return self._error(name, "Le moteur DOM/CDP n'est pas activé.")
            page_ref = str(args.get("page_ref", "")).strip()
            try:
                if name == "list_browser_pages":
                    payload = {"pages": browser.pages()}
                    message = "Pages navigateur observées."
                elif name == "inspect_browser_page":
                    try:
                        payload = browser.observe(page_ref)
                    except ValueError as exc:
                        if "stale_or_unknown_page" not in str(exc):
                            raise
                        pages = browser.pages()
                        hint = normalize(self._last_web_title_hint)
                        site_label = normalize(
                            self._last_web_title_hint.split(" - ", 1)[0]
                        )
                        matches = [
                            item
                            for item in pages
                            if (
                                hint
                                and hint in normalize(str(item.get("title") or ""))
                            )
                            or (
                                site_label
                                and site_label in normalize(
                                    f"{item.get('title','')} {item.get('url','')}"
                                )
                            )
                        ]
                        if len(matches) == 1:
                            page_ref = str(matches[0].get("page_ref") or "")
                        elif len(pages) == 1:
                            page_ref = str(pages[0].get("page_ref") or "")
                        else:
                            raise
                        payload = browser.observe(page_ref)
                        payload["stale_page_ref_recovered"] = True
                    message = "Page observée via DOM."
                elif name == "activate_browser_page":
                    payload = browser.activate(page_ref)
                    message = "Page activée."
                else:
                    ref = str(args.get("ref", "")).strip()
                    if not ref:
                        return self._error(name, "La ref DOM est requise.")
                    if name == "write_browser_element":
                        operation = "write"
                        options = {
                            "text": str(args.get("text", "")),
                            "mode": str(args.get("mode", "replace")).strip() or "replace",
                        }
                    elif name == "click_browser_element":
                        operation, options = "click", {}
                    elif name == "press_browser_element":
                        operation = "key"
                        options = {"key": str(args.get("key", "")).strip()}
                    else:
                        operation = "scroll"
                        options = {
                            "direction": str(args.get("direction", "down")).strip() or "down"
                        }
                    payload = browser.act(page_ref, ref, operation, options)
                    fresh_page_ref = str(payload.get("page_ref") or page_ref)
                    try:
                        payload["post_observation"] = self._compact_observation(
                            browser.observe(fresh_page_ref)
                        )
                        payload["auto_reinspection"] = True
                    except Exception as post_exc:
                        payload["post_observation_error"] = (
                            f"{type(post_exc).__name__}: {post_exc}"
                        )
                    message = "Action DOM exécutée."
                return AgentActionResult(
                    name=name,
                    success=True,
                    message=message,
                    detail=json.dumps(payload, ensure_ascii=False),
                )
            except Exception as exc:
                return AgentActionResult(
                    name=name,
                    success=False,
                    message="Le moteur DOM/CDP n'a pas pu exécuter cette opération.",
                    detail=f"{type(exc).__name__}: {exc}",
                )

        if name in {"research_web", "search_web"}:
            query = str(args.get("query", "")).strip()
            if len(query) < 2:
                return self._error(name, "La recherche est vide.")
            result = BACKGROUND_WEB_RESEARCH.research(query)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=(
                    "Recherche web en arrière-plan terminée."
                    if result.success
                    else "La recherche web en arrière-plan a échoué."
                ),
                detail=json.dumps(result.as_dict(), ensure_ascii=False),
            )

        if name == "open_web_search":
            query = str(args.get("query", "")).strip()
            if len(query) < 2:
                return self._error(name, "La recherche est vide.")
            base = execute(ToolIntent("browser.search", {"query": query}))
            return AgentActionResult(
                name=name,
                success=base.success,
                message=(
                    "La page de recherche a été ouverte dans le navigateur."
                    if base.success
                    else base.message
                ),
                detail=json.dumps(
                    {
                        "opened_url": base.detail,
                        "query": query,
                        "results_read": False,
                        "factual_evidence": False,
                        "visible_browser_opened": bool(base.success),
                    },
                    ensure_ascii=False,
                ),
            )

        if name == "inspect_interface":
            title = str(args.get("title", "")).strip()
            focus = str(args.get("focus", "")).strip()
            structured = self.execute(
                "inspect_active_window",
                {"title": title} if title else {},
            )
            if not structured.success:
                return AgentActionResult(
                    name=name,
                    success=False,
                    message=structured.message,
                    detail=structured.detail,
                )
            try:
                payload = json.loads(structured.detail or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            snapshot = dict(payload.get("snapshot") or {})
            coverage = str(snapshot.get("semantic_coverage") or "").strip().lower()
            insufficient = (
                coverage == "insufficient"
                or snapshot.get("vision_recommended") is True
            )
            perception = {
                "structured_success": True,
                "structured_coverage": coverage or "unknown",
                "vision_attempted": False,
                "vision_success": False,
            }
            if insufficient and settings.vision_enabled:
                perception["vision_attempted"] = True
                window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
                visual_title = str(window.get("title") or title).strip() or None
                visual = observe_screen(
                    title=visual_title,
                    focus=focus or (
                        "Identifie uniquement les contrôles utiles à la mission actuelle."
                    ),
                    compact=True,
                )
                perception["vision_success"] = bool(visual.success)
                if visual.success:
                    try:
                        visual_payload = json.loads(visual.detail or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        visual_payload = {"raw": visual.detail}
                    payload["visual_observation"] = visual_payload
                else:
                    perception["vision_error"] = visual.detail or visual.message
            payload["perception"] = perception
            return AgentActionResult(
                name=name,
                success=True,
                message=(
                    "Interface inspectée avec perception structurée"
                    + (
                        " et vision."
                        if perception["vision_success"]
                        else "."
                    )
                ),
                detail=json.dumps(
                    self._compact_observation(payload, limit=28),
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
            requested_title = str(args.get("title", "")).strip() or None
            title = self._resolve_web_window_title(requested_title)
            result = inspect_active_window(title=title)
            if (
                not result.success
                and title
                and self._last_app_hint
                and normalize(title) != normalize(self._last_app_hint)
            ):
                result = inspect_active_window(title=self._last_app_hint)
            if not result.success and title:
                result = inspect_active_window(title=None)
            self._record_inspected_app(result)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "observe_screen":
            title = str(args.get("title", "")).strip() or None
            focus = str(args.get("focus", "")).strip()
            result = observe_screen(
                title=title,
                focus=focus,
                compact=bool(focus),
            )
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "click_visual_target":
            title = str(args.get("title", "")).strip() or None
            target = str(args.get("target", "")).strip()
            result = click_visual_target(target=target, title=title)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "write_visual_target":
            title = str(args.get("title", "")).strip() or None
            target = str(args.get("target", "")).strip()
            text_value = str(args.get("text", ""))
            mode = str(args.get("mode", "replace")).strip() or "replace"
            result = write_visual_target(
                target=target,
                text=text_value,
                title=title,
                mode=mode,
            )
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
                delivery_mode=str(
                    args.get("delivery_mode", "background")
                ).strip() or "background",
            )
            action = AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )
            return self._attach_windows_post_observation(action)

        if name == "close_window":
            title = str(args.get("title", "")).strip() or None
            result = close_window(title)
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "close_tab":
            target = str(args.get("name", "")).strip()
            result = close_tab(target)
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
            mode = str(args.get("mode", "replace")).strip() or "replace"
            delivery_mode = str(
                args.get("delivery_mode", "background")
            ).strip() or "background"
            result = write_ui_element(
                target,
                text,
                ref=ref,
                mode=mode,
                delivery_mode=delivery_mode,
            )
            if not result.success and ref:
                try:
                    stale = bool(
                        json.loads(result.detail or "{}").get("stale_ref")
                    )
                except Exception:
                    stale = False
                if stale:
                    recovered = self._recover_stale_write(
                        ref=ref,
                        target=target,
                        text=text,
                        mode=mode,
                        delivery_mode=delivery_mode,
                    )
                    if recovered is not None:
                        result = recovered
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "type_text_active_window":
            text_value = str(args.get("text", ""))
            title = str(args.get("title", "")).strip()
            mode = str(args.get("mode", "insert")).strip() or "insert"
            result = type_text_active_window(
                text_value,
                title=title,
                mode=mode,
            )
            return AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )

        if name == "search_application":
            title = str(args.get("title", "")).strip() or self._last_app_hint
            query = str(args.get("query", "")).strip()
            if not title or not query:
                return self._error(
                    name,
                    "L'application cible et la recherche sont requises.",
                )

            normalized_title = normalize(title)
            shortcut = next(
                (
                    value
                    for app_key, value in _DECLARED_APP_SEARCH_SHORTCUTS.items()
                    if app_key in normalized_title
                ),
                "",
            )
            if not shortcut:
                return AgentActionResult(
                    name=name,
                    success=False,
                    message=(
                        "Aucun raccourci de recherche fiable n'est déclaré "
                        "pour cette application."
                    ),
                    detail="app_search_shortcut_not_declared",
                )

            activation = activate_window(title)
            if not activation.success:
                return AgentActionResult(
                    name=name,
                    success=False,
                    message="Impossible d'activer l'application cible.",
                    detail=activation.detail,
                )

            focused = press_key(shortcut)
            if not focused.success:
                return AgentActionResult(
                    name=name,
                    success=False,
                    message="Impossible d'ouvrir la recherche native.",
                    detail=focused.detail,
                )
            time.sleep(0.12)

            typed = type_text_active_window(
                query,
                title=title,
                mode="replace",
                reactivate=False,
            )
            return AgentActionResult(
                name=name,
                success=typed.success,
                message=(
                    f"Recherche « {query} » saisie dans {title}."
                    if typed.success
                    else typed.message
                ),
                detail=json.dumps(
                    {
                        "application": title,
                        "query": query,
                        "shortcut": shortcut,
                        "typed": bool(typed.success),
                        "typing_detail": typed.detail,
                    },
                    ensure_ascii=False,
                ),
            )

        if name == "press_key":
            key = str(args.get("key", "")).strip()
            result = press_key(key)
            action = AgentActionResult(
                name=name,
                success=result.success,
                message=result.message,
                detail=result.detail,
            )
            return self._attach_windows_post_observation(action)

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

        if name == "search_agent_knowledge":
            query = str(args.get("query", "")).strip()
            if len(query) < 3:
                return self._error(name, "La recherche de connaissance est trop vague.")
            payload = self.knowledge.relevant_context(
                query,
                limit=int(args.get("limit") or 4),
            )
            return AgentActionResult(
                name=name,
                success=True,
                message="Connaissances opérationnelles locales consultées.",
                detail=json.dumps(payload, ensure_ascii=False),
            )

        if name == "save_verified_skill":
            try:
                item = self.knowledge.upsert_skill(
                    name=str(args.get("name", "")).strip(),
                    goal=str(args.get("goal", "")).strip(),
                    app_scope=str(args.get("app_scope", "")).strip(),
                    procedure=list(args.get("procedure") or []),
                    success_checks=list(args.get("success_checks") or []),
                    failure_patterns=list(args.get("failure_patterns") or []),
                    confidence=float(args.get("confidence") or 0.7),
                    source="verified_agent",
                )
            except (TypeError, ValueError) as exc:
                return self._error(name, str(exc))
            return AgentActionResult(
                name=name,
                success=True,
                message="Skill opérationnel enregistré localement.",
                detail=json.dumps(
                    {
                        "skill_id": item.id,
                        "name": item.name,
                        "version": item.version,
                    },
                    ensure_ascii=False,
                ),
            )

        if name == "save_feedback_lesson":
            try:
                item = self.knowledge.record_lesson(
                    scope=str(args.get("scope", "global")).strip() or "global",
                    pattern=str(args.get("pattern", "")).strip(),
                    rule=str(args.get("rule", "")).strip(),
                    confidence=float(args.get("confidence") or 0.8),
                    source="user_feedback",
                )
            except (TypeError, ValueError) as exc:
                return self._error(name, str(exc))
            return AgentActionResult(
                name=name,
                success=True,
                message="Leçon opérationnelle enregistrée localement.",
                detail=json.dumps(
                    {
                        "lesson_id": item.id,
                        "evidence_count": item.evidence_count,
                    },
                    ensure_ascii=False,
                ),
            )

        if name == "agent_knowledge_stats":
            return AgentActionResult(
                name=name,
                success=True,
                message="Statistiques de connaissance locale.",
                detail=json.dumps(
                    self.knowledge.stats(),
                    ensure_ascii=False,
                ),
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

    def _open_from_learned_profile(self, target: str) -> ToolResult | None:
        if not settings.operational_learning_enabled:
            return None
        if not self._safe_target(target):
            return None
        try:
            context = self.knowledge.relevant_context(target, limit=3)
        except Exception:
            return None

        for profile in list(context.get("app_profiles") or []):
            hint = str(profile.get("launch_hint") or "").strip()
            if not hint:
                continue
            if hint.lower().startswith("application ouverte via raccourci:"):
                hint = hint.split(":", 1)[1].strip()
            expanded = Path(os.path.expandvars(os.path.expanduser(hint)))
            if not expanded.exists():
                continue
            try:
                os.startfile(str(expanded))
            except OSError:
                continue
            self._last_app_hint = str(
                profile.get("display_name") or target
            )
            return ToolResult(
                True,
                "C'est fait.",
                str(expanded),
            )
        return None

    def _record_app_launch(self, target: str, result: ToolResult) -> None:
        # Session-local grounding is not persistent learning. Keep the app
        # identity even in baseline mode so a later request does not relaunch
        # an application whose observed window is already available.
        if result.success:
            self._last_app_hint = target

        if not settings.operational_learning_enabled:
            return
        try:
            self.knowledge.upsert_app_profile(
                display_name=target,
                aliases=[target],
                launch_hint=result.detail if result.success else "",
                success=result.success,
                observed_capabilities=["launch"],
            )
        except Exception:
            pass

    def _record_inspected_app(self, result) -> None:
        if not result.success:
            return
        try:
            payload = json.loads(result.detail or "{}")
            window = dict(payload.get("window") or {})
            title = str(window.get("title") or "").strip()
            if not title:
                return
            self._last_observed_window_title = title
            if not settings.operational_learning_enabled:
                return
            parts = [
                part.strip()
                for part in title.replace("–", " - ").replace("—", " - ").split(" - ")
                if part.strip()
            ]
            display = self._last_app_hint or (parts[-1] if parts else title)
            controls = list(payload.get("controls") or [])
            capabilities = sorted(
                {
                    str(item.get("type") or "").strip()
                    for item in controls
                    if str(item.get("type") or "").strip()
                }
            )[:20]
            self.knowledge.upsert_app_profile(
                display_name=display,
                aliases=[display],
                window_title_patterns=[title],
                observed_capabilities=capabilities,
                success=True,
            )
        except Exception:
            pass

    def _recover_stale_write(
        self,
        *,
        ref: str,
        target: str,
        text: str,
        mode: str,
        delivery_mode: str,
    ):
        """Reobserve and reacquire a stale writable control without guessing.

        Recovery is intentionally narrow: exact control identity wins; if the
        old control had no stable label/id, the fresh snapshot must expose one
        and only one writable control of the same type.
        """
        descriptor = ui_ref_descriptor(ref)
        if not descriptor or not descriptor.get("writable"):
            return None

        old_title = str(descriptor.get("window_title") or "").strip()
        current_title = str(self._last_observed_window_title or "").strip()
        if (
            old_title
            and current_title
            and normalize(old_title) != normalize(current_title)
        ):
            return None

        observed = inspect_active_window(
            title=old_title or current_title or None,
        )
        if (
            not observed.success
            and self._last_app_hint
            and normalize(self._last_app_hint)
            != normalize(old_title or current_title)
        ):
            observed = inspect_active_window(title=self._last_app_hint)
        if not observed.success:
            return None

        self._record_inspected_app(observed)
        try:
            payload = json.loads(observed.detail or "{}")
        except Exception:
            return None
        controls = [
            item
            for item in (payload.get("controls") or [])
            if isinstance(item, dict)
            and item.get("writable")
            and str(item.get("ref") or "").strip()
        ]
        if not controls:
            return None

        old_type = normalize(str(descriptor.get("type") or ""))
        old_id = normalize(str(descriptor.get("id") or ""))
        old_name = normalize(str(descriptor.get("name") or ""))
        old_label = normalize(str(descriptor.get("label") or ""))

        ranked: list[tuple[int, dict[str, Any]]] = []
        for item in controls:
            score = 0
            current_type = normalize(str(item.get("type") or ""))
            current_id = normalize(str(item.get("id") or ""))
            current_name = normalize(str(item.get("name") or ""))
            current_label = normalize(
                str(
                    item.get("label")
                    or item.get("label_hint")
                    or ""
                )
            )
            if old_type and current_type == old_type:
                score += 2
            if old_id and current_id == old_id:
                score += 6
            if old_name and current_name == old_name:
                score += 5
            if old_label and current_label == old_label:
                score += 5
            ranked.append((score, item))

        ranked.sort(key=lambda pair: -pair[0])
        chosen: dict[str, Any] | None = None
        if ranked and ranked[0][0] >= 5:
            if len(ranked) == 1 or ranked[0][0] > ranked[1][0]:
                chosen = ranked[0][1]
        elif len(controls) == 1:
            only = controls[0]
            if not old_type or normalize(str(only.get("type") or "")) == old_type:
                chosen = only

        if chosen is None:
            return None

        recovered_ref = str(chosen.get("ref") or "").strip()
        result = write_ui_element(
            target,
            text,
            ref=recovered_ref,
            mode=mode,
            delivery_mode=delivery_mode,
        )
        if result.detail:
            try:
                detail = json.loads(result.detail)
            except Exception:
                detail = {"raw_detail": result.detail}
        else:
            detail = {}
        detail.update(
            {
                "auto_reinspection": True,
                "recovered_from_stale_ref": ref,
                "recovered_ref": recovered_ref,
            }
        )
        return type(result)(
            success=result.success,
            message=result.message,
            detail=json.dumps(
                detail,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

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
