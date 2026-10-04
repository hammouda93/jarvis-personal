from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from .kernel_contracts import MissionContext, MissionStatus
from .mission_context_store import MissionContextStore
from .tools import normalize


_DESKTOP_ACTIONS = {
    "app.open",
    "folder.open",
    "folder.open_named",
    "file.open_named",
    "open_application",
    "open_file",
    "open_folder",
}

_BROWSER_ACTIONS = {
    "browser.open_url",
    "browser.search",
    "browser.search_site",
    "browser.back",
    "browser.close_tab",
    "open_url",
    "inspect_browser_page",
    "activate_browser_page",
    "write_browser_element",
    "click_browser_element",
    "press_browser_element",
    "scroll_browser_element",
    "inspect_browser_window",
}


def _site_from_url(url: str) -> str:
    try:
        host = urlparse(str(url or "")).netloc.casefold()
    except Exception:
        return ""
    if host.endswith("youtube.com"):
        return "YouTube"
    if host == "web.whatsapp.com":
        return "WhatsApp Web"
    if "google." in host:
        return "Google"
    parts = [item for item in host.split(".") if item and item != "www"]
    return (parts[0] if parts else host).replace("-", " ").title()


def _query_from_url(url: str) -> str:
    try:
        parsed = urlparse(str(url or ""))
        values = parse_qs(parsed.query)
    except Exception:
        return ""
    for key in ("search_query", "q", "query"):
        value = values.get(key)
        if value:
            return str(value[0] or "")
    return ""


def _browser_followup(text: str) -> bool:
    value = normalize(text)
    markers = (
        "youtube", "google", "whatsapp web", "navigateur", "browser",
        "onglet", "tab", "page", "resultat", "resultats", "video", "lien",
        "recherche", "cherche", "retour", "precedent", "premier", "deuxieme",
        "troisieme", "suivant",
    )
    return any(marker in value for marker in markers)


@dataclass
class LiveMissionView:
    mission_id: str
    objective: str
    status: str
    current_step: str
    browser: dict[str, Any]
    recent_steps: list[dict[str, Any]]
    current_request: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "objective": self.objective,
            "status": self.status,
            "current_step": self.current_step,
            "browser": dict(self.browser),
            "recent_steps": list(self.recent_steps),
            "current_request": self.current_request,
        }


class LiveMissionTracker:
    """Small authoritative live context shared by fast paths and the LLM.

    The full Kernel/MissionOrchestrator stays shadow. This tracker only carries
    verified runtime facts across turns so deterministic actions are never
    outside the mission.
    """

    def __init__(self, store: MissionContextStore | None = None) -> None:
        self.store = store or MissionContextStore()
        self.context: MissionContext | None = None
        self._version: int | None = None

    def _save(self) -> None:
        if self.context is None:
            return
        try:
            self._version = self.store.save(
                self.context,
                expected_version=self._version,
            )
        except RuntimeError:
            # A live UI turn must never fail because persistence raced.
            loaded = self.store.load(self.context.mission_id)
            self._version = loaded[1] if loaded is not None else None
            self._version = self.store.save(
                self.context,
                expected_version=self._version,
            )

    def _new(self, objective: str, *, domain: str = "") -> MissionContext:
        if self.context is not None and self.context.status == MissionStatus.RUNNING:
            self.context.status = MissionStatus.COMPLETED
            self._save()
        self._version = None
        self.context = MissionContext(
            mission_id=f"live_{uuid.uuid4().hex}",
            user_goal=str(objective or "")[:1000],
            status=MissionStatus.RUNNING,
            owner_agent_id="interaction",
            expected_state={
                "domain": domain,
                "current_request": str(objective or "")[:600],
            },
            observed_state={
                "browser": {},
                "recent_steps": [],
                "updated_at": time.time(),
            },
            tags=["live-runtime", "authoritative-context"],
        )
        self._save()
        return self.context

    def _ensure(self, user_text: str, *, domain: str = "") -> MissionContext:
        if self.context is None:
            return self._new(user_text, domain=domain)
        current_domain = str(self.context.expected_state.get("domain") or "")
        if domain and current_domain and domain != current_domain:
            return self._new(user_text, domain=domain)
        return self.context

    def prepare_turn(self, user_text: str) -> str:
        """Return current mission block before an LLM turn.

        Browser follow-ups extend the active browser mission instead of becoming
        isolated turns. Unrelated text does not destroy the previous verified
        facts, but it is not presented as a browser follow-up.
        """
        if self.context is None:
            return ""
        domain = str(self.context.expected_state.get("domain") or "")
        if domain == "browser" and _browser_followup(user_text):
            self.context.expected_state["current_request"] = str(user_text)[:600]
            self.context.current_step = "understand_followup"
            self.context.status = MissionStatus.RUNNING
            self._save()
            return self.context_block(user_text)
        return ""

    def record_direct(
        self,
        user_text: str,
        intent_name: str,
        intent_args: dict[str, Any],
        *,
        success: bool,
        detail: str = "",
        response_text: str = "",
    ) -> str:
        if intent_name in _BROWSER_ACTIONS:
            domain = "browser"
        elif intent_name in _DESKTOP_ACTIONS:
            domain = "desktop"
        else:
            # Conversational/deterministic utilities (time, etc.) are not
            # mission mutations. Preserve the active mission untouched.
            return ""
        context = self._ensure(user_text, domain=domain)
        context.status = MissionStatus.RUNNING if success else MissionStatus.BLOCKED
        context.current_step = str(intent_name)
        context.expected_state["current_request"] = str(user_text)[:600]

        observed = dict(context.observed_state or {})
        browser = dict(observed.get("browser") or {})
        steps = list(observed.get("recent_steps") or [])

        if domain == "browser":
            url = ""
            if intent_name == "browser.open_url":
                url = str(intent_args.get("url") or detail or "")
            elif intent_name in {"browser.search", "browser.search_site"}:
                url = str(detail or "")
                browser["query"] = str(intent_args.get("query") or "")
                browser["page_kind"] = "search_results"
            elif intent_name == "browser.back":
                browser["page_kind"] = "history_back"
            elif intent_name == "browser.close_tab" and success:
                browser["page_kind"] = "tab_closed"

            if url.startswith("http"):
                browser["url"] = url
                site = _site_from_url(url)
                if site:
                    browser["site"] = site
                query = _query_from_url(url)
                if query:
                    browser["query"] = query
                    browser["page_kind"] = "search_results"
                elif intent_name == "browser.open_url":
                    browser["page_kind"] = "page"

            site_arg = str(intent_args.get("site") or "").strip()
            if site_arg:
                browser["site"] = site_arg.title()
            browser["last_action"] = intent_name
            browser["last_success"] = bool(success)

        steps.append({
            "action": str(intent_name),
            "success": bool(success),
            "request": str(user_text)[:240],
            "response": str(response_text)[:240],
        })
        observed["browser"] = browser
        observed["recent_steps"] = steps[-8:]
        observed["updated_at"] = time.time()
        context.observed_state = observed
        self._save()
        return self.context_block(user_text)

    def record_agent_turn(
        self,
        user_text: str,
        actions: tuple[Any, ...] | list[Any],
        response_text: str,
    ) -> str:
        action_names = [
            str(getattr(action, "name", "") or "")
            for action in actions
        ]
        if self.context is None:
            if any(name in _BROWSER_ACTIONS for name in action_names):
                self._new(user_text, domain="browser")
            elif any(name in _DESKTOP_ACTIONS for name in action_names):
                self._new(user_text, domain="desktop")
            else:
                return ""
        elif (
            str(self.context.expected_state.get("domain") or "") == "browser"
            and any(name in _DESKTOP_ACTIONS for name in action_names)
        ):
            self._new(user_text, domain="desktop")
        elif (
            str(self.context.expected_state.get("domain") or "") == "desktop"
            and any(name in _BROWSER_ACTIONS for name in action_names)
        ):
            self._new(user_text, domain="browser")

        assert self.context is not None
        observed = dict(self.context.observed_state or {})
        browser = dict(observed.get("browser") or {})
        steps = list(observed.get("recent_steps") or [])
        for action in actions:
            name = str(getattr(action, "name", "") or "")
            if not name:
                continue
            success = bool(getattr(action, "success", False))
            steps.append({
                "action": name,
                "success": success,
                "request": str(user_text)[:240],
            })
            if name not in _BROWSER_ACTIONS and name not in {
                "inspect_active_window",
                "inspect_interface",
                "click_ui_element",
                "press_key",
            }:
                continue
            try:
                detail = json.loads(
                    str(getattr(action, "detail", "") or "{}")
                )
            except Exception:
                detail = {}
            if not isinstance(detail, dict):
                detail = {}

            current_window = detail.get("window")
            if isinstance(current_window, dict):
                title = str(current_window.get("title") or "").strip()
                if title:
                    browser["window_title"] = title

            post = detail.get("post_observation")
            if isinstance(post, dict):
                post_window = post.get("window")
                if isinstance(post_window, dict):
                    title = str(post_window.get("title") or "").strip()
                    if title:
                        browser["window_title"] = title
                if name == "click_ui_element" and success:
                    browser["page_kind"] = "navigated_after_click"

            if name == "inspect_browser_window" and success:
                browser["last_inspected"] = "browser_window"
            browser["last_action"] = name
            browser["last_success"] = success

        observed["browser"] = browser
        observed["recent_steps"] = steps[-8:]
        observed["last_response"] = str(response_text or "")[:600]
        observed["updated_at"] = time.time()
        self.context.observed_state = observed
        self.context.current_step = (
            str(getattr(actions[-1], "name", "") or "")
            if actions else "answered"
        )
        self.context.expected_state["current_request"] = str(user_text)[:600]
        self._save()
        return self.context_block(user_text)

    def view(self, current_request: str = "") -> LiveMissionView | None:
        if self.context is None:
            return None
        observed = dict(self.context.observed_state or {})
        return LiveMissionView(
            mission_id=self.context.mission_id,
            objective=self.context.user_goal,
            status=self.context.status.value,
            current_step=self.context.current_step,
            browser=dict(observed.get("browser") or {}),
            recent_steps=list(observed.get("recent_steps") or [])[-6:],
            current_request=(
                str(current_request or "")
                or str(self.context.expected_state.get("current_request") or "")
            ),
        )

    def context_block(self, current_request: str = "") -> str:
        view = self.view(current_request)
        if view is None:
            return ""
        payload = json.dumps(
            view.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            "[LIVE_MISSION]\n"
            + payload
            + "\nUse this as verified runtime context. Resolve references such as "
              "'the first result', 'go back', or 'this tab' from this mission "
              "before asking the user to repeat information."
        )
