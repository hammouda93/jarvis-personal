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

    if intent.name == "browser.search" and result.success:
        query = str(intent.args.get("query", "")).strip()
        if lang == "en":
            return f"I'm searching for {query}."
        if lang == "ar":
            return f"سأبحث عن {query}."
        return f"Je recherche {query}."

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
            name = str(intent.args.get("query", "")).strip()
            return {
                "fr": f"J'ai ouvert le dossier {name}.",
                "en": f"I opened the {name} folder.",
                "ar": f"فتحت مجلد {name}.",
            }[lang]
        return {
            "fr": "C'est fait.",
            "en": "Done.",
            "ar": "تم.",
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
