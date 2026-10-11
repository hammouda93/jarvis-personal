"""General conversational mission recovery rules; no app- or site-specific code.

This module consumes only the original user's request and trusted tool
receipts. It neither invents a mission plan nor dispatches external actions.
All safety guards, approvals and tool boundaries remain authoritative.
"""
from __future__ import annotations

import json
import re
import unicodedata

from .instruction_clauses import positive_instruction_clauses


def _plain(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "").lower())
    return "".join(c for c in raw if not unicodedata.combining(c))


def recoverable_discovery(actions) -> dict | None:
    """A failed tool may still discover valid names; failure != no capability."""
    if not actions:
        return None
    last = actions[-1]
    if bool(getattr(last, "success", False)):
        return None
    try:
        candidates = json.loads(str(getattr(last, "detail", "") or ""))
    except (ValueError, TypeError):
        return None
    if not isinstance(candidates, list):
        return None
    grounded = [dict(item) for item in candidates
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"].strip()]
    if not grounded:
        return None
    # Do not choose a candidate from a title or score; let the reasoning model
    # compare the actual user intent with observed names. Ambiguity => ask.
    return {
        "source_tool": str(getattr(last, "name", "")),
        "candidates": grounded[:5],
        "next_step": "Compare observed candidate names with the user's original goal. "
                     "If a single candidate unambiguously matches, use the exact "
                     "observed name with the already available generic tool, "
                     "without forcing a new instance. If ambiguous, ask a short "
                     "clarification. Discovery is not execution or verification.",
    }


_MEMORY_READ_TOOLS = frozenset({
    "semantic_memory_search", "semantic_memory_inspect",
    "recall_information",
})


def explicit_memory_read_not_attempted(user_text: str, actions) -> bool:
    """Explicit source-retrieval request needs actual source evidence.

    This concerns a generic data source (persistent memory), never specific
    stored values, user names, slots or entities. A failed read counts as
    attempted; the agent can then report unavailable evidence honestly.
    """
    request = _plain(user_text)
    if not re.search(r"\b(?:memoire|souvenir|souvenirs|memory)\b", request):
        return False
    if not re.search(
        r"\b(?:retrouve|retrouver|recherche|rechercher|cherche|chercher|"
        r"consulte|consulter|lis|lire|affiche|afficher|montre|montrer|"
        r"recupere|recuperer|recall|read|find|search|inspect)\b",
        request,
    ):
        return False
    return not any(
        getattr(action, "name", "") in _MEMORY_READ_TOOLS
        for action in actions
    )


_MUTATION_TOOLS = frozenset({
    "open_application", "open_file", "open_url", "close_window", "close_tab",
    "click_ui_element", "write_ui_element", "press_key",
    "browser_navigate", "browser_click", "browser_press", "browser_write",
    "browser_select", "browser_close_tab", "browser_back",
    "computer_click", "computer_press", "computer_write", "computer_shortcut",
})
_OBSERVATION_TOOLS = frozenset({
    "list_windows", "inspect_active_window", "inspect_interface",
    "observe_screen", "browser_observe_dom", "browser_list_tabs",
    "browser_verify", "computer_observe", "computer_verify",
})


def _last_step_requires_observation(user_text: str) -> bool:
    """Only an explicit final verification clause activates this guard."""
    # Use a provisional outline for *intent*, not proof or authorization.
    from .mission_trace_v10h import requested_steps
    clauses = requested_steps(user_text)
    if len(clauses) < 2:
        return False
    last = _plain(clauses[-1]).strip(" '.,;")
    return bool(re.match(
        r"^(?:verifie|verifier|controle|controler|inspecte|inspecter|"
        r"check|verify|confirm|confirme|confirmer)\b",
        last,
    ))


def post_mutation_observation_missing(user_text: str, actions) -> bool:
    """True only if user requested a *last* verification after a mutation.

    An earlier observation is not evidence about a subsequent change.
    A trustworthy structured post-observation is enough except when the final
    request explicitly asks for the updated list of windows.
    """
    if not _last_step_requires_observation(user_text):
        return False
    previous = [(i, a) for i, a in enumerate(actions)
                if getattr(a, "success", False)
                and getattr(a, "name", "") in _MUTATION_TOOLS]
    if not previous:
        return False
    last_index, last_action = previous[-1]
    later = actions[last_index + 1:]
    if any(getattr(a, "success", False)
           and getattr(a, "name", "") in _OBSERVATION_TOOLS
           for a in later):
        return False
    # When the request names a window inventory, only a fresh window read
    # can answer whether the window disappeared from that inventory.
    if re.search(r"\b(?:fenetres|windows|window list|liste des fenetres)\b",
                 _plain(user_text)):
        return True
    try:
        detail = json.loads(str(getattr(last_action, "detail", "") or "{}"))
    except (ValueError, TypeError):
        detail = {}
    if isinstance(detail, dict):
        post = detail.get("post_observation")
        if (detail.get("verified") is True and isinstance(post, dict)):
            return False
        # The Windows UI adapter takes an actual fresh UIA snapshot after a
        # click. It is a real post-action observation, even though the wrapper
        # does not set an overall "verified" flag. This read avoids paying for
        # another round solely to collect the same UI again; it does not imply
        # that the whole requested goal is completed.
        if (getattr(last_action, "name", "") == "click_ui_element"
                and detail.get("auto_reinspection") is True
                and detail.get("requires_fresh_inspection") is False
                and isinstance(post, dict)
                and isinstance(post.get("window"), dict)
                and isinstance(post.get("controls"), list)
                and post.get("observation_id")
                and post.get("observation_id") != detail.get("source_observation_id")):
            return False
    return True


def preterminal_continuation_pending(user_text: str, actions) -> bool:
    """Advisory only: a multi-step mission stopped before its requested boundary.

    Never dispatch a UI action based on this classification. A visible generic
    forward affordance is *not* permission to activate it; the agent must
    reconsider the original user instructions, the screen, approvals and stop
    conditions. Conservatively refuse to prompt continuation if any final or
    irreversible action label is visible.
    """
    request = _plain(user_text)
    if not re.search(r"\b(?:arrete|arreter|stop|pause|suspend)\b.{0,100}\b(?:avant|before)\b", request):
        return False
    if not re.search(r"\b(?:continue|continuer|poursuis|poursuivre|parcours|parcourir|avance|avancer)\b", request):
        return False
    if not actions or not getattr(actions[-1], "success", False):
        return False
    last = actions[-1]
    if getattr(last, "name", "") not in {"inspect_active_window", "click_ui_element"}:
        return False
    try:
        detail = json.loads(str(getattr(last, "detail", "") or "{}"))
    except (ValueError, TypeError):
        return False
    if not isinstance(detail, dict):
        return False
    observation = (
        detail.get("post_observation")
        if getattr(last, "name", "") == "click_ui_element"
        else detail
    )
    if not isinstance(observation, dict) or not observation.get("observation_id"):
        return False
    controls = observation.get("controls")
    if not isinstance(controls, list):
        return False
    enabled_buttons = {
        _plain(control.get("name"))
        for control in controls
        if isinstance(control, dict)
        and control.get("type") in {"Button", "button"}
        and control.get("enabled") is True
    }
    # A final-action button in the same UI means the requested boundary may
    # already be reached. No continuation should be encouraged.
    terminal = re.compile(
        r"\b(?:installer|install|installieren|finish|terminer|supprimer|delete|"
        r"payer|pay|envoyer|send|valider|confirm|confirmer|appliquer|apply)\b"
    )
    if any(terminal.search(label) for label in enabled_buttons):
        return False
    # Generic navigation labels across desktop setup/wizard workflows.
    # Their presence leads only to one reasoning checkpoint, never a click.
    forward = re.compile(
        r"^(?:suivant|next|weiter|continue|continuer|poursuivre|avance|avancer)\b"
    )
    return any(forward.search(label) for label in enabled_buttons)
