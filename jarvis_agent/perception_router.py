from __future__ import annotations

import json
from typing import Any

from .config import settings
from .screen_vision import observe_screen
from .windows_perception import UIActionResult, inspect_active_window as inspect_uia_window


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _uia_needs_visual_fallback(payload: dict[str, Any]) -> bool:
    """Return True only when structured UIA perception is demonstrably weak."""
    snapshot = dict(payload.get("snapshot") or {})
    semantic_coverage = str(
        snapshot.get("semantic_coverage") or ""
    ).strip().lower()

    if semantic_coverage == "insufficient":
        return True
    if semantic_coverage == "usable":
        return False

    capabilities = dict(payload.get("capabilities") or {})
    writable = list(capabilities.get("writable") or [])
    actionable = list(capabilities.get("actionable") or [])
    controls = list(payload.get("controls") or [])

    # Do not trigger vision merely because a window is simple. The structured
    # inspector explicitly marks known incomplete cases; this final guard only
    # catches a completely empty structured snapshot.
    return not controls and not writable and not actionable


def _compact_visual_observation(payload: dict[str, Any]) -> dict[str, Any]:
    observation = payload.get("observation_json")
    if not isinstance(observation, dict):
        observation = {}

    return {
        "title": str(payload.get("title") or ""),
        "bounds": list(payload.get("bounds") or []),
        "captured_width": payload.get("captured_width"),
        "captured_height": payload.get("captured_height"),
        "model": str(payload.get("model") or ""),
        "seconds": payload.get("seconds"),
        "observation": observation,
    }


def inspect_window_hybrid(
    *,
    title: str | None = None,
    focus: str = "",
) -> UIActionResult:
    """Inspect a Windows app with UIA first and local vision only when needed.

    This function is deliberately read-only. It does not click, type, or press
    keys. The agent can decide what to do after receiving the fused observation.
    """
    uia_result = inspect_uia_window(title=title)
    if not uia_result.success:
        return uia_result

    uia_payload = _json_object(uia_result.detail)
    if not settings.vision_enabled or not _uia_needs_visual_fallback(uia_payload):
        if uia_payload:
            uia_payload["perception"] = {
                "mode": "uia",
                "vision_attempted": False,
            }
            return UIActionResult(
                True,
                uia_result.message,
                json.dumps(uia_payload, ensure_ascii=False),
            )
        return uia_result

    visual_focus = (focus or "").strip()
    if not visual_focus:
        visual_focus = (
            "Complète l'inspection structurée de cette fenêtre. Identifie "
            "uniquement les contrôles réellement visibles et utiles pour "
            "naviguer, rechercher, sélectionner, écrire, valider ou revenir."
        )

    vision_result = observe_screen(
        title=title,
        focus=visual_focus,
    )
    if not vision_result.success:
        if uia_payload:
            uia_payload["perception"] = {
                "mode": "uia",
                "vision_attempted": True,
                "vision_success": False,
                "vision_error": str(vision_result.detail or "")[:500],
            }
            return UIActionResult(
                True,
                uia_result.message,
                json.dumps(uia_payload, ensure_ascii=False),
            )
        return uia_result

    vision_payload = _json_object(vision_result.detail)
    if not uia_payload:
        uia_payload = {
            "window": {
                "title": str(vision_payload.get("title") or title or ""),
                "bounds": list(vision_payload.get("bounds") or []),
            },
            "controls": [],
            "capabilities": {
                "writable": [],
                "actionable": [],
            },
            "snapshot": {},
        }

    uia_payload["visual_observation"] = _compact_visual_observation(
        vision_payload
    )
    uia_payload["perception"] = {
        "mode": "hybrid",
        "uia_success": True,
        "vision_attempted": True,
        "vision_success": True,
    }

    return UIActionResult(
        True,
        "Observation hybride UIA + vision locale terminée.",
        json.dumps(uia_payload, ensure_ascii=False),
    )
