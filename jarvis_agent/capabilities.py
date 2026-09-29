from __future__ import annotations

from dataclasses import dataclass

from .language import normalize_language
from .tools import ToolIntent, normalize


@dataclass(frozen=True)
class MissingCapability:
    key: str
    messages: dict[str, str]

    def message(self, language: str | None) -> str:
        lang = normalize_language(language)
        return self.messages.get(lang, self.messages["fr"])


def detect_missing_capability(user_text: str) -> MissingCapability | None:
    """Recognize clearly requested capabilities that Jarvis does not have yet."""
    text = normalize(user_text)

    if "whatsapp" in text and any(
        token in text
        for token in ("message", "envoie", "envoyer", "ecris", "écris", "send")
    ):
        return MissingCapability(
            "communication.whatsapp",
            {
                "fr": "J'ai compris que vous voulez envoyer un message WhatsApp. Je ne dispose pas encore de l'outil WhatsApp et contacts.",
                "en": "I understand that you want to send a WhatsApp message. I don't have the WhatsApp and contacts tool yet.",
                "ar": "فهمت أنك تريد إرسال رسالة واتساب، لكن أداة واتساب وجهات الاتصال غير متاحة لدي بعد.",
            },
        )

    if "meteo" in text or "météo" in user_text.lower() or "weather" in text:
        return MissingCapability(
            "weather.current",
            {
                "fr": "J'ai compris que vous voulez la météo. Je n'ai pas encore connecté Jarvis à une source météo en temps réel.",
                "en": "I understand that you want the weather. Jarvis isn't connected to a real-time weather source yet.",
                "ar": "فهمت أنك تريد معرفة الطقس، لكن Jarvis غير متصل بعد بمصدر طقس لحظي.",
            },
        )

    return None


def confirmation_prompt(intent: ToolIntent, language: str | None = None) -> str:
    lang = normalize_language(language)

    if intent.name == "browser.open_url":
        url = str(intent.args.get("url", ""))
        if "youtube.com" in url:
            return {
                "fr": "Voulez-vous que j'ouvre YouTube ?",
                "en": "Do you want me to open YouTube?",
                "ar": "هل تريدني أن أفتح يوتيوب؟",
            }[lang]
        if "google.com" in url:
            return {
                "fr": "Voulez-vous que j'ouvre Google ?",
                "en": "Do you want me to open Google?",
                "ar": "هل تريدني أن أفتح جوجل؟",
            }[lang]

    if intent.name == "browser.search":
        query = str(intent.args.get("query", "")).strip()
        if query:
            return {
                "fr": f"Voulez-vous que je recherche {query} sur Internet ?",
                "en": f"Do you want me to search the web for {query}?",
                "ar": f"هل تريدني أن أبحث على الإنترنت عن {query}؟",
            }[lang]

    if intent.name == "app.open":
        app = str(intent.args.get("app", "")).strip()
        labels = {
            "chrome": "Chrome",
            "spotify": "Spotify",
            "cursor": "Cursor",
            "vscode": "VS Code",
            "snippingtool": "Capture d'écran",
        }
        label = labels.get(app, app or "cette application")
        return {
            "fr": f"Voulez-vous que j'ouvre {label} ?",
            "en": f"Do you want me to open {label}?",
            "ar": f"هل تريدني أن أفتح {label}؟",
        }[lang]

    if intent.name in {"folder.open", "folder.open_named"}:
        folder = str(intent.args.get("query") or intent.args.get("folder") or "").strip()
        label = "Téléchargements" if folder == "downloads" else folder
        return {
            "fr": f"Voulez-vous que j'ouvre le dossier {label} ?",
            "en": f"Do you want me to open the {label} folder?",
            "ar": f"هل تريدني أن أفتح مجلد {label}؟",
        }[lang]

    if intent.name == "system.time":
        return {
            "fr": "Voulez-vous que je vous donne l'heure actuelle ?",
            "en": "Do you want me to tell you the current time?",
            "ar": "هل تريدني أن أخبرك بالوقت الحالي؟",
        }[lang]

    return {
        "fr": "Voulez-vous que j'exécute cette action ?",
        "en": "Do you want me to do that?",
        "ar": "هل تريدني أن أنفذ هذا الإجراء؟",
    }[lang]


def is_affirmative(text: str) -> bool:
    value = normalize(text)
    return value in {
        "oui", "oui vas y", "oui vas-y", "vas y", "vas-y",
        "d accord", "d'accord", "exactement", "c est ca", "c'est ca",
        "fais le", "fait le", "yes", "yes do it", "go ahead",
        "نعم", "ايوه", "إيوه", "اي", "نفذ", "اعملها",
    }


def is_negative(text: str) -> bool:
    value = normalize(text)
    return value in {
        "non", "non merci", "annule", "annuler", "laisse",
        "laisse tomber", "pas maintenant", "no", "no thanks", "cancel",
        "لا", "لا شكرا", "الغ", "ألغي",
    }
