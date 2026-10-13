"""Evidence-bound, read-only proof for an explicitly requested browser navigation.

This is one generic goal-verifier profile, not a website-specific workflow.
No tool dispatch, permissions, LLM statements or invented goal fulfillment.
Other mission types remain uncertified pending their own evidence contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import unicodedata
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BrowserGoalProof:
    target: str
    url: str
    tab_id: int
    proof_ref: str


def _plain(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def _receipt(action):
    try:
        data = json.loads(str(getattr(action, "detail", "") or "{}"))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _http(url):
    value = str(url or "")
    parsed = urlsplit(value)
    return (parsed.scheme in {"http", "https"} and bool(parsed.netloc)
            and len(value) < 1800 and not any(c in value for c in "\r\n\t"))


def _navigation_intent(goal):
    raw = str(goal or "")
    urls = re.findall(r"https?://[^\s,;<>]+", raw)
    if len(urls) != 1:
        return None
    initial = urls[0].rstrip(".,;")
    if not _http(initial):
        return None
    text = _plain(raw.replace(urls[0], " url "))
    # No other externally consequential objective may be silently ignored.
    forbidden = (
        r"\b(?:envoi\w*|send|supprim\w*|delet\w*|effac\w*|"
        r"ecri\w*|write|saisi\w*|install\w*|telecharg\w*|"
        r"download|pay\w*|paie\w*|achet\w*|buy|modifi\w*|"
        r"chang\w*|cre\w*|publ\w*|post\w*|sauv\w*|"
        r"enregistr\w*|reviens|retourn\w*|back|ferm\w*|close)\b"
    )
    if re.search(forbidden, text):
        return None
    if not re.search(r"\b(?:ouvre|ouvrir|open)\s+url\b", text):
        return None
    if not re.search(r"\b(?:verifie|verifier|verify|check|confirme)\b", text):
        return None
    match = re.search(
        r"\b(?:clique|cliquer|click)\s+(?:sur\s+|on\s+)?"
        r"(?:le\s+lien\s+|the\s+link\s+)?([^.;,]+)", text
    )
    if not match:
        return None
    target = re.split(r"\s+(?:si\s+|if\s+|et\s+verifie|and\s+verify)",
                      match.group(1), maxsplit=1)[0].strip(" :!'\"")
    if not 2 <= len(target) <= 80:
        return None
    return initial, target, ("menu" in text)


def verify_browser_navigation_goal(goal, actions):
    """Certify only a complete, same-tab, independently verified link journey."""
    intent = _navigation_intent(goal)
    if intent is None:
        return None
    initial, target, menu_requested = intent
    actions = tuple(actions or ())
    if len(actions) > 32 or not actions:
        return None
    allowed = {"open_url", "browser_get_active_tab", "browser_observe_dom",
               "browser_find", "browser_click", "browser_verify"}
    if any(not getattr(a, "success", False) or
           getattr(a, "name", "") not in allowed for a in actions):
        return None
    starts = [(i, _receipt(a)) for i, a in enumerate(actions)
              if a.name == "open_url"]
    if len(starts) != 1:
        return None
    start_at, start = starts[0]
    scope = start.get("scope")
    if (start.get("dispatched") is not True
            or not isinstance(scope, list) or len(scope) != 2
            or scope[0] != "browser" or type(scope[1]) is not int):
        return None
    tab_id = scope[1]
    # The first get_active_tab may be 'loading' with a temporarily
    # empty URL. A later independent DOM observation can confirm the
    # requested origin once real controls become available, without
    # requiring a second model round just to re-read the tab status.
    initial_active = any(
        i > start_at and a.name == "browser_get_active_tab"
        and _receipt(a).get("tab_id") == tab_id
        and _receipt(a).get("url") == initial
        and _receipt(a).get("status") == "complete"
        for i, a in enumerate(actions)
    )
    observed_dom = any(
        a.name == "browser_observe_dom"
        and (_receipt(a).get("tab") or {}).get("tab_id") == tab_id
        for a in actions
    )
    grounded_initial_dom = any(
        a.name == "browser_observe_dom"
        and _receipt(a).get("sensor") == "dom"
        and bool(_receipt(a).get("observation_id"))
        and (_receipt(a).get("tab") or {}).get("tab_id") == tab_id
        and (_receipt(a).get("tab") or {}).get("url") == initial
        and isinstance(_receipt(a).get("controls"), list)
        and bool(_receipt(a).get("controls"))
        for a in actions
    )
    if not observed_dom or not (initial_active or grounded_initial_dom):
        return None
    menus = set()
    links = []
    for i, action in enumerate(actions):
        if action.name != "browser_find":
            continue
        found = _receipt(action)
        if found.get("scope") != ["browser", tab_id]:
            continue
        for item in found.get("available_controls") or []:
            if (isinstance(item, dict) and item.get("type") == "button"
                    and "menu" in _plain(item.get("name"))
                    and isinstance(item.get("ref"), str)):
                menus.add((i, item["ref"]))
        matches = found.get("matches")
        if not isinstance(matches, list) or len(matches) != 1 or found.get("unique") is not True:
            continue
        item = matches[0]
        if (isinstance(item, dict) and item.get("type") == "link"
                and _plain(item.get("name")).strip() == target
                and isinstance(item.get("ref"), str)
                and _http(item.get("href"))):
            links.append((i, item))
    for link_at, link in links:
        if menu_requested and menus and not any(
            candidate_at < j < link_at
            and a.name == "browser_click"
            and _receipt(a).get("source_ref") == candidate_ref
            and _receipt(a).get("dispatched") is True
            for candidate_at, candidate_ref in menus
            for j, a in enumerate(actions)
        ):
            continue
        for click_at, a in enumerate(actions):
            if click_at <= link_at or a.name != "browser_click":
                continue
            click = _receipt(a)
            observation = click.get("post_observation")
            tab = observation.get("tab") if isinstance(observation, dict) else None
            if (click.get("source_ref") != link["ref"]
                    or click.get("verified") is not True
                    or click.get("postcondition") != "navigation_observed"
                    or not isinstance(tab, dict)
                    or tab.get("tab_id") != tab_id
                    or tab.get("url") != link["href"]
                    or tab.get("status") != "complete"):
                continue
            # A separate read-only bridge query may already be attached
            # to this verified click by the executor. Otherwise require
            # a later explicit browser_get_active_tab action. Never reuse
            # the click's post_observation as its own independent witness.
            readback = click.get("independent_readback")
            embedded_readback = (
                isinstance(readback, dict)
                and type(readback.get("tab_id")) is int
                and readback["tab_id"] == tab_id
                and readback.get("url") == link["href"]
                and readback.get("status") == "complete"
            )
            later_readback = any(
                read.name == "browser_get_active_tab"
                and _receipt(read).get("tab_id") == tab_id
                and _receipt(read).get("url") == link["href"]
                and _receipt(read).get("status") == "complete"
                for read in actions[click_at + 1:]
            )
            if not (embedded_readback or later_readback):
                continue
            witness = (initial + "\0" + link["ref"] + "\0"
                       + link["href"] + "\0" + str(tab_id))
            return BrowserGoalProof(
                target=link["name"], url=link["href"], tab_id=tab_id,
                proof_ref="browser_navigation_verified:"
                          + sha256(witness.encode()).hexdigest()[:24],
            )
    return None
