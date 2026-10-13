"""Bounded model-less recovery helpers for already-authorized observations.

Never grant access, invent a repository owner, or dispatch a tool. The LLM
still chooses the next action from the executor-approved schema catalogue.
"""
from __future__ import annotations
import re


_PERMISSION_CLAIM = re.compile(
    r"\b(?:autorisation|authoriz(?:ation|e)|permission|"
    r"acc[eè]s|access|confirmer|confirm|propri[eé]taire|owner)\b",
    re.IGNORECASE,
)
_READ_GOAL = re.compile(
    r"\b(?:consulte|consult|inspecte|inspect|analyse|analy[sz]e|"
    r"recherche|search|trouve|find|liste|list|r[eé]cup[eè]re|fetch|read|lis)\b",
    re.IGNORECASE,
)
_COMMAND = re.compile(
    r"\b(?:supprime|delete|remove|merge|fusionne|push|publie|publish|"
    r"installe|install|envoie|send|paie|pay|transf[eè]re|transfer|"
    r"autorise|approve|revoke|r[eé]voque|change|modify|modifie|"
    r"stop|arr[eê]te|annule|cancel)\b", re.IGNORECASE,
)


def needs_authorized_read_retry(user_goal: str, model_reply: str, definitions: list,
                                requires_confirmation) -> bool:
    """Only for a no-tools refusal with actual exposed and ungated MCP reads."""
    if not _READ_GOAL.search(str(user_goal or "")):
        return False
    if not _PERMISSION_CLAIM.search(str(model_reply or "")[:1800]):
        return False
    for definition in definitions or ():
        fn = definition.get("function", {}) if isinstance(definition, dict) else {}
        name = str(fn.get("name") or "")
        if name.startswith("mcp__") and not requires_confirmation(name):
            return True
    return False


def is_neutral_identifier_clarification(text: str, *, status: str,
                                         pending_confirmation=None,
                                         manual_review=False) -> bool:
    """A short supplied *data value*, never a permission or new instruction.

    When the model's turn-classifier emits malformed JSON, these facts may
    safely be appended as a clarification to the existing goal. They do NOT
    authorize an effect or resolve a guarded security precondition.
    """
    if str(status).lower() not in {"waiting_user", "waiting_external", "paused"}:
        return False
    if pending_confirmation or manual_review:
        return False
    value = str(text or "").strip()
    if not 4 <= len(value) <= 160 or "\n" in value or ":" in value:
        return False
    if _COMMAND.search(value):
        return False
    # Never fold credential-looking material into durable mission objectives.
    if re.search(r"(?i)(?:gh[pousr]_|github_pat_|sk-[a-z0-9]|AIza|"
                 r"token|secret|password|credential|api[_-]?key|bearer)", value):
        return False
    words = value.split()
    if not 1 <= len(words) <= 4:
        return False
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,70}", word)
               for word in words):
        return False
    # An answer with at least one identifier-shaped item (digits, underscore,
    # dot or hyphen) is safer than an unqualified imperative/plain phrase.
    return any(re.search(r"[0-9_.-]", word) for word in words)
