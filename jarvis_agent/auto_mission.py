"""Automatic mission admission for the ONE existing Jarvis runtime.

This module is deliberately advisory. A plan is never a permission, a tool
receipt is never proof of the objective, and an uncertain external effect
must never be replayed by automatic admission.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Admission:
    track: bool
    plan: bool
    reason: str


def _plain(text: str) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in raw if not unicodedata.combining(char))


def decide(text: str) -> Admission:
    """Conservative, generic admission; never routes by website/app name."""
    value = _plain(text).strip()
    if not value or len(value) > 2000:
        return Admission(False, False, "out_of_scope")
    if re.fullmatch(r"(?:bonjour|salut|hello|merci|ok|oui|non|d'accord)[.!? ]*", value):
        return Admission(False, False, "conversation")
    numbered = len(re.findall(r"(?m)^\s*\d+[.)]\s+\S", value)) >= 2
    sequence = bool(re.search(
        r"\b(?:puis|ensuite|apres|enfin|then|afterwards|finally)\b", value))
    research = bool(re.search(
        r"\b(?:recherche approfondie|etude approfondie|analyse comparative|"
        r"compare les sources|recoupe les sources|deep research|"
        r"research thoroughly|business plan|etude de marche)\b", value))
    action_words = re.findall(
        r"\b(?:cherche|recherche|retrouve|ouvre|inspecte|analyse|compare|"
        r"verifie|prepare|redige|cree|fais|fait|effectue|lance|organise|"
        r"telecharge|enregistre|etudie|search|find|open|inspect|create|write|"
        r"check|research|analyze|compare|prepare|download)\b", value)
    compound = len(action_words) >= 2 and bool(
        re.search(r"[,;]|\b(?:et|and)\b", value))
    if research:
        return Admission(True, True, "research_goal")
    if action_words and (numbered or sequence or compound):
        return Admission(True, True, "multi_step_goal")
    if numbered:
        return Admission(True, True, "numbered_goal")
    return Admission(False, False, "ordinary_turn")


def prepare(agent: Any, user_text: str, *, log: Callable[[str], None] | None = None) -> Admission:
    """Attach complex work automatically; the existing agent remains executor.

    Planning consumes at most one extra model request. It cannot execute tools,
    grant consent, approve a plan, register a proof or mark a mission complete.
    A planning failure leaves the original user request available for the
    normal agent turn; the error class is logged without secret-bearing bodies.
    """
    decision = decide(user_text)
    if not decision.track:
        return decision
    if getattr(agent, "active_mission_id", None):
        return Admission(False, False, "existing_mission")
    start = getattr(agent, "begin_mission", None)
    if not callable(start):
        if log:
            log("[MISSION_AUTO] state=UNAVAILABLE reason=runtime_convergence_missing")
        return Admission(False, False, "runtime_unavailable")
    try:
        mission_id = start(user_text)
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        if log:
            log("[MISSION_AUTO] state=NOT_STARTED error_type=" + type(exc).__name__)
        return Admission(False, False, "start_failed")
    if log:
        log(f"[MISSION_AUTO] state=TRACKING mission_id={mission_id} reason={decision.reason}")
    generate = getattr(agent, "generate_semantic_plan", None)
    allow_plan = os.getenv("JARVIS_AUTO_MISSION_PLANNING", "1").strip().lower() not in {
        "0", "false", "off", "no"}
    if decision.plan and allow_plan and callable(generate):
        try:
            result = generate()
            if log:
                log("[MISSION_AUTO] state=PLAN_DRAFTED steps="
                    + str(result.get("step_count", 0))
                    + " unresolved=" + str(result.get("unresolved_count", 0))
                    + " approval=not_granted proof=not_established")
        except Exception as exc:
            if log:
                log("[MISSION_AUTO] state=PLAN_UNAVAILABLE error_type="
                    + type(exc).__name__ + " fallback=original_goal")
    return decision
