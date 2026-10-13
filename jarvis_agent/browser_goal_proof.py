"""Evidence-bound, read-only proof for an explicitly requested browser navigation.

This is one generic goal-verifier profile, not a website-specific workflow.
No tool dispatch, permissions, LLM statements or invented goal fulfillment.
Other mission types remain uncertified pending their own evidence contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import json
import re
import unicodedata
from urllib.parse import urlsplit


_TURN_EVIDENCE = ContextVar("jarvis_browser_goal_evidence", default=None)


@contextmanager
def navigation_goal_scope(goal, previous_actions, *, certificate_allowed=True):
    """Trusted in-process evidence; model/checkpoint text cannot set this scope."""
    scope = {"goal": goal, "previous": tuple(previous_actions),
             "allowed": certificate_allowed, "fresh_tabs": set(), "grounding": []}
    token = _TURN_EVIDENCE.set(scope)
    try:
        yield scope
    finally:
        _TURN_EVIDENCE.reset(token)


def navigation_turn_inputs(goal, actions):
    scope = _TURN_EVIDENCE.get()
    if scope is None:
        return goal, tuple(actions), True
    return (scope["goal"], scope["previous"] + tuple(scope["grounding"]) + tuple(actions),
            scope["allowed"])


def navigation_resume_required():
    scope = _TURN_EVIDENCE.get()
    return bool(scope and scope["previous"] and _navigation_intent(scope["goal"]))


def note_navigation_observation(observation):
    scope = _TURN_EVIDENCE.get()
    if (scope and isinstance(observation, dict) and observation.get("sensor") == "dom"
            and observation.get("observation_id") and observation.get("ready") is not False):
        tab = observation.get("tab") or {}
        if type(tab.get("tab_id")) is int:
            scope["fresh_tabs"].add(tab["tab_id"])


def note_navigation_grounding(active, observation):
    """Retain actual automatic sensor reads; these are not synthetic tool calls."""
    if not navigation_resume_required():
        return
    from .native_tools import AgentActionResult
    scope = _TURN_EVIDENCE.get()
    for name, data in (("browser_get_active_tab", active), ("browser_observe_dom", observation)):
        if isinstance(data, dict):
            scope["grounding"].append(AgentActionResult(name, True, "Automatic read-only grounding",
                                                       json.dumps(data, ensure_ascii=False)))


def navigation_dispatch_guard(name, arguments):
    """Prevent redispatch of prior navigation effects, without granting permission."""
    scope = _TURN_EVIDENCE.get()
    if not navigation_resume_required():
        return ""
    if name == "open_url" and any(a.name == "open_url" for a in scope["previous"]):
        return "mission_initial_navigation_already_dispatched"
    if name == "browser_click" and any(
        a.name == name and _receipt(a).get("dispatched") is True
        and _receipt(a).get("source_ref") == arguments.get("ref")
        for a in scope["previous"]
    ):
        return "mission_browser_action_already_dispatched"
    if name in {"browser_click", "browser_write", "browser_select", "browser_press",
                "browser_navigate", "browser_back", "browser_forward", "browser_close_tab"}:
        if arguments.get("tab_id") not in scope["fresh_tabs"]:
            return "mission_resume_requires_fresh_dom_observation"
    return ""


def verify_browser_navigation_turn(goal, actions):
    original, evidence, allowed = navigation_turn_inputs(goal, actions)
    scope = _TURN_EVIDENCE.get()
    current = tuple(actions) + (tuple(scope["grounding"]) if scope else ())
    if navigation_resume_required() and not has_navigation_readback(current):
        return None
    return verify_browser_navigation_goal(original, evidence) if allowed else None


def has_navigation_readback(actions):
    return any(getattr(a, "success", False) and (
        a.name == "browser_get_active_tab" or
        (a.name == "browser_click" and isinstance(_receipt(a).get("independent_readback"), dict)))
        for a in actions)


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
    if not isinstance(data, dict):
        return {}
    for key in ("tab", "post_observation", "observation", "independent_readback"):
        value = data.get(key)
        if value is not None and not isinstance(value, dict):
            return {}
    for key in ("controls", "matches", "available_controls"):
        if data.get(key) is not None and not isinstance(data[key], list):
            return {}
    for key in ("post_observation", "observation"):
        value = data.get(key) or {}
        if value.get("tab") is not None and not isinstance(value["tab"], dict):
            return {}
        if value.get("controls") is not None and not isinstance(value["controls"], list):
            return {}
    return data


def _http(url):
    value = str(url or "")
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme in {"http", "https"} and bool(parsed.netloc)
            and not parsed.username and not parsed.password
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
        r"enregistr\w*|reviens|retourn\w*|back|ferm\w*|close|"
        r"lis|read|recherche|search|compare|analyse|contient|contains|titre|title)\b"
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


def _fresh_source(actions, found_at, click_at, item):
    """A find pins the current DOM generation; intervening refresh invalidates it."""
    expected = _receipt(actions[found_at]).get("observation_id")
    click = _receipt(actions[click_at])
    if expected and click.get("source_observation_id") != expected:
        return False
    if (item.get("document_id") and click.get("source_document_id")
            != item["document_id"]):
        return False
    for action in actions[found_at + 1:click_at]:
        if action.name in {"open_url", "browser_click"}:
            return False
        data = _receipt(action)
        observation = data.get("observation") or data
        observed = observation.get("observation_id") if isinstance(observation, dict) else None
        if action.name in {"browser_find", "browser_observe_dom", "browser_verify"}:
            if expected and observed and observed != expected:
                return False
    return True


def navigation_continuation_checkpoint(goal, actions):
    """One fresh ordinary navigation checkpoint; never dispatch or pick a control."""
    original, evidence, allowed = navigation_turn_inputs(goal, actions)
    if not allowed or _navigation_intent(original) is None or not actions:
        return None
    if verify_browser_navigation_goal(original, evidence) is not None:
        return None
    if any(not getattr(a, "success", False) or _receipt(a).get("outcome_unknown")
           for a in evidence):
        return None
    if any(a.name not in {"open_url", "browser_get_active_tab", "browser_observe_dom",
                          "browser_find", "browser_click", "browser_verify"} for a in evidence):
        return None
    last = actions[-1]
    if last.name not in {"browser_click", "browser_observe_dom", "browser_find"}:
        return None
    data = _receipt(last)
    post = data.get("post_observation") or data
    if not isinstance(post, dict) or not post.get("observation_id"):
        return None
    # Reuse the fresh read already in the receipt. A text-only yield should
    # not ask the user to continue a navigation they have already authorized.
    controls = post.get("controls") or post.get("matches") or post.get("available_controls")
    if not isinstance(controls, list) or not controls:
        return None
    return str(post["observation_id"])


def verify_browser_navigation_goal(goal, actions):
    """Certify only a complete, same-tab, independently verified link journey."""
    intent = _navigation_intent(goal)
    if intent is None:
        return None
    initial, target, menu_requested = intent
    actions = tuple(actions or ())
    if len(actions) > 128 or not actions:
        return None
    allowed = {"open_url", "browser_get_active_tab", "browser_observe_dom",
               "browser_find", "browser_click", "browser_verify"}
    if any(getattr(a, "name", "") not in allowed or
           _receipt(a).get("outcome_unknown") is True for a in actions):
        return None
    # A failed read can be superseded by a later real read of the same sensor.
    # Unknown effects and failed mutations always prevent a certificate.
    for i, action in enumerate(actions):
        if getattr(action, "success", False):
            continue
        if (_receipt(action).get("dispatched") is False
                and _receipt(action).get("outcome_unknown") is False):
            continue
        if (action.name not in allowed - {"open_url", "browser_click"}
                or _receipt(action).get("dispatched") is True
                or not any(later.name == action.name and getattr(later, "success", False)
                           for later in actions[i + 1:])):
            return None
    actions = tuple(a for a in actions if getattr(a, "success", False))
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
    start_tab = start.get("tab") or {}
    window_id = start_tab.get("window_id") if isinstance(start_tab, dict) else None
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
        i > start_at and a.name == "browser_observe_dom"
        and (_receipt(a).get("tab") or {}).get("tab_id") == tab_id
        for i, a in enumerate(actions)
    )
    grounded_initial_dom = any(
        i > start_at and a.name == "browser_observe_dom"
        and _receipt(a).get("sensor") == "dom"
        and bool(_receipt(a).get("observation_id"))
        and (_receipt(a).get("tab") or {}).get("tab_id") == tab_id
        and (_receipt(a).get("tab") or {}).get("url") == initial
        and isinstance(_receipt(a).get("controls"), list)
        and bool(_receipt(a).get("controls"))
        for i, a in enumerate(actions)
    )
    if not observed_dom or not (initial_active or grounded_initial_dom):
        return None
    menus = []
    links = []
    for i, action in enumerate(actions):
        if i <= start_at or action.name != "browser_find":
            continue
        found = _receipt(action)
        if found.get("scope") != ["browser", tab_id]:
            continue
        for item in (found.get("available_controls") or []) + (found.get("matches") or []):
            if (isinstance(item, dict) and item.get("type") == "button"
                    and "menu" in _plain(item.get("name"))
                    and isinstance(item.get("ref"), str)):
                if (item.get("expanded") is False or item.get("aria_expanded") == "false"
                        or re.search(r"\b(?:ouvrir|open|expand)\b", _plain(item.get("name")))):
                    menus.append((i, item))
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
        # DOM inspection must precede selection, not be retrofitted afterward.
        if not any(start_at < i < link_at and a.name == "browser_observe_dom"
                   and (_receipt(a).get("tab") or {}).get("tab_id") == tab_id
                   for i, a in enumerate(actions)):
            continue
        menu_clicks = set()
        for candidate_at, candidate in menus:
            for j, a in enumerate(actions):
                receipt = _receipt(a)
                post = receipt.get("post_observation") or {}
                after_tab = post.get("tab") or {}
                if (candidate_at < j < link_at and a.name == "browser_click"
                        and receipt.get("source_ref") == candidate["ref"]
                        and receipt.get("dispatched") is True
                        and _fresh_source(actions, candidate_at, j, candidate)
                        and post.get("sensor") == "dom" and post.get("observation_id")
                        and post.get("observation_id") != receipt.get("source_observation_id")
                        and after_tab.get("tab_id") == tab_id and after_tab.get("url") == initial
                        and any(isinstance(c, dict) and c.get("type") == "link"
                                and _plain(c.get("name")).strip() == target
                                and c.get("href") == link["href"] for c in post.get("controls") or [])):
                    menu_clicks.add(j)
        if menu_requested and any(i < link_at for i, _ in menus) and not menu_clicks:
            continue
        for click_at, a in enumerate(actions):
            if click_at <= link_at or a.name != "browser_click":
                continue
            click = _receipt(a)
            observation = click.get("post_observation")
            tab = observation.get("tab") if isinstance(observation, dict) else None
            if (click.get("source_ref") != link["ref"]
                    or click.get("dispatched") is not True
                    or (click.get("scope") is not None and click["scope"] != ["browser", tab_id])
                    or not _fresh_source(actions, link_at, click_at, link)
                    or click.get("verified") is not True
                    or click.get("postcondition") != "navigation_observed"
                    or not isinstance(tab, dict)
                    or tab.get("tab_id") != tab_id
                    or tab.get("url") != link["href"]
                    or tab.get("status") != "complete"):
                continue
            if window_id is not None and (tab.get("window_id") != window_id
                    or click.get("source_window_id") != window_id):
                continue
            if any(a.name == "browser_click" and j != click_at and j not in menu_clicks
                   for j, a in enumerate(actions)):
                continue
            later_tabs = []
            for later in actions[click_at + 1:]:
                data = _receipt(later)
                observed = data.get("observation") or data
                later_tab = observed.get("tab") or (data if later.name == "browser_get_active_tab" else {})
                if later_tab:
                    later_tabs.append(later_tab)
            if any(t.get("tab_id") != tab_id or (t.get("url") and t["url"] != link["href"])
                   for t in later_tabs):
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
            later_reads = [_receipt(read) for read in actions[click_at + 1:]
                           if read.name == "browser_get_active_tab"]
            last_read = later_reads[-1] if later_reads else readback
            later_readback = isinstance(last_read, dict) and (
                type(last_read.get("tab_id")) is int and last_read["tab_id"] == tab_id
                and last_read.get("url") == link["href"]
                and last_read.get("status") == "complete")
            if later_reads and not later_readback:
                continue
            if window_id is not None and (not isinstance(last_read, dict)
                                         or last_read.get("window_id") != window_id):
                continue
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
