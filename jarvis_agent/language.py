from __future__ import annotations

import datetime as dt

from .tools import ToolIntent, ToolResult


SUPPORTED_LANGUAGES = {"fr", "en", "ar"}


def normalize_language(language: str | None) -> str:
    if not language:
        return "fr"
    code = language.lower().split("-")[0]
    return code if code in SUPPORTED_LANGUAGES else "fr"


def tool_message(
    intent: ToolIntent,
    result: ToolResult,
    language: str | None,
) -> str:
    lang = normalize_language(language)

    if intent.name == "browser.search_prompt" and result.success:
        return {
            "fr": "Que voulez-vous rechercher ?",
            "en": "What would you like me to search for?",
            "ar": "عن ماذا تريدني أن أبحث؟",
        }[lang]

    if intent.name == "folder.open_prompt" and result.success:
        return {
            "fr": "Quel dossier voulez-vous ouvrir ?",
            "en": "Which folder would you like me to open?",
            "ar": "أي مجلد تريدني أن أفتح؟",
        }[lang]

    if intent.name == "system.time" and result.success:
        try:
            current = dt.datetime.fromisoformat(result.detail)
            hhmm = current.strftime("%H:%M")
        except (TypeError, ValueError):
            hhmm = ""
        if lang == "en":
            return f"It is {hhmm}." if hhmm else "Here is the current time."
        if lang == "ar":
            return f"الوقت الآن {hhmm}." if hhmm else "هذا هو الوقت الحالي."
        return result.message

    if intent.name in {"browser.search", "browser.search_site"} and result.success:
        query = str(intent.args.get("query", "")).strip()
        if intent.name == "browser.search_site" and lang == "fr":
            return result.message
        if lang == "en":
            return f"I'm searching for {query}."
        if lang == "ar":
            return f"سأبحث عن {query}."
        return result.message or f"Je recherche {query}."

    if intent.name in {"browser.close_tab", "browser.back"} and result.success:
        return result.message

    if result.success:
        if intent.name == "assistant.stop":
            return {
                "fr": "À bientôt monsieur.",
                "en": "See you soon, sir.",
                "ar": "إلى اللقاء سيدي.",
            }[lang]
        if intent.name == "assistant.sleep":
            return {
                "fr": "Très bien, je retourne en veille.",
                "en": "All right, I'm going back to standby.",
                "ar": "حسنًا، سأعود إلى وضع الانتظار.",
            }[lang]
        if intent.name == "folder.open_named":
            if lang == "fr":
                return result.message
            name = str(intent.args.get("query", "")).strip()
            return {
                "en": f"I opened the {name} folder.",
                "ar": f"فتحت مجلد {name}.",
            }[lang]
        return {
            "fr": "C'est fait.",
            "en": "Done.",
            "ar": "تم.",
        }[lang]

    if not result.success and intent.name == "folder.open_named":
        query = str(intent.args.get("query", "")).strip()
        return {
            "fr": f"Je n'ai pas trouvé de dossier correspondant à {query}.",
            "en": f"I couldn't find a folder matching {query}.",
            "ar": f"لم أجد مجلدًا يطابق {query}.",
        }[lang]

    if not result.success and intent.name == "app.open_named":
        query = str(intent.args.get("query", "")).strip()
        return {
            "fr": f"Je n'ai pas trouvé d'application correspondant à {query}.",
            "en": f"I couldn't find an application matching {query}.",
            "ar": f"لم أجد تطبيقًا يطابق {query}.",
        }[lang]

    # Keep detailed capability-specific error messages when they contain
    # useful information, otherwise use a generic localized failure.
    if result.message and result.message not in {
        "Je n'ai pas pu ouvrir le navigateur.",
        "Je n'ai pas trouvé ce dossier.",
    }:
        return result.message

    return {
        "fr": "Je n'ai pas pu effectuer cette action.",
        "en": "I couldn't complete that action.",
        "ar": "لم أتمكن من تنفيذ هذا الإجراء.",
    }[lang]


def repeat_prompt(language: str | None) -> str:
    lang = normalize_language(language)
    return {
        "fr": "Je n'ai pas bien compris. Pouvez-vous répéter ?",
        "en": "I didn't understand that clearly. Could you repeat it?",
        "ar": "لم أفهم جيدًا. هل يمكنك إعادة ما قلت؟",
    }[lang]


def no_speech_prompt(language: str | None) -> str:
    lang = normalize_language(language)
    return {
        "fr": "Je ne vous ai pas entendu.",
        "en": "I didn't hear you.",
        "ar": "لم أسمعك.",
    }[lang]


def cancellation_prompt(language: str | None) -> str:
    lang = normalize_language(language)
    return {
        "fr": "D'accord, je n'exécute pas cette action.",
        "en": "All right, I won't do that.",
        "ar": "حسنًا، لن أنفذ هذا الإجراء.",
    }[lang]


def ai_unavailable_prompt(language: str | None) -> str:
    lang = normalize_language(language)
    return {
        "fr": "Mon cerveau local n'est pas disponible pour le moment, mais mes outils directs restent disponibles.",
        "en": "My local AI brain isn't available right now, but my direct tools still work.",
        "ar": "المحرك المحلي للذكاء الاصطناعي غير متاح الآن، لكن الأدوات المباشرة ما زالت تعمل.",
    }[lang]


def capability_unavailable_prompt(language: str | None) -> str:
    lang = normalize_language(language)
    return {
        "fr": "J'ai compris ce que vous voulez faire, mais je ne dispose pas encore de cette capacité.",
        "en": "I understand what you want to do, but I don't have that capability yet.",
        "ar": "فهمت ما تريد القيام به، لكن هذه القدرة غير متاحة لدي بعد.",
    }[lang]


def action_mismatch_prompt(language: str | None) -> str:
    lang = normalize_language(language)
    return {
        "fr": "Je pense avoir compris votre intention, mais l'action proposée ne correspond pas assez précisément. Pouvez-vous préciser ce que vous voulez que je fasse ?",
        "en": "I think I understand your intent, but the proposed action doesn't match it closely enough. Could you clarify what you want me to do?",
        "ar": "أعتقد أنني فهمت قصدك، لكن الإجراء المقترح لا يطابق طلبك بدقة كافية. هل يمكنك توضيح ما تريدني أن أفعله؟",
    }[lang]
