"""Read-only product projection of existing manifests and observed runtime flags.

This is not a second registry, router or permission store. Display associations
do not dispatch agents or grant tools. A declared specialist is not an active
independent worker. No model, network or filesystem access belongs here.
"""
from __future__ import annotations

from .capability_registry import DEFAULT_CAPABILITY_REGISTRY
from .project_roadmap import snapshot as roadmap_snapshot


_LABELS = {
    "interaction": ("Jarvis généraliste", "Coordonne la conversation, les missions et leurs résultats."),
    "windows": ("Windows", "Observe les fenêtres et agit sur des contrôles réellement identifiés."),
    "browser": ("Navigateur", "Observe le DOM de Chrome, navigue et vérifie les actions ciblées."),
    "research": ("Recherche", "Recherche des sources et distingue données observées et conclusions."),
    "documents": ("Documents et fichiers", "Retrouve, ouvre et vérifie des fichiers ; création via les outils d’interface."),
    "data": ("Données", "Rôle d’analyse ; traitements spécialisés et connecteurs restent à intégrer."),
    "reports": ("Rapports", "Synthèse par Jarvis ; exécuteur spécialisé encore à développer."),
    "communications": ("Communication", "Rôle mail et messageries ; accès uniquement via services autorisés."),
    "developer": ("Développement", "Inspection via outils connectés ; exécution de code isolée encore à développer."),
    "automations": ("Automatisations", "Contrats et planification existants ; service permanent de bout en bout à développer."),
    "mcp": ("Services MCP", "Accès aux capacités découvertes et autorisées dans le centre MCP."),
    "ms_football": ("MS Football", "Agent métier avec outils de lecture et mutations préparées puis approuvées."),
}

# Code-level tools already implemented, grouped for display. The manifest's
# allowlist is shown separately: these groups never amend kernel authority.
_NATIVE = {
    "interaction": ("get_current_time", "mission_checkpoint", "request_tool_capabilities", "read_observation_evidence",
                    "workspace_read", "workspace_save_activity", "workspace_update_profile", "workspace_prepare_action"),
    "windows": ("list_windows", "list_applications", "open_application", "inspect_active_window",
                "click_ui_element", "write_ui_element", "press_key", "close_window"),
    "browser": ("open_url", "browser_list_tabs", "browser_get_active_tab", "browser_observe_dom",
                "browser_find", "browser_click", "browser_write", "browser_press", "browser_navigate",
                "browser_back", "browser_close_tab", "browser_verify"),
    "research": ("research_web", "open_web_search"),
    "documents": ("find_file", "open_file", "open_folder", "verify_file_artifact"),
    "ms_football": ("msf_capabilities", "msf_query_records", "msf_count_records", "msf_describe_schema",
                    "msf_prepare_mutation", "msf_commit_mutation", "msf_list_routes", "msf_search_code"),
}
_COMPUTER = ("computer_observe", "computer_find", "computer_focus_probe", "computer_click",
             "computer_write", "computer_press", "computer_shortcut", "computer_verify")
_PLANNED = (
    ("calendar", "Calendrier", "Rendez-vous, disponibilités et rappels via un service explicitement connecté.",
     ("calendar.read", "calendar.prepare", "calendar.confirm")),
    ("phone", "Téléphone", "Appels et actions téléphoniques avec connecteur et autorisations adaptés.",
     ("phone.inspect", "phone.prepare_call")),
    ("fashion", "Style et vie personnelle", "Conseils spécialisés et préférences explicitement fournies.",
     ("personal.advise",)),
    ("business_packs", "Autres agents métier", "Domaines ajoutés par profils : objectifs, compétences, outils et preuves propres.",
     ("domain.configure",)),
    ("cloud_worker", "Exécution cloud", "Exécuteurs isolés et service permanent ; déploiement et reprise à implémenter.",
     ("cloud.execute", "cloud.resume")),
)


def build_snapshot(runtime: dict | None = None, *, registry=None) -> dict:
    """Project safe metadata already received by the operator console."""
    state = runtime or {}
    registry = registry or DEFAULT_CAPABILITY_REGISTRY
    servers = [s for s in (state.get("mcp") or {}).get("servers", []) if isinstance(s, dict)]
    mcp_on = (state.get("mcp") or {}).get("enabled") is True
    agents = []
    for manifest in registry.agents():
        identifier = manifest.agent_id
        name, purpose = _LABELS.get(identifier, (manifest.display_name, manifest.description))
        capabilities = []
        for capability in manifest.capabilities:
            item = registry.get_capability(capability)
            capabilities.append({"name": capability,
                "description": item.spec.description if item else "Déclaration du manifeste",
                "risk": item.spec.risk.value if item else "unknown",
                "requires_confirmation": bool(item and item.spec.requires_confirmation)})
        native = list(_NATIVE.get(identifier, ()))
        if identifier == "interaction" and state.get("memory_enabled") is True:
            native.extend(("remember_information", "recall_information"))
            if state.get("semantic_memory_enabled") is True:
                native.extend(("semantic_memory_search", "semantic_memory_inspect",
                               "semantic_memory_events_on_date", "semantic_memory_events_in_range"))
        mode = "shared_runtime" if identifier in {"interaction", "windows", "browser", "research", "ms_football", "mcp"} else "declared"
        module = "Outils du runtime partagé ; validation réelle requise"
        if identifier == "windows":
            if state.get("computer_core_enabled") is True:
                native.extend(_COMPUTER)
                module = "Computer Core configuré actif · UIA/OCR ; disponibilité à vérifier sur Windows"
            else:
                module = "Outils Windows natifs intégrés · Computer Core désactivé"
        elif identifier == "browser":
            module = "Browser Core configuré actif · connexion Chrome à vérifier" if state.get("browser_core_enabled") else "Browser Core désactivé"
        elif identifier == "mcp":
            module = "MCP configuré actif · droits par outil" if mcp_on else "MCP désactivé"
        elif identifier == "ms_football":
            module = "Outils métier intégrés · configuration et recette métier à vérifier"
        elif mode == "declared":
            module = "Rôle déclaré ; aucun worker indépendant activé par ce panneau"
        linked = servers if identifier == "mcp" else [
            s for s in servers if s.get("id") in manifest.allowed_connectors]
        connections = []
        for server in linked:
            tools = [{"name": str(t.get("name") or "")[:100],
                      "allowed": t.get("allowed") is True,
                      "effective": mcp_on and server.get("enabled") is True and t.get("allowed") is True,
                      "automatic_read": t.get("auto_read") is True}
                     for t in server.get("tools", []) if isinstance(t, dict)]
            connections.append({"id": str(server.get("id") or "")[:36],
                "enabled": server.get("enabled") is True,
                "connection_state": str(server.get("connection_state") or "unknown")[:60],
                "connected_now": server.get("connected_now") is True,
                "tools": tools,
                "inventory_partial": bool(server.get("inventory_truncated")) or int(server.get("discovered") or 0) > len(tools)})
        agents.append({"id": identifier, "name": name, "purpose": purpose,
            "category": "Métier" if identifier == "ms_football" else "Spécialiste" if identifier != "interaction" else "Coordination",
            "mode": mode, "module": module, "capabilities": capabilities,
            "native_tools": native, "declared_tools": list(manifest.allowed_tools),
            "connectors": list(manifest.allowed_connectors), "connections": connections,
            "memory_scopes": [scope.value for scope in manifest.memory_scopes],
            "max_concurrency": manifest.max_concurrency,
            "independent_worker": False})
    for identifier, name, purpose, capabilities in _PLANNED:
        agents.append({"id": identifier, "name": name, "purpose": purpose,
            "category": "À développer", "mode": "planned", "module": "Planifié · aucun exécuteur actif",
            "capabilities": [{"name": c, "description": "Compétence cible", "risk": "unknown",
                              "requires_confirmation": False} for c in capabilities],
            "native_tools": [], "declared_tools": [], "connectors": [], "connections": [],
            "memory_scopes": [], "max_concurrency": 0, "independent_worker": False})
    blocks = []
    for server in servers:
        for pending in (server.get("activity") or {}).get("unresolved", []):
            if not isinstance(pending, dict):
                continue
            blocks.append({"server": str(server.get("id") or "")[:36],
                "attempt_id": pending.get("attempt_id"), "tool": str(pending.get("tool") or "")[:100],
                "reviewable": pending.get("reviewable") is True,
                "outcome": str(pending.get("outcome") or "unknown")[:20]})
    return {"agents": agents, "blocks": blocks, "roadmap": roadmap_snapshot(),
        "features": [{"name": label, "enabled": state.get(key) is True}
                     for key, label in (("memory_enabled", "Mémoire SQLite"),
                                        ("semantic_memory_enabled", "Mémoire V5"),
                                        ("memory_direct_enabled", "Outils mémoire directs"),
                                        ("browser_core_enabled", "Chrome habituel"),
                                        ("computer_core_enabled", "Windows UIA/OCR"),
                                        ("grounding_model_enabled", "Modèle de grounding"),
                                        ("efficient_context_enabled", "Contexte efficient"),
                                        ("mission_enabled", "Missions persistées"),
                                        ("skills_enabled", "Skills"), ("learning_enabled", "Learning"))],
        "notice": "Vue de l’existant et de la cible. Les associations affichées ne donnent aucun droit ; les outils restent contrôlés par le runtime et les autorisations MCP."}
