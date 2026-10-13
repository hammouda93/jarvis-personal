"""Conservative user-facing progress from tool receipts, not model claims."""
from __future__ import annotations

import json
from urllib.parse import urlsplit


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
    parts.append(
        f"Actions avec effet vérifié : {counts['verified']}. "
        f"Actions envoyées ou incertaines : {counts['pending']}. "
        f"Appels en échec : {counts['failed']}."
    )
    parts.append(
        "La progression est conservée. Il faut encore vérifier les conditions "
        "de réussite manquantes, sans rejouer les actions déjà envoyées."
    )
    return " ".join(parts)
