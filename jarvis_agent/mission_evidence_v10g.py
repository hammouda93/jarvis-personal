"""Generic response/evidence contract for multi-step desktop missions.

This module does not execute tools, grant permissions, classify specific apps,
or infer success from the assistant's claims. It only prevents a false report
when the current turn lacks the appropriate verified effect.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


_OBSERVATION_TOOLS = frozenset({
    "inspect_active_window", "inspect_interface", "list_windows",
    "computer_observe", "computer_verify", "computer_list_windows",
    "computer_get_active_window", "browser_observe_dom", "browser_verify",
    "browser_get_active_tab", "browser_list_tabs", "observe_screen",
    "verify_file_artifact",
})
_UI_EFFECT_TOOLS = frozenset({
    "open_url", "open_application", "close_window", "close_tab",
    "click_ui_element", "click_visual_target", "write_ui_element",
    "write_visual_target", "type_text_active_window", "press_key",
    "browser_navigate", "browser_activate_tab", "browser_click",
    "browser_write", "browser_press", "browser_select", "browser_back",
    "browser_forward", "browser_close_tab", "browser_download",
    "computer_click", "computer_write", "computer_press",
    "computer_shortcut",
})
_INTERNAL_TOOLS = frozenset({
    "mission_checkpoint", "request_tool_capabilities",
    "read_observation_evidence",
})
# A click/input succeeded != the end goal was verified.
_TERMINAL_GOAL = re.compile(
    r"\b(?:install(?:e|er|ation)|configur(?:e|er|ation)|"
    r"deploy(?:er|e|ment)|deploiement|telecharg(?:e|er|ement)|"
    r"setup|install(?:ed|ation)?|configure|deploy|download)\b"
)
_TERMINAL_CLAIM = re.compile(
    r"\b(?:termin(?:e|ee|er)|fini|finalis(?:e|ee)|complete|completed|"
    r"installe|installed|done|acheve|achevee)\b|\bc.est fait\b"
)
_IMMEDIATE_ACTION_CLAIM = re.compile(
    r"\b(?:je|j)[\s']*(?:vais\s+)?(?:clique|click|ouvre|open|lance|launch|"
    r"ferme|close|installe|install|appuie|ecris|saisis|envoie|send)\b",
)


def _normalized(value: str) -> str:
    chars = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(c for c in chars if not unicodedata.combining(c)).replace("’", "'")


def _effect_result(action: Any) -> bool:
    return (
        getattr(action, "success", False) is True
        and str(getattr(action, "name", "")) in _UI_EFFECT_TOOLS
    )


def _terminal_proof(actions: list[Any] | tuple[Any, ...]) -> bool:
    """An explicit trusted tool attestation, not a successful Next click.

    No existing click primitive is allowed to manufacture this proof. A
    future verifier may add goal_verified + postcondition when it actually
    checks the requested terminal state.
    """
    for action in actions:
        if not getattr(action, "success", False):
            continue
        try:
            proof = json.loads(str(getattr(action, "detail", "") or "{}"))
        except (ValueError, TypeError):
            continue
        if (str(getattr(action, "name", "")) in _OBSERVATION_TOOLS
                and isinstance(proof, dict)
                and proof.get("goal_verified") is True
                and proof.get("verified") is True
                and isinstance(proof.get("postcondition"), str)
                and proof.get("postcondition")
                not in ("", "navigation_pending", "click_dispatched",
                        "navigation_observed", "element_value")):
            return True
    return False


def guard_unverified_mission_reply(
    user_text: str,
    reply: str,
    actions: list[Any] | tuple[Any, ...],
    *,
    continuation: bool = False,
) -> tuple[str, str]:
    """Return a conservative response and reason, or the original text.

    A missing proof never means an operation failed. It means Jarvis cannot
    *claim* that it succeeded. Genuine evidence from the environment stays
    authoritative, and no click is retried as part of this guard.
    """
    user = _normalized(user_text)
    answer = _normalized(reply)
    if _IMMEDIATE_ACTION_CLAIM.search(answer) and not any(
            _effect_result(action) for action in actions):
        return (
            "Je n'ai pas exécuté cette action dans le tour actuel. "
            "La mission reste à poursuivre à partir d'une observation réelle.",
            "action_claim_without_dispatch",
        )

    if (continuation and not any(_effect_result(a) for a in actions)
            and re.search(r"\b(?:fait|execute|termine|lance|ouvert|clique|reussi)\b", answer)):
        return (
            "Je n'ai pas de preuve d'une nouvelle action dans ce tour. "
            "La mission précédente reste en attente.",
            "continuation_without_effect",
        )

    # A spoken assertion of verification is not an observation. In a mixed
    # task, the memory search cannot independently verify a GUI/window state.
    sections = re.split(r"\b(?:puis|ensuite|then)\b|[;,]", user)
    external_check = any(
        re.search(r"\b(?:verifie|verification|controle|inspecte|check|verify)\b", part)
        and not re.search(r"\b(?:memoire|souvenir|memorise|memory)\b", part)
        for part in sections
    )
    claims_check = bool(re.search(
        r"\b(?:verifie|verification|confirme|constate|bien ouverte|"
        r"bien ouvert|est ouverte|est ouvert|verified|confirmed)\b",
        answer,
    ))
    if (external_check and claims_check
            and not any(getattr(a, "success", False)
                        and str(getattr(a, "name", "")) in _OBSERVATION_TOOLS
                        for a in actions)):
        return (
            "Je n'ai pas effectué de nouvelle observation de l'interface "
            "dans ce tour ; son état ne peut pas encore être confirmé.",
            "external_verification_without_observation",
        )

    if (_TERMINAL_GOAL.search(user) and _TERMINAL_CLAIM.search(answer)
            and not _terminal_proof(actions)):
        return (
            "Des étapes ont pu être exécutées, mais l'objectif final "
            "n'a pas encore été vérifié. Je ne peux pas confirmer sa réussite.",
            "terminal_goal_without_proof",
        )
    return reply, ""
