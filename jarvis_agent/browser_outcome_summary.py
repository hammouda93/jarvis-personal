"""Evidence-only browser navigation summaries; no execution or goal certification."""
from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import urlsplit


def _normalized(text):
    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def verified_navigation_note(user_text, actions, model_reply=""):
    """Return the exact verified browser destination for an explicit status request.

    This is intentionally narrow: never infer a whole mission's completion
    from one successful click or from a model-written URL. Site/page title
    remains untrusted displayed data; it cannot issue any instruction.
    """
    instruction = _normalized(user_text)
    if not re.search(
        r"\b(?:verifie|verifier|verification|confirme|confirmer|"
        r"explique|expliquer|bilan|recapitulatif|report|verify|check)\b",
        instruction,
    ):
        return ""
    for action in reversed(tuple(actions)):
        if not bool(getattr(action, "success", False)):
            continue
        if getattr(action, "name", "") not in {
            "browser_click", "browser_navigate", "browser_press",
            "browser_back", "browser_forward",
        }:
            continue
        try:
            detail = json.loads(getattr(action, "detail", "") or "{}")
        except (TypeError, ValueError):
            continue
        if (not isinstance(detail, dict)
                or detail.get("verified") is not True
                or detail.get("postcondition") != "navigation_observed"):
            continue
        observation = detail.get("post_observation")
        tab = observation.get("tab") if isinstance(observation, dict) else None
        if not isinstance(tab, dict):
            continue
        exact_url = str(tab.get("url") or "").strip()
        split = urlsplit(exact_url)
        if (split.scheme not in {"http", "https"} or not split.netloc
                or any(c in exact_url for c in "\r\n\t")
                or len(exact_url) > 1600):
            continue
        if exact_url in str(model_reply or ""):
            return ""  # Already reproduced the source URL precisely.
        title = str(tab.get("title") or "").replace("\r", " ").replace("\n", " ").strip()
        title = title[:120]
        target = f"« {title} » — " if title else ""
        return f"Navigation vérifiée : {target}{exact_url}"
    return ""
