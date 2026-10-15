"""Conservative user-facing progress from tool receipts, not model claims."""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .mcp_result import source_matches_alias
from .mcp_read_policy import is_conservative_read_tool


def _data(action):
    try:
        data = json.loads(str(getattr(action, "detail", "") or "{}"))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def action_evidence_counts(actions):
    verified = pending = failures = 0
    for action in actions:
        data = _data(action)
        if not getattr(action, "success", False):
            failures += 1
        elif data.get("verified") is True:
            verified += 1
        elif data.get("outcome_unknown") is True or data.get("dispatched") is True:
            pending += 1
    return {"verified": verified, "pending": pending, "failed": failures}


def partial_mission_report(actions):
    """Honest bounded progress when an agent turn budget is exhausted."""
    actions = tuple(actions or ())
    counts = action_evidence_counts(actions)
    last_page = ""
    for action in reversed(actions):
        if not getattr(action, "success", False):
            continue
        name = str(getattr(action, "name", ""))
        data = _data(action)
        tab = None
        if name == "browser_get_active_tab" and data.get("status") == "complete":
            tab = data
        elif (name in {"browser_click", "browser_navigate"}
              and data.get("verified") is True):
            post = data.get("post_observation")
            tab = post.get("tab") if isinstance(post, dict) else None
        if isinstance(tab, dict):
            url = str(tab.get("url") or "")
            parsed = urlsplit(url)
            if parsed.scheme in {"http", "https"} and parsed.netloc and len(url) <= 1500:
                last_page = url
                break
    if not actions:
        return ("Mission en attente : aucune action vérifiable n'a encore été "
                "effectuée dans ce tour. Aucune action ne sera relancée automatiquement.")
    parts = ["Mission encore non certifiée."]
    if last_page:
        parts.append("Dernière page réellement observée : " + last_page + ".")
    # An acknowledged observational MCP read is useful *source evidence*.
    # It is neither a mutating action nor independent proof of the whole
    # objective. Expose only trusted local tool identities, never raw remote
    # content or account identifiers from an untrusted response body.
    observed = []
    for action in actions:
        name = str(getattr(action, "name", "") or "")
        payload = _data(action)
        origin = payload.get("source")
        if (name.startswith("mcp__") and getattr(action, "success", False)
                and payload.get("dispatched") is True
                and payload.get("outcome_unknown") is False
                and source_matches_alias(name, origin)
                and is_conservative_read_tool(origin["tool_name"])
                and name not in observed):
            observed.append(name)
    if observed:
        parts.append(
            "Lectures MCP reçues (contenu externe, pas preuve de mission) : "
            + ", ".join(observed[:5]) + "."
        )
    blockers = [
        _data(action) for action in actions
        if _data(action).get("guard") == "mcp_unknown_requires_review"
        and _data(action).get("blocked_by_previous_attempt") is True
        and _data(action).get("dispatched") is False
    ]
    if blockers:
        blocker = blockers[-1]
        server = str(blocker.get("server_id") or "")
        tool = str(blocker.get("tool_name") or "")
        if (re.fullmatch(r"[a-z][a-z0-9_]{0,35}", server)
                and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", tool)):
            label = "Lecture MCP bloquée" if is_conservative_read_tool(tool) else "Appel MCP bloqué"
            parts.append(label + " : " + server + "/" + tool + ".")
        attempt = blocker.get("previous_attempt_id")
        if type(attempt) is int and attempt > 0:
            parts.append(
                "Tentative précédente #" + str(attempt)
                + " à examiner dans MCP Control Center > Services > Journal."
            )
        else:
            parts.append(
                "Vérifier les tentatives incertaines dans "
                "MCP Control Center > Services > Journal."
            )
        parts.append(
            "Ce nouvel appel n'a pas été envoyé. Le verrou persistant "
            "nécessite la vérification explicite de l'opérateur."
        )
    elif any(_data(action).get("outcome_unknown") is True
             and str(getattr(action, "name", "")).startswith("mcp__")
             for action in actions):
        parts.append(
            "Un appel MCP a un résultat incertain. Examiner sa tentative "
            "dans MCP Control Center > Services > Journal avant de poursuivre."
        )
    budgets = [_data(action) for action in actions
               if _data(action).get("guard") == "mcp_budget_unavailable"]
    if budgets:
        budget = budgets[-1]
        label = ("Quota horaire MCP atteint" if budget.get("budget_reason") ==
                 "mcp_hourly_quota_exhausted" else "Service MCP temporairement indisponible")
        delay = budget.get("retry_after_seconds")
        parts.append(label + (f" ; nouvelle tentative possible dans {delay} s."
            if type(delay) is int and 0 < delay <= 3600 else "."))
    parts.append(
        f"Actions avec effet vérifié : {counts['verified']}. "
        f"Actions envoyées ou incertaines : {counts['pending']}. "
        f"Appels en échec : {counts['failed']}."
    )
    parts.append(
        "Le résultat global reste incomplet ; aucune action incertaine ne "
        "doit être rejouée automatiquement."
    )
    return " ".join(parts)
