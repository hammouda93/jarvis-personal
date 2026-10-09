"""Documented MCP endpoints, checked 2026-10-09; no account acceptance claim.

These cards are UX hints, not executable skills, authorized credentials, MCP
implementations or hardcoded per-service automation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceCard:
    identifier: str
    label: str
    category: str
    requirement: str
    warning: str
    endpoint: str = ""
    documentation: str = ""
    authentication: str = ""
    scope: str = ""


_WORKSPACE_DOC = "https://developers.google.com/workspace/guides/configure-mcp-servers"
_WORKSPACE_REQUIREMENT = "Developer Preview Google Workspace, projet Cloud, APIs actives et client OAuth preenregistre."
_WORKSPACE_WARNING = "Acces conditionnel ; compte non teste dans Jarvis. Les connecteurs ChatGPT ne donnent aucun droit a Jarvis."


def _workspace(identifier, label, host, scope):
    return ServiceCard(identifier, label, "Google Workspace", _WORKSPACE_REQUIREMENT,
        _WORKSPACE_WARNING, f"https://{host}.googleapis.com/mcp/v1", _WORKSPACE_DOC, "oauth", scope)


SERVICE_CARDS: tuple[ServiceCard, ...] = (
    _workspace("gmail", "Gmail", "gmailmcp", "https://www.googleapis.com/auth/gmail.readonly"),
    _workspace("google_calendar", "Google Calendar", "calendarmcp", "https://www.googleapis.com/auth/calendar.calendarlist.readonly https://www.googleapis.com/auth/calendar.events.readonly"),
    _workspace("google_drive", "Google Drive", "drivemcp", "https://www.googleapis.com/auth/drive.readonly"),
    _workspace("google_docs", "Google Docs", "docsmcp", "https://www.googleapis.com/auth/documents.readonly https://www.googleapis.com/auth/drive.readonly"),
    _workspace("google_sheets", "Google Sheets", "sheetsmcp", "https://www.googleapis.com/auth/spreadsheets.readonly https://www.googleapis.com/auth/drive.readonly"),
    ServiceCard(
        "google_maps", "Google Maps", "Cartographie",
        "Maps Grounding Lite active, cle API restreinte dans X-Goog-Api-Key.",
        "Quotas et couts Google possibles ; compte non teste dans Jarvis.",
        "https://mapstools.googleapis.com/mcp", "https://developers.google.com/maps/ai/grounding-lite", "X-Goog-Api-Key",
    ),
    ServiceCard(
        "github", "GitHub", "Développement",
        "PAT GitHub limite ; OAuth exige votre propre application enregistree.",
        "Politiques du compte applicables ; authentification non testee dans Jarvis.",
        "https://api.githubcopilot.com/mcp/", "https://github.com/github/github-mcp-server", "bearer",
    ),
    ServiceCard(
        "hermes", "Hermes (serveur local)", "Agents",
        "Hermes doit déjà être installé pour le profil local facultatif.",
        "hermes mcp serve expose les conversations ; il ne relaie pas les autres MCP.",
        documentation="https://github.com/NousResearch/hermes-agent", authentication="stdio",
    ),
)


def find_card(identifier: str) -> ServiceCard | None:
    return next((x for x in SERVICE_CARDS if x.identifier == identifier), None)
