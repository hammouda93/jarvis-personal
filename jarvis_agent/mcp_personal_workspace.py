"""Jarvis operator-owned connection blueprint; zero effects on import.

A human-friendly inventory of the services relevant to the owner's projects.
This is NOT a credential registry, a profile with granted permission, a source
of tool schemas, or executable integration logic. Runtime authorization remains
exclusively in MCPRegistry/MCPControlInbox/MCPToolRegistry.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceConnection:
    identifier: str
    label: str
    area: str
    purpose: str
    integration: str  # remote_mcp | api_bridge | native | optional
    priority: int
    catalog_id: str = ""
    note: str = ""


CONNECTIONS: tuple[WorkspaceConnection, ...] = (
    WorkspaceConnection("github", "GitHub", "Développement",
        "Dépôts Jarvis, coaching, PR, commits, CI", "remote_mcp", 1, "github"),
    WorkspaceConnection("gmail", "Gmail", "Organisation",
        "Lire, préparer, envoyer et classer selon les outils autorisés", "remote_mcp", 1, "gmail",
        "Parcours guidé dans l’onglet Gmail : connexion Google gérée par Pipedream ou serveur MCP existant."),
    WorkspaceConnection("google_calendar", "Google Calendar", "Organisation",
        "Consulter les agendas et préparer les rendez-vous", "remote_mcp", 1, "google_calendar",
        "Google Workspace Developer Preview et OAuth peuvent être requis."),
    WorkspaceConnection("google_drive", "Google Drive", "Organisation",
        "Retrouver les fichiers et documents", "remote_mcp", 1, "google_drive",
        "Google Workspace Developer Preview et OAuth peuvent être requis."),
    WorkspaceConnection("google_docs", "Google Docs", "Organisation",
        "Lire et préparer des documents", "remote_mcp", 1, "google_docs"),
    WorkspaceConnection("google_sheets", "Google Sheets", "Organisation",
        "Données et tableaux de suivi", "remote_mcp", 1, "google_sheets"),
    WorkspaceConnection("ms_football", "MS Football (Django)", "Football",
        "Demandes vidéo, joueurs, analyses et suivi des prestations", "api_bridge", 1,
        note="Utiliser un adaptateur avec permissions par client, pas un accès admin global."),
    WorkspaceConnection("windows", "Windows et fichiers locaux", "Ordinateur",
        "Applications, explorateur et fichiers", "native", 1,
        note="Déjà natif : aucune connexion MCP nécessaire."),
    WorkspaceConnection("chrome", "Chrome et navigateur", "Ordinateur",
        "Sites, profils Chrome, onglets, formulaires", "native", 1,
        note="Déjà natif via Browser Bridge, ne pas partager les sessions par défaut."),
    WorkspaceConnection("google_slides", "Google Slides", "Organisation",
        "Présentations, scouting et rapports", "remote_mcp", 2, "google_slides"),
    WorkspaceConnection("google_contacts", "Google Contacts / People", "Organisation",
        "Recherche de contacts et correspondants", "remote_mcp", 2, "google_contacts"),
    WorkspaceConnection("cloudflare", "Cloudflare", "Déploiement",
        "Workers, domaines, observabilité et incidents", "remote_mcp", 2, "cloudflare",
        "Nécessite authentification Cloudflare et droits restreints."),
    WorkspaceConnection("heroku", "Heroku", "Déploiement",
        "Apps Django, journaux, versions et dynos", "api_bridge", 2,
        note="Aucune URL MCP universelle de gestion de compte présumée."),
    WorkspaceConnection("postgresql", "PostgreSQL", "Déploiement",
        "Lecture contrôlée de bases de données des applications", "api_bridge", 2,
        note="Compte technique lecture seule, réseau limité et bases isolées."),
    WorkspaceConnection("coaching_app", "Coaching App (Django)", "Coaching",
        "Adhérents, souscriptions, paiements et séances", "api_bridge", 2,
        note="Séparer données personnelles, lecture et mutations approuvées."),
    WorkspaceConnection("coach_workspace", "Coach Workspace / Club Workspace", "Coaching",
        "Planification, analyste, saison et match", "api_bridge", 2,
        note="Connecter quand l'API de l'application sera prête."),
    WorkspaceConnection("whatsapp", "WhatsApp", "Communication",
        "Échanges personnels et professionnels", "api_bridge", 2,
        note="WhatsApp personnel via interface observée; Business via Cloud API éligible."),
    WorkspaceConnection("youtube", "YouTube", "Football",
        "Chaînes et vidéos highlights", "api_bridge", 2,
        note="Préserver les deux profils et l'autorisation séparée pour publier."),
    WorkspaceConnection("sportsbase", "SportsBase", "Football",
        "Données All Player Actions et séquences de match", "api_bridge", 2,
        note="Ne pas présumer l'existence d'une API publique autorisée."),
    WorkspaceConnection("google_maps", "Google Maps", "Recherche",
        "Recherche de lieux et trajets", "remote_mcp", 3, "google_maps",
        "Clé Google Cloud restreinte et coût/quota à vérifier."),
    WorkspaceConnection("dailymotion", "Dailymotion", "Football",
        "Vidéos de secours", "api_bridge", 3),
    WorkspaceConnection("railway", "Railway", "Déploiement",
        "Tests Django/PostgreSQL si ce fournisseur est utilisé", "optional", 3),
    WorkspaceConnection("web_research", "Recherche Web", "Recherche",
        "Recherche multi-source et rapports", "native", 3,
        note="Capacité Web à exposer par les outils existants, pas un faux MCP."),
    WorkspaceConnection("reminders", "Rappels et notifications", "Vie personnelle",
        "Suivi de tâches et automatisations", "native", 3,
        note="S'appuyer sur le scheduler local et les permissions."),
    WorkspaceConnection("islamic", "Prières et sources coraniques", "Vie personnelle",
        "Horaires fiables, lecture et rappels", "api_bridge", 3,
        note="Adapter à des sources API avec fuseau horaire et localisation explicites."),
    WorkspaceConnection("telegram", "Telegram", "Communication",
        "Messages facultatifs", "optional", 3,
        note="N'activer que si souhaité; vérifier les permissions de bot."),
    WorkspaceConnection("hermes", "Hermes local (optionnel)", "Développement",
        "Serveur MCP complémentaire, sans remplacer Jarvis", "remote_mcp", 3,
        "hermes", "N'exécuter aucun serveur local sans confiance explicite."),
)


def get_connection(identifier: str) -> WorkspaceConnection | None:
    return next((entry for entry in CONNECTIONS if entry.identifier == identifier), None)


def connection_state(entry: WorkspaceConnection, servers: tuple | list) -> str:
    """Read-only UI summary, never claim a persistent live MCP session."""
    if entry.integration == "native":
        return "Natif Jarvis · sans MCP"
    if entry.integration in {"api_bridge", "optional"}:
        return "Pont/API à préparer" if entry.integration == "api_bridge" else "Facultatif"
    server = next((item for item in servers
                   if isinstance(item, dict) and item.get("id") == entry.catalog_id), None)
    if server is None:
        return "Non configuré"
    if server.get("enabled") is not True:
        return "Enregistré · désactivé"
    count = int(server.get("allowed") or 0) if type(server.get("allowed")) is int else 0
    discovered = bool(server.get("last_discovery_success_utc"))
    if discovered and count:
        return "Découvert · outils autorisés (session non garantie)"
    if discovered:
        return "Découvert · outils non autorisés"
    return "Activé · connexion non testée"


def workspace_summary(servers: tuple | list) -> str:
    """Local operator preview, bounded and free of credentials or remote data."""
    labels = {1: "ESSENTIEL", 2: "MÉTIER & HÉBERGEMENT", 3: "COMPLÉMENTAIRE"}
    lines = [
        "Ce profil prépare les connexions, il ne les établit pas.",
        "Activer un service et ses outils exige toujours une action explicite.",
    ]
    for priority in (1, 2, 3):
        lines.append("")
        lines.append(labels[priority])
        for entry in CONNECTIONS:
            if entry.priority == priority:
                lines.append(f"• {entry.label} : {connection_state(entry, servers)}")
    return "\n".join(lines)
