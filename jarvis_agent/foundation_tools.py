"""Opt-in tool adapter: preserve the checkpoint registry/installer engine.

Browser missions cannot enter any Windows keyboard/mouse fallback. Page data
cannot set the mission scope; only user input and successful local actions can.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict

from .intent_guards import is_explicit_memory_write_request


def enabled(name):
    return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "on"}


_BROWSER_PROPS = {
    "tab_id": {
        "type": "integer",
        "description": "ID exact d'un onglet obtenu via browser_list_tabs, browser_get_active_tab ou une observation."
    },
    "url": {"type": "string", "description": "URL http/https à ouvrir dans l'onglet explicitement ciblé."},
    "ref": {"type": "string", "description": "Référence opaque provenant de la dernière observation DOM du même onglet."},
    "text": {
        "type": "string",
        "description": (
            "Pour browser_find: nom/label/placeholder visible ou accessible du contrôle à retrouver, "
            "jamais le texte que vous voulez saisir dans ce contrôle. Pour browser_write: texte à saisir. "
            "Pour browser_select: libellé ou valeur EXACTE de l'option observée à sélectionner."
        ),
    },
    "type": {
        "type": "string",
        "description": (
            "Rôle sémantique observé dans browser_observe_dom, par ex. searchbox, textbox, link, button, "
            "checkbox, combobox. Réutiliser le rôle réellement observé plutôt que deviner un tag HTML."
        ),
    },
    "exact": {"type": "boolean"},
    "mode": {"type": "string", "enum": ["replace", "append"]},
    "key": {"type": "string"},
    "title": {"type": "string"},
    "download_id": {"type": "integer"},
    "expected_value": {
        "type": "string",
        "description": (
            "Valeur attendue d'un contrôle observé. Pour browser_verify ciblé, "
            "fournir aussi tab_id et la ref fraîche du contrôle."
        ),
    },
}
_BROWSER_FIELDS = {
    "list_tabs": ([], []), "get_active_tab": ([], []),
    "activate_tab": (["tab_id"], ["tab_id"]), "navigate": (["url", "tab_id"], ["url", "tab_id"]),
    "observe_dom": (["tab_id"], ["tab_id"]),
    "find": (["tab_id", "text", "type", "exact"], ["tab_id"]),
    "click": (["tab_id", "ref"], ["tab_id", "ref"]),
    "write": (["tab_id", "ref", "text", "mode"], ["tab_id", "ref", "text"]),
    "select": (["tab_id", "ref", "text"], ["tab_id", "ref", "text"]),
    "press": (["tab_id", "ref", "key"], ["tab_id", "ref", "key"]),
    "back": (["tab_id"], ["tab_id"]), "forward": (["tab_id"], ["tab_id"]),
    "close_tab": (["tab_id"], ["tab_id"]), "download": (["url"], ["url"]),
    "verify": (
        ["tab_id", "ref", "expected_value", "text", "url", "title", "download_id"],
        [],
    ),
}
_BROWSER_DESCRIPTIONS = {
    "list_tabs": "Liste les onglets réels du profil Chrome normal avec tab_id, titre, URL et état.",
    "get_active_tab": "Retourne l'onglet actif réel. Utilise son tab_id pour continuer la mission dans le même onglet.",
    "activate_tab": "Active un onglet déjà observé.",
    "navigate": (
        "Navigue un onglet EXISTANT vers une URL. tab_id est obligatoire. "
        "Cette primitive ne doit jamais servir à créer un nouvel onglet ou à contourner l'observation d'un résultat visible."
    ),
    "observe_dom": (
        "CAPTEUR PRINCIPAL du contenu web. Observe la page réelle et retourne des contrôles DOM/accessibilité avec ref, "
        "type/rôle, name, placeholder, value, href, writable/actionable, bbox et ordre visuel. "
        "Utilise-le avant de deviner une cible ou un sélecteur."
    ),
    "find": (
        "Filtre le SNAPSHOT COURANT sans créer une nouvelle observation ni invalider ses refs. "
        "Recherche par nom/label/placeholder accessible et/ou rôle sémantique. "
        "Le champ text décrit la CIBLE à retrouver; ce n'est pas le contenu à saisir. "
        "S'il n'existe encore aucun snapshot, browser_find peut en créer un."
    ),
    "click": "Clique une ref réellement observée dans le même onglet puis vérifie l'état obtenu.",
    "write": "Écrit uniquement dans une ref texte/contenteditable writable; la valeur écrite est vérifiée localement.",
    "select": (
        "Sélectionne une option d'un contrôle natif SELECT/combobox réellement observé. "
        "Utilise uniquement une option présente dans le champ options du snapshot; la valeur sélectionnée est relue et vérifiée."
    ),
    "press": "Envoie une touche supportée (Enter, Space, Tab, Escape, flèches) à une ref observée/focalisable du même onglet.",
    "back": "Navigue en arrière dans l'historique de l'onglet ciblé.",
    "forward": "Navigue en avant dans l'historique de l'onglet ciblé.",
    "close_tab": "Ferme uniquement le tab_id ciblé et vérifie son absence.",
    "download": "Démarre un téléchargement http/https; le démarrage n'est pas la preuve de fin.",
    "verify": (
        "Observe à nouveau l'onglet et vérifie une postcondition explicite. "
        "Après un browser_write/browser_select incertain, utilise tab_id + ref fraîche "
        "+ expected_value pour vérifier le contrôle ciblé; ne valide jamais une écriture "
        "uniquement parce que le texte existe ailleurs dans la page."
    ),
}

_OS_MUTATIONS = {"press_key", "type_text_active_window", "write_ui_element", "click_ui_element",
                 "write_visual_target", "click_visual_target", "close_window", "close_tab",
                 "computer_click", "computer_write", "computer_press", "computer_shortcut",
                 "computer_focus_probe"}


class FoundationToolAdapter:
    def __init__(self, delegate, *, browser=None, computer=None, memory=None):
        self.delegate = delegate
        self.browser, self.computer, self.memory = browser, computer, memory
        self.browser_mode = False
        self.current_user_text = ""
        self.pending_verification = set()
        self.uncertain_scopes = set()
        self.pending_mutations = {}
        self._semantic_memory_write_authorized = False
        self.semantic_memory_engine = None

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def begin_turn(self, user_text):
        import re
        self.current_user_text = user_text
        self._semantic_memory_write_authorized = False
        if not self.browser:
            return
        from .tools import route
        intent = route(user_text)
        if intent.name.startswith("browser.") or (intent.name == "app.open" and intent.args.get("app") == "chrome"):
            self.browser_mode = True
            return
        if intent.name in {"app.open", "app.open_named"}:
            self.browser_mode = False
            return
        # Missing context errs toward blocking OS input. Scope remains browser
        # through continuation turns until an explicit other-app request.
        if re.search(r"https?://|\b(?:chrome|browser|navigateur|onglet|tab|site|web)\b", user_text, re.I):
            self.browser_mode = True
        elif re.search(r"\b(?:ouvre|ouvrir|open|inspecte|inspect)\b.*\b(?:application|app|exe|installateur|installer|fenetre|window)\b", user_text, re.I):
            self.browser_mode = False

    def browser_grounding_context(self, *, max_controls=32):
        """Return a bounded read-only observation of the current browser tab.

        This is perception, not planning: the model receives the real page state
        and decides what it means and which generic primitive to use.
        """
        if not self.browser or not self.browser_mode:
            return ""
        try:
            active = self.browser.get_active_tab()
            tab_id = active.get("tab_id")
            if not isinstance(tab_id, int):
                return ""
            url = str(active.get("url") or "")
            if not url.startswith(("http://", "https://")):
                return (
                    "BROWSER_GROUNDING_READ_ONLY:\n"
                    + json.dumps(
                        {"tab": active, "observation_available": False},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
            observation = self.browser.observe_dom(tab_id)
            # A fresh read-only observation establishes the current browser
            # state for this new user turn. Old unverified dispatches must not
            # poison the next mission once the actual current state is seen.
            scope = ("browser", tab_id)
            self.pending_verification.discard(scope)
            self.uncertain_scopes.discard(scope)
            self.pending_mutations.pop(scope, None)
            raw_controls = [
                item
                for item in list(observation.get("controls") or [])
                if isinstance(item, dict)
            ]
            def priority(item):
                role = str(item.get("type") or "").strip().lower()
                region = str(item.get("region") or "").strip().lower()
                score = 0
                if item.get("writable"):
                    score += 12
                if region == "content":
                    score += 9
                elif region in {"form", "dialog"}:
                    score += 8
                elif region in {"header", "navigation"}:
                    score -= 2
                if role in {"searchbox", "textbox", "combobox"}:
                    score += 10
                elif role in {"link", "button"}:
                    score += 7
                elif role in {"heading", "option", "tab", "menuitem"}:
                    score += 3
                if str(item.get("name") or "").strip():
                    score += 2
                return (-score, int(item.get("visual_index") or 9999))

            ranked_controls = sorted(raw_controls, key=priority)

            # Keep a second, deterministic view for ordinal link/result requests.
            # This is generic DOM evidence: no site names or URL patterns are
            # hard-coded. Duplicate links (for example thumbnail + title pointing
            # to the same target) collapse to one destination.
            ordered_links = []
            seen_link_targets = set()
            for item in sorted(
                raw_controls,
                key=lambda value: int(value.get("visual_index") or 9999),
            ):
                if str(item.get("type") or "").strip().lower() != "link":
                    continue
                href = str(item.get("href") or "").strip()
                if not href or href in seen_link_targets:
                    continue
                seen_link_targets.add(href)
                ordered_links.append(
                    {
                        key: item.get(key)
                        for key in (
                            "ref", "name", "href", "region", "visual_index",
                        )
                        if item.get(key) not in (None, "", False)
                    }
                )
                if len(ordered_links) >= 30:
                    break

            controls = []
            for item in ranked_controls[:max_controls]:
                controls.append(
                    {
                        key: item.get(key)
                        for key in (
                            "ref", "type", "name", "placeholder",
                            "aria_label", "href", "value", "tag",
                            "selected_text", "options", "editable_kind",
                            "writable", "selectable", "actionable", "region",
                            "visual_index",
                        )
                        if item.get(key) not in (None, "", False)
                    }
                )
            visible = str(observation.get("visible_text") or "")
            payload = {
                "observation_id": observation.get("observation_id"),
                "tab": observation.get("tab") or active,
                "sensor": observation.get("sensor") or "dom",
                "controls": controls,
                "controls_omitted": max(0, len(raw_controls) - len(controls)),
                "ordered_links": ordered_links,
                "visible_text": [
                    line.strip()
                    for line in visible.splitlines()
                    if line.strip()
                ][:30],
                "frames_skipped": observation.get("frames_skipped"),
                "note": (
                    "Fresh read-only current-page snapshot. ordered_links is de-duplicated "
                    "and sorted by real visual order for ordinal selection. Reuse refs only while "
                    "filtering; a browser mutation invalidates them and requires fresh evidence."
                ),
            }
            return (
                "BROWSER_GROUNDING_READ_ONLY:\n"
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
        except Exception as exc:
            return (
                "BROWSER_GROUNDING_READ_ONLY:\n"
                + json.dumps(
                    {
                        "observation_available": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )

    def authorize_semantic_memory_write(self, user_text):
        if str(user_text or "") != str(self.current_user_text or ""):
            raise RuntimeError("semantic_memory_write_turn_mismatch")
        if not is_explicit_memory_write_request(str(user_text or "")):
            raise RuntimeError(
                "persistent_write_requires_explicit_user_request"
            )
        self._semantic_memory_write_authorized = True

    def attach_semantic_memory_engine(self, engine):
        self.semantic_memory_engine = engine

    def ollama_tools(self):
        tools = self.delegate.ollama_tools()
        if self.memory and enabled("JARVIS_SEMANTIC_MEMORY_V5_ENABLED"):
            # Memory V5 is a pre-LLM runtime. Hide legacy memory tools from the
            # conversational model so it cannot bypass semantic admission,
            # projection, scoping or retrieval.
            tools = [
                item
                for item in tools
                if item["function"]["name"]
                not in {"remember_information", "recall_information"}
            ]
            if self.semantic_memory_engine is not None:
                tools.append(
                    self.delegate._ollama(
                        "semantic_memory_search",
                        "Recherche READ-ONLY dans la mémoire personnelle V5. "
                        "Utilise-la seulement quand une question personnelle "
                        "nécessite d'autres souvenirs que les preuves déjà "
                        "présentes. Fournis une question de recherche naturelle "
                        "et précise; l'outil retourne des preuves brutes et des "
                        "indices sémantiques, jamais une réponse à recopier "
                        "aveuglément.",
                        {
                            "query": {
                                "type": "string",
                                "description": (
                                    "Question naturelle décrivant exactement "
                                    "l'information personnelle à retrouver."
                                ),
                            }
                        },
                        ["query"],
                    )
                )
        if self.browser:
            legacy = {"list_browser_pages", "inspect_browser_page", "activate_browser_page",
                      "write_browser_element", "click_browser_element", "press_browser_element",
                      "scroll_browser_element", "open_web_search", "close_tab"}
            tools = [t for t in tools if t["function"]["name"] not in legacy and
                     not (self.browser_mode and t["function"]["name"] in _OS_MUTATIONS)]
            for op, (fields, required) in _BROWSER_FIELDS.items():
                tools.append(self.delegate._ollama(
                    "browser_" + op,
                    _BROWSER_DESCRIPTIONS[op],
                    {field: _BROWSER_PROPS[field] for field in fields},
                    required,
                ))
        if self.computer:
            fields = {"window_id": {"type": "string"}, "ref": {"type": "string"},
                      "text": {"type": "string"}, "type": {"type": "string"},
                      "key": {"type": "string"}, "exact": {"type": "boolean"},
                      "expected": {"type": "object"}, "condition": {"type": "object"}}
            fields["context"] = {"type":"object"}
            for op, names, required in (
                ("observe", ["window_id"], ["window_id"]),
                ("find", ["text", "type", "exact"], []),
                ("focus_probe", ["ref"], ["ref"]),
                ("click", ["ref", "expected"], ["ref"]),
                ("write", ["ref", "text", "expected", "context"], ["ref", "text"]),
                ("press", ["ref", "key", "expected", "context"], ["ref", "key"]),
                ("shortcut", ["window_id", "key"], ["window_id", "key"]),
                ("verify", ["window_id", "condition"], ["window_id", "condition"])):
                if self.browser_mode and "computer_" + op in _OS_MUTATIONS:
                    continue
                tools.append(self.delegate._ollama("computer_" + op,
                    "Grounding générique UIA/OCR local. Références opaques obligatoires. "
                    "Aucune affirmation de réussite sans postcondition vérifiée.",
                    {field: fields[field] for field in names}, required))
        return tools

    def openai_tools(self):
        return [{"type": "function", "name": t["function"]["name"],
                 "description": t["function"]["description"],
                 "parameters": t["function"]["parameters"], "strict": False}
                for t in self.ollama_tools()]

    def requires_confirmation(self, name):
        if name.startswith(("browser_", "computer_")):
            return False
        return self.delegate.requires_confirmation(name)

    def execute(self, name, arguments, *, approved=False):
        from .native_tools import AgentActionResult
        args = dict(arguments or {})
        try:
            if name == "browser_navigate" and not isinstance(args.get("tab_id"), int):
                raise RuntimeError("browser_navigate_requires_observed_tab_id")
            if name == "open_url" or (name.startswith("browser_") and name[8:] in {"navigate", "click", "write", "select", "press", "back", "forward", "close_tab", "download"}):
                if ("browser", args.get("tab_id")) in self.uncertain_scopes or ("browser", None) in self.uncertain_scopes:
                    raise RuntimeError("unknown_action_requires_verification_before_another_mutation")
            if name.startswith("computer_") and name[9:] in {"click", "write", "press", "shortcut"} and self.computer:
                window = (self.computer._observation or {}).get("window", {})
                if ("computer", args.get("window_id", window.get("window_id"))) in self.uncertain_scopes:
                    raise RuntimeError("unknown_action_requires_verification_before_another_mutation")
            if name == "save_verified_skill" and self.pending_verification:
                raise RuntimeError("core_actions_still_require_verification")
            if self.browser and self.browser_mode and name in _OS_MUTATIONS:
                raise RuntimeError("browser_mission_requires_tab_scoped_primitives")
            if self.browser and name == "open_url":
                payload = self.browser.navigate(args.get("url", ""))
                self.browser_mode = True
            elif self.browser and name == "open_application" and str(args.get("name", "")).casefold() in {
                    "chrome", "google chrome", "browser", "navigateur"}:
                active = self.browser.get_active_tab()
                payload = self.browser.activate_tab(active["tab_id"])
                self.browser_mode = True
            elif name.startswith("browser_") and self.browser:
                payload = self.browser.call(name[8:], **args)
                self.browser_mode = True

                # A replace write is idempotent, so an unknown outcome can be
                # recovered locally without risking a duplicate side effect:
                # verify the fresh target value first, then retry at most once
                # on the fresh successor ref when the requested value is absent.
                if (
                    name == "browser_write"
                    and isinstance(payload, dict)
                    and payload.get("outcome_unknown") is True
                    and str(args.get("mode") or "replace") == "replace"
                    and isinstance(args.get("tab_id"), int)
                    and isinstance(payload.get("target_after"), dict)
                    and str(payload["target_after"].get("ref") or "").strip()
                ):
                    expected_value = str(args.get("text") or "")
                    try:
                        verification = self.browser.call(
                            "verify",
                            tab_id=args["tab_id"],
                            ref=str(payload["target_after"]["ref"]),
                            expected_value=expected_value,
                        )
                    except Exception:
                        verification = None

                    if isinstance(verification, dict) and verification.get("verified") is True:
                        payload = {
                            **payload,
                            "verified": True,
                            "outcome_unknown": False,
                            "postcondition": "target_value",
                            "target_after": verification.get("target_after")
                            or payload.get("target_after"),
                            "post_observation": verification.get("observation")
                            or payload.get("post_observation"),
                            "recovery": {
                                "kind": "verify_after_unknown_replace",
                                "retry_count": 0,
                            },
                        }
                    elif isinstance(verification, dict):
                        fresh_target = verification.get("target_after") or {}
                        fresh_ref = str(fresh_target.get("ref") or "").strip()
                        if fresh_ref:
                            retry = self.browser.call(
                                "write",
                                tab_id=args["tab_id"],
                                ref=fresh_ref,
                                text=expected_value,
                                mode="replace",
                            )
                            if isinstance(retry, dict):
                                retry["recovery"] = {
                                    "kind": "bounded_unknown_replace_retry",
                                    "retry_count": 1,
                                }
                                payload = retry
            elif self.browser and name in {"open_web_search", "close_tab"}:
                raise RuntimeError("use_generic_browser_primitives_with_observed_tab_id")
            elif name == "remember_information" and self.memory:
                if enabled("JARVIS_SEMANTIC_MEMORY_V5_ENABLED"):
                    if not self._semantic_memory_write_authorized:
                        raise RuntimeError(
                            "persistent_write_requires_semantic_user_authorization"
                        )
                    self._semantic_memory_write_authorized = False
                else:
                    from .memory_router import MemoryRouter
                    if MemoryRouter().decide(self.current_user_text).kind != "write":
                        raise RuntimeError(
                            "persistent_write_requires_explicit_user_request"
                        )
                item = self.memory.remember(
                    str(args.get("content", "")),
                    tags=str(args.get("tags", "")),
                )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="Information mémorisée localement.",
                    detail=f"memory_id={item.id}",
                )
            elif name == "recall_information" and self.memory:
                from .memory_retrieval import search
                payload = [asdict(item) for item in search(self.memory, str(args.get("query", "")))]
            elif (
                name == "semantic_memory_search"
                and self.semantic_memory_engine is not None
            ):
                query_text = str(args.get("query", "")).strip()
                if not query_text:
                    raise RuntimeError("semantic_memory_search_query_required")
                intent = self.semantic_memory_engine.interpret_turn(
                    query_text
                )
                resolution = {
                    "status": "raw_only",
                    "hits": [],
                }
                if intent.operation == "recall" and intent.query is not None:
                    resolution = self.semantic_memory_engine.resolve(
                        intent.query
                    )

                hits = []
                for hit in list(resolution.get("hits") or [])[:12]:
                    projection = hit.fact.projection
                    hits.append(
                        {
                            "memory_id": hit.fact.memory_id,
                            "raw": str(hit.fact.raw_content or "")[:900],
                            "subject": projection.subject,
                            "relation": projection.relation,
                            "value": projection.value,
                            "kind": projection.kind,
                            "qualifiers": dict(projection.qualifiers),
                            "entities": list(projection.entities),
                            "scope": projection.scope,
                            "score": round(float(hit.score), 4),
                            "provenance": hit.fact.provenance,
                        }
                    )

                # This tool is already an explicit READ-ONLY retrieval action
                # chosen by the agent. Do not reject the search just because
                # the intent parser classifies the short search phrase as
                # "pass". Use deterministic raw retrieval as a second lane.
                from .memory_retrieval import search as raw_search

                raw_items = raw_search(
                    self.memory,
                    query_text,
                    limit=12,
                ) if self.memory is not None else []
                raw_fallback = [
                    {
                        "memory_id": item.id,
                        "raw": str(item.content or "")[:900],
                        "created_at": item.created_at,
                    }
                    for item in raw_items
                ]

                # Returning recent memories for an unrelated question
                # exposes unrequested personal facts to the model. Only an
                # explicitly interpreted inventory request may return recent
                # rows. Specific queries with no evidence stay genuinely empty.
                if (not hits and not raw_fallback and self.memory is not None
                        and intent.operation == "inspect"):
                    raw_fallback = [
                        {
                            "memory_id": item.id,
                            "raw": str(item.content or "")[:900],
                            "created_at": item.created_at,
                        }
                        for item in self.memory.recent_memories(limit=12)
                    ]

                payload = {
                    "status": resolution.get("status"),
                    "parser_operation": intent.operation,
                    "query": query_text,
                    "hits": hits,
                    "raw_fallback": raw_fallback,
                    "read_only": True,
                }
            elif name.startswith("computer_") and self.computer:
                op = name[9:]
                if op == "observe":
                    payload = self.computer.observe(args["window_id"])
                elif op == "find":
                    payload = self.computer.find(**args)
                elif op == "focus_probe":
                    payload = self.computer.focus_probe(args["ref"])
                elif op == "verify":
                    observation = self.computer.observe(args["window_id"])
                    payload = {"verified": self.computer.verify(args["condition"], observation=observation),
                               "post_observation": observation}
                elif op == "shortcut":
                    payload = self.computer.shortcut(args["window_id"],args["key"])
                else:
                    ref = args.pop("ref")
                    payload = self.computer.act(ref, op, **args)
            else:
                if name == "open_application" and self.browser:
                    if self.browser_mode:
                        from .tools import route
                        if route(self.current_user_text).name.startswith("browser."):
                            raise RuntimeError("browser_mission_requires_browser_navigate")
                    self.browser_mode = False
                return self.delegate.execute(name, arguments, approved=approved)
            browser_mutating_names = {
                "open_url", "browser_navigate", "browser_click", "browser_write",
                "browser_select", "browser_press", "browser_back",
                "browser_forward", "browser_close_tab", "browser_download",
            }
            if name in browser_mutating_names and not isinstance(payload, dict):
                raise RuntimeError("browser_mutation_missing_result")
            if isinstance(payload,dict):
                scope = ("browser", (payload.get("tab") or {}).get("tab_id",args.get("tab_id"))) if self.browser and (
                    name.startswith("browser_") or name == "open_url" or "tab" in payload) else ("computer",args.get("window_id") or
                    ((payload.get("post_observation") or {}).get("window") or {}).get("window_id"))
                if "download_id" in payload:
                    scope = ("browser_download",payload["download_id"])
                payload["scope"] = scope
                mutating = name in {"open_url", "browser_navigate", "browser_click", "browser_write",
                    "browser_select", "browser_press", "browser_back", "browser_forward",
                    "browser_close_tab", "browser_download", "computer_click", "computer_write",
                    "computer_press", "computer_shortcut"}
                if mutating and payload.get("verified") is not True:
                    self.pending_verification.add(scope)
                    self.pending_mutations[scope] = {
                        "name": name,
                        "ref": args.get("ref"),
                        "requested_value": (
                            payload.get("requested_value")
                            if isinstance(payload, dict)
                            else None
                        ),
                    }
                if mutating and payload.get("outcome_unknown") is True:
                    self.uncertain_scopes.add(scope)
                if payload.get("verified") is True and mutating:
                    self.pending_verification.discard(scope)
                    self.uncertain_scopes.discard(scope)
                    self.pending_mutations.pop(scope, None)
                elif payload.get("verified") is True and name == "browser_verify":
                    pending = self.pending_mutations.get(scope) or {}
                    requires_targeted_value = pending.get("name") in {
                        "browser_write",
                        "browser_select",
                    }
                    targeted_value_proof = (
                        payload.get("postcondition") == "target_value"
                        and bool(payload.get("target_after"))
                    )
                    if not requires_targeted_value or targeted_value_proof:
                        self.pending_verification.discard(scope)
                        self.uncertain_scopes.discard(scope)
                        self.pending_mutations.pop(scope, None)
            # A dispatched action is a successful tool invocation, but not
            # proof of the user's goal. Never encourage retry of a sent action.
            success = not isinstance(payload, dict) or payload.get("verified") is not False or bool(payload.get("dispatched"))
            return AgentActionResult(name=name, success=success,
                message="Opération exécutée." if success else "Action exécutée ; résultat à vérifier explicitement.",
                detail=json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            error_text = str(exc)
            if (
                self.browser
                and name.startswith("browser_")
                and isinstance(args.get("tab_id"), int)
                and (
                    "stale_or_cross_tab_browser_ref" in error_text
                    or "stale_browser_ref" in error_text
                )
            ):
                try:
                    observation = self.browser.observe_dom(args["tab_id"])
                except Exception:
                    observation = None
                if isinstance(observation, dict):
                    return AgentActionResult(
                        name=name,
                        success=False,
                        message=(
                            "La référence observée a expiré. Une observation "
                            "fraîche du même onglet est disponible."
                        ),
                        detail=json.dumps(
                            {
                                "reason": "stale_or_cross_tab_browser_ref",
                                "reobserve_required": True,
                                "post_observation": observation,
                                "scope": ("browser", args["tab_id"]),
                            },
                            ensure_ascii=False,
                        ),
                    )
            if self.browser and ("outcome_unknown" in error_text or isinstance(exc, TimeoutError)):
                scope = ("browser", args.get("tab_id"))
                self.pending_verification.add(scope)
                self.uncertain_scopes.add(scope)
            if self.computer and "computer_outcome_unknown" in error_text:
                scope = ("computer", ((self.computer._observation or {}).get("window") or {}).get("window_id"))
                self.pending_verification.add(scope)
                self.uncertain_scopes.add(scope)
            return AgentActionResult(name=name, success=False, message="Fondation : opération non validée.",
                                     detail=f"{type(exc).__name__}: {exc}")


class FoundationRuntime:
    def __init__(self, delegate, tools):
        self.delegate, self.tools = delegate, tools

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    @property
    def supports_grounded_context(self):
        return callable(
            getattr(self.delegate, "run_with_context", None)
        )

    def _pending_proof_affects_turn(self, result):
        """Do not let unresolved mutations from unrelated tabs mask a proved turn.

        Preserve legacy behavior for a turn without any mutation evidence.
        Any current mutation without a reliable scope remains conservatively
        unverified.
        """
        pending = self.tools.pending_verification
        if not pending:
            return False

        mutating_names = {
            "open_url", "browser_activate_tab", "browser_navigate",
            "browser_click", "browser_write", "browser_select",
            "browser_press", "browser_back", "browser_forward",
            "browser_close_tab", "browser_download",
            "computer_click", "computer_write", "computer_press",
            "computer_shortcut",
        }
        current_actions = [
            action
            for action in getattr(result, "actions", ())
            if action.name in mutating_names
        ]
        if not current_actions:
            return True

        scopes = set()
        for action in current_actions:
            try:
                detail = json.loads(action.detail or "{}")
            except (TypeError, ValueError):
                return True
            if not isinstance(detail, dict):
                return True
            scope = detail.get("scope")
            if not isinstance(scope, (list, tuple)) or len(scope) != 2:
                return True
            scopes.add(tuple(scope))

        if ("browser", None) in pending and any(
            scope[0] == "browser" for scope in scopes
        ):
            return True
        return bool(pending.intersection(scopes))

    def run(self, user_text, *, log=None, phase=None):
        was_browser_mode = self.tools.browser_mode
        self.tools.begin_turn(user_text)
        browser_context = (
            self.tools.browser_grounding_context()
            if was_browser_mode and self.tools.browser_mode
            else ""
        )
        method = getattr(self.delegate, "run_with_context", None)
        if browser_context and method is not None:
            result = method(
                user_text,
                browser_context,
                log=log,
                phase=phase,
            )
        else:
            result = self.delegate.run(user_text, log=log, phase=phase)
        if self._pending_proof_affects_turn(result):
            from dataclasses import replace
            return replace(result,text="Des actions ont été envoyées, mais leur résultat reste à vérifier dans l'interface.")
        return result

    def run_with_context(self, user_text, context, *, log=None, phase=None):
        was_browser_mode = self.tools.browser_mode
        self.tools.begin_turn(user_text)
        browser_context = (
            self.tools.browser_grounding_context()
            if was_browser_mode and self.tools.browser_mode
            else ""
        )
        combined_context = "\n\n".join(
            part for part in (str(context or "").strip(), browser_context) if part
        )
        method = getattr(self.delegate, "run_with_context", None)
        if method is None:
            result = self.delegate.run(user_text, log=log, phase=phase)
        else:
            result = method(
                user_text,
                combined_context,
                log=log,
                phase=phase,
            )
        if self._pending_proof_affects_turn(result):
            from dataclasses import replace
            return replace(result,text="Des actions ont été envoyées, mais leur résultat reste à vérifier dans l'interface.")
        return result

    def record_external_turn(self, user_text, assistant_text, **kwargs):
        self.tools.begin_turn(user_text)
        method = getattr(self.delegate, "record_external_turn", None)
        if method:
            method(user_text, assistant_text, **kwargs)

    def reset(self):
        self.tools.browser_mode = False
        self.tools.pending_verification.clear()
        self.tools.uncertain_scopes.clear()
        self.tools.pending_mutations.clear()
        self.delegate.reset()

    def warm_up(self, *, log=None):
        self.delegate.warm_up(log=log)


def build_foundation_tools(delegate):
    browser = computer = memory = None
    if enabled("JARVIS_MEMORY_CORE_ENABLED"):
        from .memory import LOCAL_MEMORY
        from .memory_core_store import MemoryCoreStore
        memory = MemoryCoreStore(LOCAL_MEMORY.db_path)
    if enabled("JARVIS_BROWSER_CORE_ENABLED"):
        from .browser_core import BrowserCore, NativeBrowserTransport
        config = os.getenv("JARVIS_BROWSER_BRIDGE_CONFIG", "")
        if not config:
            raise RuntimeError("JARVIS_BROWSER_BRIDGE_CONFIG_required")
        browser = BrowserCore(NativeBrowserTransport(config))
    if enabled("JARVIS_COMPUTER_CORE_ENABLED"):
        from .computer_grounding import ComputerGrounding, WindowsGroundingBackend
        from .fast_grounding import bounded_detect
        budget = min(5, max(0.5,float(os.getenv("JARVIS_GROUNDING_BUDGET_S", "3"))))
        model = os.getenv("JARVIS_GROUNDING_MODEL", "")
        endpoint = os.getenv("JARVIS_GROUNDING_ENDPOINT", "http://127.0.0.1:11434/api/chat")
        visual = lambda png, remaining: bounded_detect(png, timeout_s=min(2,remaining))
        element_model = ((lambda png, remaining: bounded_detect(png, provider="model", timeout_s=remaining,
                          endpoint=endpoint, model=model)) if model else None)
        computer = ComputerGrounding(WindowsGroundingBackend(focus_verifier=element_model), visual=visual,
                                     element_model=element_model, budget_s=budget)
    return FoundationToolAdapter(delegate, browser=browser, computer=computer, memory=memory)
