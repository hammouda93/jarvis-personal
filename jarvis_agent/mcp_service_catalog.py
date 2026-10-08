"""Presentation-only MCP provider catalog; NEVER invent or connect endpoints.

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


SERVICE_CARDS: tuple[ServiceCard, ...] = (
    ServiceCard(
        "gmail", "Gmail", "Communication",
        "Une adresse MCP Gmail fiable et une connexion OAuth distincte.",
        "ChatGPT Gmail ne transmet pas son autorisation à Jarvis.",
    ),
    ServiceCard(
        "google_sheets", "Google Sheets", "Documents",
        "Un serveur MCP Sheets fiable et les autorisations Google nécessaires.",
        "Lecture et édition doivent rester des outils séparément approuvés.",
    ),
    ServiceCard(
        "google_maps", "Google Maps", "Cartographie",
        "Un serveur MCP Maps fiable ; clé API/OAuth selon le fournisseur.",
        "Les quotas et coûts des API Google peuvent s'appliquer.",
    ),
    ServiceCard(
        "google_drive", "Google Drive", "Documents",
        "Un serveur MCP Drive fiable et accès OAuth explicite.",
        "Les fichiers ne deviennent jamais accessibles sans accord.",
    ),
    ServiceCard(
        "whatsapp", "WhatsApp", "Communication",
        "Un MCP WhatsApp approuvé ; compte personnel ou Business selon le serveur.",
        "WhatsApp Cloud officiel vise les comptes Business, pas les chats personnels.",
    ),
    ServiceCard(
        "github", "GitHub", "Développement",
        "Un serveur MCP GitHub approuvé avec droits limités.",
        "Le connecteur GitHub ChatGPT n'autorise pas automatiquement Jarvis.",
    ),
    ServiceCard(
        "hermes", "Hermes (serveur local)", "Agents",
        "Hermes doit déjà être installé pour le profil local facultatif.",
        "Ce pont expose seulement les outils MCP réellement offerts par Hermes.",
    ),
)


def find_card(identifier: str) -> ServiceCard | None:
    return next((x for x in SERVICE_CARDS if x.identifier == identifier), None)
