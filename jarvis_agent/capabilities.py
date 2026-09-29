from __future__ import annotations

from dataclasses import dataclass

from .tools import ToolIntent, normalize


@dataclass(frozen=True)
class MissingCapability:
    key: str
    message: str


def detect_missing_capability(user_text: str) -> MissingCapability | None:
    """Recognize clearly requested capabilities that Jarvis does not have yet."""
    text = normalize(user_text)

    if "vlc" in text and any(
        token in text for token in ("ouvre", "ouvrir", "lance", "lancer")
    ):
        return MissingCapability(
            "app.vlc",
            "J'ai compris que vous voulez ouvrir VLC Media Player. "
            "Je ne dispose pas encore de l'outil VLC sur ce Jarvis.",
        )

    if "whatsapp" in text and any(
        token in text
        for token in ("message", "envoie", "envoyer", "ecris", "écris")
    ):
        return MissingCapability(
            "communication.whatsapp",
            "J'ai compris que vous voulez envoyer un message WhatsApp. "
            "Je ne dispose pas encore de l'outil WhatsApp et contacts pour envoyer le message.",
        )

    if "meteo" in text or "météo" in user_text.lower():
        return MissingCapability(
            "weather.current",
            "J'ai compris que vous voulez une météo actuelle ou prévisionnelle. "
            "Je n'ai pas encore connecté Jarvis à une source météo en temps réel.",
        )

    return None


def confirmation_prompt(intent: ToolIntent) -> str:
    if intent.name == "browser.open_url":
        url = str(intent.args.get("url", ""))
        if "youtube.com" in url:
            return "Voulez-vous que j'ouvre YouTube ?"
        if "google.com" in url:
            return "Voulez-vous que j'ouvre Google ?"
        return "Voulez-vous que j'ouvre ce site ?"

    if intent.name == "browser.search":
        query = str(intent.args.get("query", "")).strip()
        if query:
            return f"Voulez-vous que je recherche {query} sur Internet ?"
        return "Voulez-vous que je lance une recherche sur Internet ?"

    if intent.name == "app.open":
        app = str(intent.args.get("app", "")).strip()
        labels = {
            "chrome": "Chrome",
            "spotify": "Spotify",
            "cursor": "Cursor",
            "vscode": "VS Code",
            "snippingtool": "l'outil Capture d'écran",
        }
        label = labels.get(app, app or "cette application")
        return f"Voulez-vous que j'ouvre {label} ?"

    if intent.name == "folder.open":
        folder = str(intent.args.get("folder", "")).strip()
        label = "le dossier Téléchargements" if folder == "downloads" else folder
        return f"Voulez-vous que j'ouvre {label} ?"

    if intent.name == "system.time":
        return "Voulez-vous que je vous donne l'heure actuelle ?"

    if intent.name == "assistant.sleep":
        return "Voulez-vous que je retourne en veille ?"

    return "Voulez-vous que j'exécute cette action ?"


def is_affirmative(text: str) -> bool:
    value = normalize(text)
    return value in {
        "oui",
        "oui vas y",
        "oui vas-y",
        "vas y",
        "vas-y",
        "d accord",
        "d'accord",
        "exactement",
        "c est ca",
        "c'est ca",
        "fais le",
        "fait le",
    }


def is_negative(text: str) -> bool:
    value = normalize(text)
    return value in {
        "non",
        "non merci",
        "annule",
        "annuler",
        "laisse",
        "laisse tomber",
        "pas maintenant",
    }
