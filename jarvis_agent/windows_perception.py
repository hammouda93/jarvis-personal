from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass
from typing import Any

from .tools import normalize


def _desktop():
    # Import lazily inside Jarvis' worker thread. Importing pywinauto before
    # QApplication is created can initialize COM with a mode that conflicts
    # with Qt's OLE setup on Windows.
    from pywinauto import Desktop

    return Desktop(backend="uia")


def _send_keys(sequence: str) -> None:
    from pywinauto import keyboard

    keyboard.send_keys(sequence, pause=0.03)


_INTERACTIVE_TYPES = {
    "Button",
    "CheckBox",
    "ComboBox",
    "DataItem",
    "Edit",
    "Hyperlink",
    "ListItem",
    "MenuItem",
    "RadioButton",
    "Slider",
    "Spinner",
    "TabItem",
    "TreeItem",
}


@dataclass(frozen=True)
class UIElementInfo:
    name: str
    control_type: str
    automation_id: str
    enabled: bool
    visible: bool
    interactive: bool
    rectangle: tuple[int, int, int, int]


@dataclass(frozen=True)
class UIWindowInfo:
    title: str
    control_type: str
    enabled: bool
    visible: bool
    rectangle: tuple[int, int, int, int]


@dataclass(frozen=True)
class UIActionResult:
    success: bool
    message: str
    detail: str = ""


def _rect_tuple(wrapper: Any) -> tuple[int, int, int, int]:
    try:
        rect = wrapper.rectangle()
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return (0, 0, 0, 0)


def _element_name(wrapper: Any) -> str:
    try:
        return str(wrapper.window_text() or "").strip()
    except Exception:
        info = getattr(wrapper, "element_info", None)
        return str(getattr(info, "name", "") or "").strip()


def _control_type(wrapper: Any) -> str:
    info = getattr(wrapper, "element_info", None)
    return str(getattr(info, "control_type", "") or "").strip()


def _automation_id(wrapper: Any) -> str:
    info = getattr(wrapper, "element_info", None)
    return str(getattr(info, "automation_id", "") or "").strip()


def _is_visible(wrapper: Any) -> bool:
    try:
        return bool(wrapper.is_visible())
    except Exception:
        return True


def _is_enabled(wrapper: Any) -> bool:
    try:
        return bool(wrapper.is_enabled())
    except Exception:
        return True


def _active_window():
    windows = _desktop().windows(
        active_only=True,
        visible_only=True,
        top_level_only=True,
    )
    if not windows:
        return None
    return windows[0]


def _window_info(wrapper: Any) -> UIWindowInfo:
    return UIWindowInfo(
        title=_element_name(wrapper),
        control_type=_control_type(wrapper) or "Window",
        enabled=_is_enabled(wrapper),
        visible=_is_visible(wrapper),
        rectangle=_rect_tuple(wrapper),
    )


def _element_info(wrapper: Any) -> UIElementInfo:
    ctype = _control_type(wrapper)
    return UIElementInfo(
        name=_element_name(wrapper),
        control_type=ctype,
        automation_id=_automation_id(wrapper),
        enabled=_is_enabled(wrapper),
        visible=_is_visible(wrapper),
        interactive=ctype in _INTERACTIVE_TYPES,
        rectangle=_rect_tuple(wrapper),
    )


def list_windows(*, limit: int = 25) -> UIActionResult:
    try:
        wrappers = _desktop().windows(
            visible_only=True,
            top_level_only=True,
        )
    except Exception as exc:
        return UIActionResult(False, "Impossible de lire les fenêtres Windows.", str(exc))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for wrapper in wrappers:
        info = _window_info(wrapper)
        key = normalize(info.title)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(asdict(info))
        if len(items) >= max(1, min(limit, 50)):
            break

    return UIActionResult(
        True,
        f"{len(items)} fenêtre(s) visible(s).",
        json.dumps(items, ensure_ascii=False),
    )


def inspect_active_window(*, limit: int = 60) -> UIActionResult:
    try:
        window = _active_window()
    except Exception as exc:
        return UIActionResult(False, "Impossible d'inspecter la fenêtre active.", str(exc))

    if window is None:
        return UIActionResult(False, "Aucune fenêtre active détectée.")

    window_info = _window_info(window)
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    try:
        descendants = window.descendants()
    except Exception as exc:
        return UIActionResult(False, "Impossible de lire les éléments de la fenêtre.", str(exc))

    for wrapper in descendants:
        info = _element_info(wrapper)
        if not info.visible:
            continue
        if not info.name and not info.automation_id:
            continue
        key = (
            normalize(info.name),
            normalize(info.control_type),
            normalize(info.automation_id),
        )
        if key in seen:
            continue
        seen.add(key)
        items.append(asdict(info))
        if len(items) >= max(1, min(limit, 120)):
            break

    payload = {
        "window": asdict(window_info),
        "elements": items,
    }
    return UIActionResult(
        True,
        f"Fenêtre active inspectée: {window_info.title or 'sans titre'}.",
        json.dumps(payload, ensure_ascii=False),
    )


def _score_name(query: str, candidate: str) -> float:
    wanted = normalize(query)
    name = normalize(candidate)
    if not wanted or not name:
        return 0.0
    if wanted == name:
        return 1.0
    if wanted in name or name in wanted:
        return 0.94
    return difflib.SequenceMatcher(None, wanted, name).ratio()


def _rank_wrappers(
    wrappers: list[Any],
    query: str,
    *,
    control_type: str | None = None,
) -> list[tuple[float, Any]]:
    ranked: list[tuple[float, Any]] = []
    wanted_type = normalize(control_type or "")
    for wrapper in wrappers:
        if not _is_visible(wrapper):
            continue
        current_type = _control_type(wrapper)
        if wanted_type and normalize(current_type) != wanted_type:
            continue
        score = max(
            _score_name(query, _element_name(wrapper)),
            _score_name(query, _automation_id(wrapper)),
        )
        if score >= 0.70:
            ranked.append((score, wrapper))
    ranked.sort(key=lambda item: -item[0])
    return ranked


def _find_active_element(
    query: str,
    *,
    control_type: str | None = None,
) -> tuple[Any | None, list[str]]:
    window = _active_window()
    if window is None:
        return None, []

    candidates = [window]
    try:
        candidates.extend(window.descendants())
    except Exception:
        pass

    ranked = _rank_wrappers(
        candidates,
        query,
        control_type=control_type,
    )
    if not ranked:
        return None, []

    names = [_element_name(wrapper) for _score, wrapper in ranked[:5]]
    top_score = ranked[0][0]
    if top_score < 0.82:
        return None, names

    if len(ranked) > 1 and top_score - ranked[1][0] < 0.035:
        first = normalize(_element_name(ranked[0][1]))
        second = normalize(_element_name(ranked[1][1]))
        if first != second:
            return None, names

    return ranked[0][1], names


def click_ui_element(
    name: str,
    *,
    control_type: str | None = None,
) -> UIActionResult:
    target = (name or "").strip()
    if len(normalize(target)) < 2:
        return UIActionResult(False, "Le nom de l'élément est trop vague.")

    try:
        wrapper, alternatives = _find_active_element(
            target,
            control_type=control_type,
        )
    except Exception as exc:
        return UIActionResult(False, "Impossible de rechercher cet élément.", str(exc))

    if wrapper is None:
        if alternatives:
            return UIActionResult(
                False,
                "Élément ambigu. Précisez la cible.",
                json.dumps(alternatives, ensure_ascii=False),
            )
        return UIActionResult(False, f"Élément introuvable: {target}.")

    label = _element_name(wrapper) or target
    try:
        wrapper.set_focus()
        try:
            wrapper.invoke()
        except Exception:
            wrapper.click_input()
    except Exception as exc:
        return UIActionResult(False, f"Impossible d'activer {label}.", str(exc))

    return UIActionResult(True, f"Élément activé: {label}.", label)


def activate_window(title: str) -> UIActionResult:
    target = (title or "").strip()
    if len(normalize(target)) < 2:
        return UIActionResult(False, "Le nom de la fenêtre est trop vague.")

    try:
        windows = _desktop().windows(
            visible_only=True,
            top_level_only=True,
        )
        ranked = _rank_wrappers(windows, target)
    except Exception as exc:
        return UIActionResult(False, "Impossible de rechercher cette fenêtre.", str(exc))

    if not ranked or ranked[0][0] < 0.82:
        alternatives = [_element_name(item[1]) for item in ranked[:5]]
        return UIActionResult(
            False,
            "Fenêtre introuvable ou ambiguë.",
            json.dumps(alternatives, ensure_ascii=False),
        )

    wrapper = ranked[0][1]
    label = _element_name(wrapper) or target
    try:
        try:
            wrapper.restore()
        except Exception:
            pass
        wrapper.set_focus()
    except Exception as exc:
        return UIActionResult(False, f"Impossible d'activer la fenêtre {label}.", str(exc))

    return UIActionResult(True, f"Fenêtre activée: {label}.", label)


def write_ui_element(
    name: str,
    text: str,
) -> UIActionResult:
    target = (name or "").strip()
    value = str(text or "")
    if len(normalize(target)) < 2:
        return UIActionResult(False, "Le nom du champ est trop vague.")
    if len(value) > 4000:
        return UIActionResult(False, "Le texte est trop long pour une saisie UI directe.")

    try:
        wrapper, alternatives = _find_active_element(
            target,
            control_type="Edit",
        )
        if wrapper is None:
            # Some UIA applications expose editable controls under a custom
            # control type. Retry without forcing Edit, but still require the
            # wrapper to support set_edit_text.
            wrapper, alternatives = _find_active_element(target)
    except Exception as exc:
        return UIActionResult(False, "Impossible de rechercher ce champ.", str(exc))

    if wrapper is None:
        if alternatives:
            return UIActionResult(
                False,
                "Champ ambigu. Précisez la cible.",
                json.dumps(alternatives, ensure_ascii=False),
            )
        return UIActionResult(False, f"Champ introuvable: {target}.")

    label = _element_name(wrapper) or _automation_id(wrapper) or target
    try:
        wrapper.set_focus()
        if not hasattr(wrapper, "set_edit_text"):
            return UIActionResult(
                False,
                f"L'élément {label} n'accepte pas la saisie directe.",
                _control_type(wrapper),
            )
        wrapper.set_edit_text(value)
    except Exception as exc:
        return UIActionResult(False, f"Impossible d'écrire dans {label}.", str(exc))

    return UIActionResult(True, f"Texte saisi dans {label}.", label)


_ALLOWED_KEYS = {
    "enter": "{ENTER}",
    "return": "{ENTER}",
    "escape": "{ESC}",
    "esc": "{ESC}",
    "tab": "{TAB}",
    "space": "{SPACE}",
    "up": "{UP}",
    "down": "{DOWN}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
    "pageup": "{PGUP}",
    "pagedown": "{PGDN}",
    "home": "{HOME}",
    "end": "{END}",
}


def press_key(key: str) -> UIActionResult:
    normalized = normalize(key).replace(" ", "")
    sequence = _ALLOWED_KEYS.get(normalized)
    if sequence is None:
        return UIActionResult(
            False,
            "Cette touche n'est pas autorisée par le contrôle Windows de Jarvis.",
        )
    try:
        _send_keys(sequence)
    except Exception as exc:
        return UIActionResult(False, f"Impossible d'envoyer la touche {key}.", str(exc))
    return UIActionResult(True, f"Touche envoyée: {key}.", key)
