from __future__ import annotations

import re


def is_explicit_memory_write_request(text: str) -> bool:
    """Return True only for an explicit request to persist personal memory.

    This is an admission guard, not an intent classifier. Semantic models may
    still infer richer write content, but they cannot authorize durable state.
    """
    normalized = (text or "").lower().replace("’", "'").strip()
    patterns = (
        r"\b(retiens|retenez|mémorise|memorise|mémorisez|memorisez)\b",
        r"\b(garde|gardez|conserve|conservez)\b.{0,32}\ben mémoire\b",
        r"\b(souviens-toi|souvenez-vous)\b",
        r"\b(remember|memorize|memorise)\b",
        r"\b(save|keep)\b.{0,24}\b(in )?(memory|mind)\b",
        r"\b(save|keep)\b.{0,48}\b(?:this|that|detail|information|fact)\b"
        r".{0,40}\b(?:for later|for another day|for the future)\b",
        r"\b(?:garde|gardez|conserve|conservez)\b.{0,32}"
        r"\b(?:ça|ca|cela|ceci|cette information|ce détail|ce detail|ce fait)\b"
        r".{0,32}\b(?:pour plus tard|pour l'avenir|pour une autre fois)\b",
    )
    return any(
        re.search(pattern, normalized, flags=re.DOTALL)
        for pattern in patterns
    )


def is_explicit_context_reset_request(text: str) -> bool:
    """Return True only when the user explicitly asks to reset chat context.

    Closing a tab/window/application, correcting a target, or changing topic is
    deliberately not enough. This guard protects transient conversation state
    from model tool-selection mistakes.
    """
    normalized = (
        (text or "")
        .lower()
        .replace("’", "'")
        .replace("‑", "-")
        .strip()
    )
    patterns = (
        r"\b(?:oublie|oubliez|efface|effacez|supprime|supprimez)\b"
        r".{0,48}\b(?:contexte|conversation|discussion)\b",
        r"\b(?:repars|repart|repartez|recommence|recommencez)\b"
        r".{0,24}\b(?:de zéro|de zero|à zéro|a zero)\b",
        r"\b(?:nouvelle|nouveau)\b.{0,16}\b(?:conversation|discussion)\b",
        r"\b(?:reset|réinitialise|reinitialise|réinitialisez|reinitialisez)\b"
        r".{0,32}\b(?:contexte|conversation|discussion)\b",
        r"\b(?:forget|clear|reset)\b.{0,32}\b(?:context|conversation|chat)\b",
        r"\b(?:start|begin)\b.{0,24}\b(?:new|fresh)\b"
        r".{0,16}\b(?:conversation|chat)\b",
    )
    return any(
        re.search(pattern, normalized, flags=re.DOTALL)
        for pattern in patterns
    )
