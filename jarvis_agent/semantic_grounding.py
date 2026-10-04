"""Generic UI roles and provenance-preserving structural/visual fusion."""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import replace
from typing import Any

from .ui_geometry import CaptureGeometry, intersection_over_union, normalized_box, valid_rect
from .ui_observation import SurfaceIdentity, UIEntity, UIObservation

SEMANTIC_ROLES = frozenset({
    "search_input", "text_input", "message_composer", "list_item",
    "conversation_item", "contact_result", "send_button", "save_button",
    "continue_button", "close_button", "navigation_tab", "browser_tab", "menu",
    "confirmation_dialog", "video_result", "content_title", "message",
    "email_input", "password_input", "checkbox", "button", "link",
    "scroll_region", "content_region", "conversation_list",
})
TEXT_ROLES = frozenset({"edit", "document", "combobox", "textbox", "searchbox", "textarea"})


def normalized_text(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    value = "".join(x for x in value if not unicodedata.combining(x))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value)).strip()


def semantic_roles(role: str, label: str, explicit: str = "") -> tuple[str, ...]:
    raw, text = normalized_text(role), normalized_text(label)
    inferred = set()
    if explicit in SEMANTIC_ROLES:
        inferred.add(explicit)
    if role in SEMANTIC_ROLES:
        inferred.add(role)
    if raw in TEXT_ROLES:
        inferred.add("text_input")
        if any(x in text.split() for x in ("recherche", "rechercher", "search", "find")) or raw == "searchbox":
            inferred.add("search_input")
        if any(x in text.split() for x in ("message", "composer", "reply", "reponse")):
            inferred.add("message_composer")
        if any(x in text.split() for x in ("email", "courriel")):
            inferred.add("email_input")
        if "password" in text or "mot de passe" in text:
            inferred.add("password_input")
    if raw in {"button", "pushbutton"}:
        inferred.add("button")
        for target, words in {
            "send_button": ("envoyer", "send"),
            "save_button": ("enregistrer", "sauvegarder", "save"),
            "continue_button": ("continuer", "continue", "next", "suivant"),
            "close_button": ("fermer", "close"),
        }.items():
            if any(w in text.split() for w in words):
                inferred.add(target)
    if raw in {"listitem", "dataitem", "treeitem", "row"}:
        inferred.add("list_item")
    if raw in {"tab", "tabitem"}:
        inferred.add("navigation_tab")
    if raw in {"hyperlink", "link"}:
        inferred.add("link")
    if raw in {"heading", "header"}:
        inferred.add("content_title")
    return tuple(sorted(inferred))


def structured_entities(payload: dict[str, Any]) -> list[UIEntity]:
    caps = payload.get("capabilities") or {}
    if not isinstance(caps,dict): caps = {}
    writable = {str(x.get("ref")) for x in caps.get("writable", []) if isinstance(x, dict)}
    actionable = {str(x.get("ref")) for x in caps.get("actionable", []) if isinstance(x, dict)}
    sensor = str(payload.get("sensor") or ("dom" if payload.get("browser") else "uia"))
    entities = []
    controls = payload.get("controls") or []
    for item in (controls if isinstance(controls,list) else [])[:160]:
        if not isinstance(item, dict) or not isinstance(item.get("ref"), str):
            continue
        ref = item["ref"]
        role = str(item.get("type") or item.get("role") or "")
        label = str(item.get("label") or item.get("name") or item.get("label_hint") or "")[:600]
        edit = item.get("writable") is True or ref in writable
        action = item.get("actionable") is True or ref in actionable or edit
        bounds = valid_rect(item.get("bounds")) or ()
        value = item.get("value") if isinstance(item.get("value"), str) else None
        if value is not None and isinstance(item.get("value_length"), int) and item["value_length"] > len(value):
            value = None  # A preview cannot prove equality of the complete control value.
        entities.append(UIEntity(
            ref=ref, native_ref=ref, sensor=sensor, technical_role=role,
            semantic_roles=semantic_roles(role, label, str(item.get("semantic_role") or "")),
            label=label, value=value, bounds=bounds, region=str(item.get("region") or ""),
            writable=edit, actionable=action, visible=item.get("visible") is not False,
            enabled=item.get("enabled") is not False,
            selected=item.get("selected") if isinstance(item.get("selected"), bool) else None,
            focused=item.get("focused") if isinstance(item.get("focused"), bool) else None,
            score=1.0, evidence_ids=(ref,), native_id=str(item.get("id") or ""),
        ))
    return entities


def visual_entities(payload: dict[str, Any], observation_id: str) -> list[UIEntity]:
    observation = payload.get("observation_json") or payload.get("observation") or {}
    if not isinstance(observation, dict):
        return []
    try:
        geometry = CaptureGeometry.from_metadata(payload)
    except (ValueError, TypeError, OverflowError):
        return []
    entities = []
    targets = observation.get("targets") or []
    for index, item in enumerate((targets if isinstance(targets,list) else [])[:100], 1):
        if not isinstance(item, dict) or item.get("visible") is False or item.get("grounding_confirmed") is False:
            continue
        box = normalized_box(item.get("box_1000"))
        confidence = item.get("confidence")
        if (
            box is None or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1
        ):
            continue
        role = str(item.get("role") or "")
        label = str(item.get("label") or "")[:600]
        roles = semantic_roles(role, label, str(item.get("semantic_role") or ""))
        edit = normalized_text(role) in TEXT_ROLES or bool(set(roles) & {
            "search_input", "message_composer", "email_input", "password_input", "text_input"
        })
        ref = f"{observation_id}:v{index}"
        entities.append(UIEntity(
            ref=ref, native_ref="", sensor="vision", technical_role=role,
            semantic_roles=roles, label=label,
            value=item.get("value") if isinstance(item.get("value"), str) else None,
            bounds=geometry.box_to_screen(box), region=str(item.get("region") or ""),
            writable=edit, actionable=edit or bool(set(roles)-{"content_title", "message", "confirmation_dialog"})
            or role.casefold() in {"button", "icon"},
            selected=item.get("selected") if isinstance(item.get("selected"), bool) else None,
            focused=item.get("focused") if isinstance(item.get("focused"), bool) else None,
            score=float(confidence), evidence_ids=(ref,),
        ))
    return entities


def fuse_observation(
    structured: dict[str, Any], visual: dict[str, Any] | None, *,
    observation_id: str, generation: int = 0, mission_target: dict[str, Any] | None = None,
) -> UIObservation:
    scope = SurfaceIdentity.from_payload(structured)
    entities = structured_entities(structured)
    uncertainties = []
    sensors = {"structured": {
        "status": "available" if structured.get("controls") else "empty",
        "captured_at": structured.get("captured_at", ""),
    }}
    texts: list[str] = [x[:2000] for x in structured.get("visible_text", []) if isinstance(x, str)][:100]
    summary = ""
    if visual:
        visual_scope = SurfaceIdentity.from_payload(visual)
        if scope.title and not scope.same_surface(visual_scope):
            uncertainties.append("SENSOR_SCOPE_MISMATCH")
            visual = None
        elif scope.bounds and visual_scope.bounds and scope.bounds != visual_scope.bounds:
            uncertainties.append("SENSOR_GEOMETRY_CHANGED")
            visual = None
        elif not scope.title:
            scope = visual_scope
    if visual:
        obs = visual.get("observation_json") or {}
        texts = list(dict.fromkeys(texts + [x[:2000] for x in (obs.get("visible_text") or []) if isinstance(x, str)]))[:100]
        summary = str(obs.get("summary") or "")[:1000]
        sensors["vision"] = {"status": "available", "captured_at": visual.get("captured_at", ""),
                             "model": visual.get("model"), "seconds": visual.get("seconds")}
        uncertainties.append("VISUAL_CONFIDENCE_UNCALIBRATED")
        uncertainties.extend(x for x in obs.get("ambiguities", []) if isinstance(x,str))
        for candidate in visual_entities(visual, observation_id):
            nearby = [(index,item) for index,item in enumerate(entities) if item.sensor != "vision"
                      and item.writable == candidate.writable and intersection_over_union(item.bounds,candidate.bounds) >= .55]
            generic = {"text_input", "button", "list_item", "content_region", "content_title", "message",
                       "conversation_item", "contact_result", "navigation_tab", "browser_tab", "menu"}
            conflicting = [(index,item) for index,item in nearby if item.label and candidate.label
                and normalized_text(item.label) != normalized_text(candidate.label)
                and normalized_text(item.label) not in {normalized_text(item.technical_role), "document", "champ", "texte"}
                and not ((set(item.semantic_roles)-generic) & (set(candidate.semantic_roles)-generic))]
            if conflicting:
                uncertainties.append("SENSOR_SEMANTIC_CONFLICT")
                for index,item in conflicting:
                    entities[index] = replace(item,visible=False)
                continue  # Gather fresh evidence; never relabel a known control on conflicting pixels.
            matches = [
                (index, item) for index, item in enumerate(entities)
                if item.sensor != "vision" and item.writable == candidate.writable
                and intersection_over_union(item.bounds, candidate.bounds) >= .55
                and (
                    normalized_text(item.technical_role) == normalized_text(candidate.technical_role)
                    or set(item.semantic_roles) & set(candidate.semantic_roles)
                    or item.writable and candidate.writable
                )
            ]
            if len(matches) == 1:
                index, existing = matches[0]
                entities[index] = replace(
                    existing, semantic_roles=tuple(sorted(set(existing.semantic_roles+candidate.semantic_roles))),
                    label=existing.label or candidate.label,
                    evidence_ids=existing.evidence_ids+candidate.evidence_ids,
                )
            else:
                entities.append(candidate)
    snapshot = structured.get("snapshot") or {}
    if snapshot.get("truncated") is True:
        uncertainties.append("STRUCTURE_TRUNCATED")
    readable = "available" if entities or texts else "empty"
    coverage = {
        "structured": snapshot.get("semantic_coverage", readable),
        "readable": readable,
        "actionable": "available" if any(x.actionable for x in entities) else "empty",
        "mission_target": "unspecified",
        "tree_complete": snapshot.get("tree_complete") is True and snapshot.get("truncated") is not True,
    }
    observation = UIObservation(
        observation_id, scope, tuple(entities), generation=generation, text_regions=tuple(texts),
        coverage=coverage, sensors=sensors, uncertainties=tuple(uncertainties), summary=summary,
        **({"monotonic_at": float(structured["monotonic_at"])} if structured.get("controls") and
           isinstance(structured.get("monotonic_at"),(int,float)) else
           {"monotonic_at":float(visual["monotonic_at"])} if visual and isinstance(visual.get("monotonic_at"),(int,float)) else {}),
    )
    if mission_target:
        from .target_resolver import TargetIntent, resolve_target
        intent = TargetIntent.from_dict(mission_target)
        resolution = resolve_target(intent, observation)
        coverage["mission_target"] = resolution.status
    return observation
