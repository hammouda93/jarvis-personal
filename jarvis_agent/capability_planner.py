"""Proof-aware, read-only capability proposals for existing Jarvis missions.

Unlike an autonomous executor, this planner NEVER calls a model, executes a
tool, starts a service or infers success from tool metadata. It proposes
candidate specialist agents and *already allowed* MCP tools for the semantic
steps actually registered with a mission. The normal Jarvis runtime remains
the sole authoritative action executor.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from .capability_registry import DEFAULT_CAPABILITY_REGISTRY

# Generic grammar particles only; no application/brand-specific routing.
_STOPWORDS = frozenset({
    "a", "à", "au", "aux", "avec", "and", "by", "dans", "de", "des",
    "du", "en", "et", "for", "from", "il", "in", "is", "la", "le",
    "les", "l", "on", "or", "par", "pour", "que", "qui", "the",
    "to", "un", "une", "via", "with",
})


def _terms(raw: object) -> frozenset[str]:
    text = unicodedata.normalize("NFKD", str(raw or "")[:600]).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return frozenset(
        s for s in re.findall(r"[^\W_]{3,}", text, flags=re.UNICODE)
        if s not in _STOPWORDS
    )


def _candidates(
    *,
    allowed_mcp: list[dict[str, Any]],
    include_native: bool,
) -> list[dict[str, Any]]:
    options = []
    for item in allowed_mcp[:200]:
        server = str(item.get("server") or "")[:36]
        tool = str(item.get("tool") or "")[:100]
        if not server or not tool:
            continue
        label = "mcp__" + server + "__" + re.sub(r"[^A-Za-z0-9_]", "_", tool)
        description = str(item.get("description") or "")[:400]
        options.append({
            "provider": "mcp", "agent": "connector:" + server,
            "capability": label,
            "description": description,
            "terms": _terms(" ".join((server, tool, description))),
            "authorization": "confirmation_utilisateur",
            "availability": "configuré_et_autorisé_non_testé",
        })
    if include_native:
        for record in DEFAULT_CAPABILITY_REGISTRY.capabilities()[:80]:
            agent = str(record.provider_agent_id)[:60]
            spec = record.spec
            name = str(spec.name)[:100]
            description = str(spec.description)[:400]
            options.append({
                "provider": "native_declared", "agent": agent,
                "capability": name,
                "description": description,
                "terms": _terms(" ".join((name, description, agent))),
                "authorization": "politique_native_existante",
                "availability": "déclaré_pas_une_preuve_d_execution",
            })
    return options


def propose_capabilities(
    snapshot: Mapping[str, Any], *,
    allowed_mcp: list[dict[str, Any]] | None = None,
    include_native: bool = True,
    max_candidates: int = 4,
) -> dict[str, Any]:
    """Shadow-only route proposals from existing proof/dependency checkpoints.

    Ranked overlap is a heuristic for a *human/LLM to inspect*, not a semantic
    guarantee. Unknown steps or no matching eligible tools stay unresolved.
    The model is never prevented from exploring unrelated native capabilities.
    """
    limit = max(1, min(5, int(max_candidates)))
    review = snapshot.get("supervisor") or {}
    if not isinstance(review, dict):
        review = {}
    blocked = (
        snapshot.get("manual_review_required") is True
        or str(review.get("state") or "") == "manual_review"
        or str(snapshot.get("status") or "") == "blocked"
    )
    options = _candidates(
        allowed_mcp=list(allowed_mcp or []),
        include_native=include_native,
    )
    prepared = []
    for entry in list(review.get("steps") or [])[:24]:
        if not isinstance(entry, dict):
            continue
        step_state = str(entry.get("state") or "")[:40]
        intent = str(entry.get("intent") or "")[:180]
        needed = tuple(str(s)[:120] for s in (entry.get("waiting_for") or [])[:16])
        dependencies = tuple(
            str(s)[:80] for s in (entry.get("unmet_dependencies") or [])[:16]
        )
        if blocked:
            dispatch_status = "blocked_requires_independent_review"
        elif step_state == "verified":
            dispatch_status = "step_already_verified_no_action"
        elif dependencies:
            dispatch_status = "waiting_dependencies"
        elif step_state in ("evidence_spec_needed", "invalid_dependencies"):
            dispatch_status = "needs_plan_or_proof_definition"
        else:
            dispatch_status = "candidate_review_only"

        chosen = []
        if dispatch_status == "candidate_review_only":
            intent_terms = _terms(intent)
            goal_terms = _terms(str(snapshot.get("user_goal") or "")[:180])
            target = intent_terms | goal_terms
            scored = []
            for item in options:
                match = target & item["terms"]
                if not match:
                    continue
                # Deterministic, dynamic wording overlap, not app hardcoding.
                score = round((3 * len(intent_terms & match) + len(goal_terms & match)) / max(1, 3 * len(intent_terms) + len(goal_terms)), 3)
                scored.append((score, item))
            scored.sort(
                key=lambda pair: (
                    -pair[0],
                    pair[1]["provider"] != "mcp",
                    pair[1]["agent"],
                    pair[1]["capability"],
                )
            )
            for score, item in scored[:limit]:
                chosen.append({
                    "agent": item["agent"],
                    "capability": item["capability"],
                    "provider": item["provider"],
                    "match_score": score,
                    "matching_words": sorted(
                        target & item["terms"]
                    )[:7],
                    "requires": item["authorization"],
                    "availability": item["availability"],
                    "approved_to_execute": False,
                    "reason": "candidate_from_descriptions_only",
                })
        prepared.append({
            "step_id": str(entry.get("id") or "")[:80],
            "intent": intent,
            "state": step_state,
            "routing_status": (
                "no_matching_capability" if
                dispatch_status == "candidate_review_only" and not chosen
                else dispatch_status
            ),
            "missing_evidence": list(needed),
            "unmet_dependencies": list(dependencies),
            "candidates": chosen,
        })
    return {
        "mission_id": str(snapshot.get("mission_id") or "")[:70],
        "status": (
            "manual_review" if blocked else
            "plan_not_registered" if not review.get("plan_registered") else
            "shadow_routes_only"
        ),
        "plan_registered": review.get("plan_registered") is True,
        "authoritative": False,
        "will_execute": False,
        "executor": "existing_jarvis_runtime_only",
        "allowed_mcp_candidates_seen": min(200, len(allowed_mcp or [])),
        "steps": prepared,
        "note": (
            "Propositions seulement : aucun agent délégué, aucune nouvelle "
            "requête au modèle, aucune action envoyée, aucune preuve fabriquée."
        ),
    }
