from __future__ import annotations

import difflib
import json
import re
import time
from dataclasses import dataclass
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

_TEXT_TYPES = {"Document", "Text"}

_SNAPSHOT_ELEMENTS: dict[str, Any] = {}
_SNAPSHOT_WINDOW_TITLE = ""


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


def _is_assistant_window(wrapper: Any) -> bool:
    return normalize(_element_name(wrapper)) == "jarvis personal"


def _title_app_hint(title: str) -> str:
    """Extract a stable application suffix from a changing document title."""
    parts = [
        part.strip()
        for part in re.split(r"\s+(?:-|–|—|\|)\s+", str(title or ""))
        if part.strip()
    ]
    if len(parts) < 2:
        return ""
    hint = parts[-1]
    return hint if len(normalize(hint)) >= 4 else ""


_GENERIC_WINDOW_ROLE_WORDS = {
    "app",
    "application",
    "assistant",
    "config",
    "configuration",
    "dialog",
    "dialogue",
    "fenetre",
    "install",
    "installation",
    "installer",
    "programme",
    "program",
    "setup",
    "update",
    "updater",
    "user",
    "utilisateur",
    "window",
    "wizard",
}


def _window_identity_tokens(value: str) -> set[str]:
    """Return app-identity tokens while ignoring generic window-role words."""
    raw = str(value or "")
    # Preserve product identity inside names such as CursorUserSetup,
    # GitHubDesktopSetup or SomeAppInstaller before normalize() lowercases it.
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
    expanded = re.sub(
        r"(?i)(setup|installer|installation|updater|update|user)",
        r" \1 ",
        expanded,
    )
    normalized = normalize(expanded)
    tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._+-]*", normalized)
        if len(token) >= 2 and token not in _GENERIC_WINDOW_ROLE_WORDS
    }
    return tokens


def _window_identity_score(query: str, candidate: str) -> float:
    """Generic app/window identity score resilient to localized setup titles."""
    wanted = _window_identity_tokens(query)
    actual = _window_identity_tokens(candidate)
    if not wanted or not actual:
        return 0.0
    overlap = wanted & actual
    if not overlap:
        return 0.0
    if wanted <= actual or actual <= wanted:
        return 0.97
    union = wanted | actual
    return 0.72 + (0.22 * (len(overlap) / max(1, len(union))))


def _window_by_title(title: str):
    target = (title or "").strip()
    if not target:
        return None

    windows = _desktop().windows(
        visible_only=True,
        top_level_only=True,
    )
    ranked = _rank_wrappers(windows, target)
    if ranked and ranked[0][0] >= 0.82:
        return ranked[0][1]

    identity_ranked = sorted(
        (
            (_window_identity_score(target, _element_name(wrapper)), wrapper)
            for wrapper in windows
        ),
        key=lambda pair: -pair[0],
    )
    if identity_ranked and identity_ranked[0][0] >= 0.90:
        top = identity_ranked[0][0]
        tied = [
            wrapper
            for score, wrapper in identity_ranked
            if abs(score - top) < 0.015
        ]
        if len(tied) == 1:
            return tied[0]
        for wrapper in tied:
            try:
                if bool(getattr(wrapper, "is_active", lambda: False)()):
                    return wrapper
            except Exception:
                continue

    # Browser/editor document titles change constantly. If an earlier exact
    # title became stale, fall back to its stable application suffix
    # ("Google Chrome", "Visual Studio Code", etc.) instead of failing.
    hint = _title_app_hint(target)
    if hint:
        hinted = [
            wrapper
            for wrapper in windows
            if normalize(hint) in normalize(_element_name(wrapper))
        ]
        if len(hinted) == 1:
            return hinted[0]
        if hinted:
            active = [
                wrapper
                for wrapper in hinted
                if bool(getattr(wrapper, "is_active", lambda: False)())
            ]
            if len(active) == 1:
                return active[0]
    return None


def _active_window():
    """Return the user's active work window, not Jarvis' own overlay.

    Jarvis can temporarily become the foreground Qt window while updating its
    UI. In that case, choose the first visible non-Jarvis top-level window so
    perception stays focused on the user's application.
    """
    windows = _desktop().windows(
        active_only=True,
        visible_only=True,
        top_level_only=True,
    )
    if windows and not _is_assistant_window(windows[0]):
        return windows[0]

    try:
        native_item = _native_target_window(None)
        if native_item is not None:
            native_wrapper = _uia_window_from_handle(
                int(native_item.get("handle") or 0)
            )
            if native_wrapper is not None and not _is_assistant_window(
                native_wrapper
            ):
                return native_wrapper
    except Exception:
        pass

    candidates = _desktop().windows(
        visible_only=True,
        top_level_only=True,
    )
    for wrapper in candidates:
        if _is_assistant_window(wrapper):
            continue
        rect = _rect_tuple(wrapper)
        if rect[2] - rect[0] < 80 or rect[3] - rect[1] < 60:
            continue
        title = _element_name(wrapper)
        if normalize(title) in {"program manager", "barre des taches"}:
            continue
        return wrapper

    return windows[0] if windows else None


def _compact_window(wrapper: Any) -> dict[str, Any]:
    return {
        "title": _element_name(wrapper),
        "type": _control_type(wrapper) or "Window",
        "bounds": list(_rect_tuple(wrapper)),
    }


def _control_value(wrapper: Any) -> str:
    """Best-effort readable value for text controls and hyperlinks."""
    if wrapper is None:
        return ""

    getter = getattr(wrapper, "get_value", None)
    if callable(getter):
        try:
            value = str(getter() or "").strip()
            if value:
                return value
        except Exception:
            pass

    try:
        iface_value = getattr(wrapper, "iface_value", None)
    except Exception:
        iface_value = None
    if iface_value is not None:
        try:
            value = str(iface_value.CurrentValue or "").strip()
            if value:
                return value
        except Exception:
            pass

    try:
        legacy = getattr(wrapper, "legacy_properties", None)
    except Exception:
        legacy = None
    if callable(legacy):
        try:
            props = legacy() or {}
            for key in ("Value", "value"):
                value = str(props.get(key) or "").strip()
                if value:
                    return value
        except Exception:
            pass

    if _control_type(wrapper) in {"Edit", "Document", "ComboBox"}:
        return _element_name(wrapper)
    return ""


def _compact_control(ref: str, wrapper: Any) -> dict[str, Any]:
    control_type = _control_type(wrapper)
    item: dict[str, Any] = {
        "ref": ref,
        "type": control_type,
        "enabled": _is_enabled(wrapper),
    }
    if control_type in {"Edit", "Document", "ComboBox"}:
        item["writable"] = True
    name = _element_name(wrapper)
    automation_id = _automation_id(wrapper)
    if name:
        item["name"] = name[:160]
    if automation_id:
        item["id"] = automation_id[:100]

    value = _control_value(wrapper)
    if value:
        if control_type == "Hyperlink":
            item["target"] = value[:500]
        elif control_type in {"Edit", "Document", "ComboBox"}:
            item["value"] = value[:500]
            if len(value) > 500:
                item["value_length"] = len(value)

    item["bounds"] = list(_rect_tuple(wrapper))
    return item


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _native_visible_windows(*, limit: int = 20) -> list[dict[str, Any]]:
    """Fallback window enumeration using the Win32 API.

    UI Automation can intermittently raise WinError 6 when a top-level window
    disappears during enumeration. Native EnumWindows is much more tolerant
    for the simple task of listing visible titled windows.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    @enum_proc
    def callback(hwnd, _lparam):
        if len(items) >= max(1, min(int(limit), 30)):
            return False
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = int(user32.GetWindowTextLengthW(hwnd) or 0)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = str(buffer.value or "").strip()
            key = normalize(title)
            if not key or key in seen:
                return True
            seen.add(key)
            items.append(
                {
                    "title": title[:180],
                    "type": "Window",
                }
            )
        except Exception:
            return True
        return True

    user32.EnumWindows(callback, 0)
    return items


def _native_process_name(hwnd: int) -> str:
    """Best-effort executable name for a top-level HWND without extra deps."""
    import ctypes
    import os
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(
        wintypes.HWND(int(hwnd)),
        ctypes.byref(pid),
    )
    if not pid.value:
        return ""

    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(pid.value),
    )
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        )
        if not ok:
            return ""
        return os.path.basename(buffer.value or "").strip()
    finally:
        kernel32.CloseHandle(handle)


def _native_window_candidates(*, limit: int = 40) -> list[dict[str, Any]]:
    """Return visible top-level windows in Win32 z-order with stable handles."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    items: list[dict[str, Any]] = []
    seen: set[int] = set()

    enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    @enum_proc
    def callback(hwnd, _lparam):
        try:
            handle = int(hwnd or 0)
            if not handle or handle in seen or not user32.IsWindowVisible(hwnd):
                return True

            length = int(user32.GetWindowTextLengthW(hwnd) or 0)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = str(buffer.value or "").strip()
            key = normalize(title)
            if not key:
                return True

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                bounds = (0, 0, 0, 0)
            else:
                bounds = (
                    int(rect.left),
                    int(rect.top),
                    int(rect.right),
                    int(rect.bottom),
                )

            width = max(0, bounds[2] - bounds[0])
            height = max(0, bounds[3] - bounds[1])
            if width < 80 or height < 60:
                return True
            if key in {"program manager", "barre des taches"}:
                return True

            seen.add(handle)
            items.append(
                {
                    "handle": handle,
                    "title": title[:180],
                    "process": _native_process_name(handle)[:120],
                    "bounds": bounds,
                }
            )
            return len(items) < max(1, min(int(limit), 80))
        except Exception:
            return True

    user32.EnumWindows(callback, 0)
    return items


def _native_target_window(title: str | None = None) -> dict[str, Any] | None:
    """Resolve a requested/foreground work window without UI Automation."""
    import ctypes

    candidates = _native_window_candidates(limit=60)
    if not candidates:
        return None

    target = (title or "").strip()
    if target:
        ranked = sorted(
            (
                (_score_name(target, str(item.get("title") or "")), item)
                for item in candidates
            ),
            key=lambda pair: -pair[0],
        )
        if ranked and ranked[0][0] >= 0.82:
            return ranked[0][1]

        identity_ranked = sorted(
            (
                (
                    _window_identity_score(
                        target,
                        str(item.get("title") or ""),
                    ),
                    item,
                )
                for item in candidates
            ),
            key=lambda pair: -pair[0],
        )
        if identity_ranked and identity_ranked[0][0] >= 0.90:
            top = identity_ranked[0][0]
            tied = [
                item
                for score, item in identity_ranked
                if abs(score - top) < 0.015
            ]
            if len(tied) == 1:
                return tied[0]
            try:
                foreground = int(
                    ctypes.windll.user32.GetForegroundWindow() or 0
                )
            except Exception:
                foreground = 0
            for item in tied:
                if int(item.get("handle") or 0) == foreground:
                    return item

        process_ranked = sorted(
            (
                (
                    max(
                        _score_name(
                            target,
                            re.sub(
                                r"(?i)\\.exe$",
                                "",
                                str(item.get("process") or ""),
                            ),
                        ),
                        _window_identity_score(
                            target,
                            re.sub(
                                r"(?i)\\.exe$",
                                "",
                                str(item.get("process") or ""),
                            ),
                        ),
                    ),
                    item,
                )
                for item in candidates
                if item.get("process")
            ),
            key=lambda pair: -pair[0],
        )
        if process_ranked and process_ranked[0][0] >= 0.82:
            top = process_ranked[0][0]
            tied = [
                item
                for score, item in process_ranked
                if abs(score - top) < 0.015
            ]
            if len(tied) == 1:
                return tied[0]
            try:
                foreground = int(
                    ctypes.windll.user32.GetForegroundWindow() or 0
                )
            except Exception:
                foreground = 0
            for item in tied:
                if int(item.get("handle") or 0) == foreground:
                    return item

        hint = _title_app_hint(target)
        if hint:
            hinted = [
                item
                for item in candidates
                if normalize(hint) in normalize(str(item.get("title") or ""))
            ]
            if len(hinted) == 1:
                return hinted[0]
            if hinted:
                try:
                    foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)
                except Exception:
                    foreground = 0
                for item in hinted:
                    if int(item.get("handle") or 0) == foreground:
                        return item
        return None

    try:
        foreground = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        foreground = 0

    if foreground:
        for item in candidates:
            if int(item.get("handle") or 0) != foreground:
                continue
            if normalize(str(item.get("title") or "")) != "jarvis personal":
                return item
            break

    # EnumWindows follows top-level z-order. If Jarvis temporarily owns the
    # foreground, choose the first substantial visible non-Jarvis work window.
    for item in candidates:
        if normalize(str(item.get("title") or "")) == "jarvis personal":
            continue
        return item
    return None


def _uia_window_from_handle(handle: int):
    """Attach UI Automation directly to an already resolved Win32 HWND."""
    specification = _desktop().window(handle=int(handle))
    wrapper_object = getattr(specification, "wrapper_object", None)
    if callable(wrapper_object):
        return wrapper_object()
    return specification


def _uia_window_from_native_with_retry(
    item: dict[str, Any],
    *,
    attempts: int = 4,
    delay_s: float = 0.12,
):
    """Retry UIA attachment for a freshly opened/still-initializing HWND."""
    last_error: Exception | None = None
    handle = int(item.get("handle") or 0)
    if not handle:
        raise RuntimeError("Fenêtre native sans handle valide.")

    for attempt in range(max(1, attempts)):
        try:
            return _uia_window_from_handle(handle)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(max(0.0, delay_s))
    assert last_error is not None
    raise last_error


def _native_compact_window(item: dict[str, Any]) -> dict[str, Any]:
    bounds = tuple(item.get("bounds") or (0, 0, 0, 0))
    return {
        "title": str(item.get("title") or ""),
        "type": "Window",
        "bounds": [int(value) for value in bounds],
    }


def _native_only_inspection(
    item: dict[str, Any],
    *,
    uia_error: str,
) -> UIActionResult:
    """Return useful window perception even when its UIA tree is unavailable."""
    global _SNAPSHOT_ELEMENTS, _SNAPSHOT_WINDOW_TITLE

    _SNAPSHOT_ELEMENTS = {}
    _SNAPSHOT_WINDOW_TITLE = str(item.get("title") or "").strip()
    payload = {
        "window": _native_compact_window(item),
        "controls": [],
        "fallback": "win32_window_only",
        "uia_error": str(uia_error or "")[:300],
        "note": (
            "La fenêtre est identifiée par Win32, mais son arbre UI Automation "
            "n'est pas disponible. Les actions par ref nécessitent une nouvelle "
            "inspection UIA."
        ),
    }
    return UIActionResult(
        True,
        f"Fenêtre détectée: {_SNAPSHOT_WINDOW_TITLE or 'sans titre'}.",
        _json(payload),
    )


def list_windows(*, limit: int = 20) -> UIActionResult:
    uia_error = ""
    items: list[dict[str, Any]] = []

    try:
        wrappers = _desktop().windows(
            visible_only=True,
            top_level_only=True,
        )
        seen: set[str] = set()
        for wrapper in wrappers:
            title = _element_name(wrapper)
            key = normalize(title)
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "title": title[:180],
                    "type": _control_type(wrapper) or "Window",
                }
            )
            if len(items) >= max(1, min(limit, 30)):
                break
    except Exception as exc:
        uia_error = str(exc)

    if not items:
        try:
            items = _native_visible_windows(limit=limit)
        except Exception as exc:
            detail = str(exc)
            if uia_error:
                detail = f"UIA: {uia_error}; Win32: {detail}"
            return UIActionResult(
                False,
                "Impossible de lire les fenêtres Windows.",
                detail,
            )

    detail = _json(items)
    if uia_error:
        detail = _json(
            {
                "windows": items,
                "fallback": "win32",
                "uia_error": uia_error[:180],
            }
        )

    return UIActionResult(
        True,
        f"{len(items)} fenêtre(s) visible(s).",
        detail,
    )


def _nearest_text_label(
    wrapper: Any,
    informative: list[Any],
) -> str:
    """Best-effort visual label for an editable/control using nearby Text UIA."""
    left, top, right, bottom = _rect_tuple(wrapper)
    cy = (top + bottom) / 2
    candidates: list[tuple[float, str]] = []
    for label in informative:
        name = _element_name(label).strip()
        if not name:
            continue
        l, t, r, b = _rect_tuple(label)
        label_cy = (t + b) / 2

        # Prefer a label horizontally to the left on the same row.
        same_row = abs(label_cy - cy) <= max(24, (bottom - top) * 1.2)
        left_of = r <= right and r <= left + 40
        if same_row and left_of:
            gap = max(0, left - r)
            vertical = abs(label_cy - cy)
            candidates.append((gap + vertical * 1.5, name))

        # Also allow a label immediately above the control.
        above = b <= top and (top - b) <= 48
        horizontally_overlaps = not (r < left or l > right)
        if above and horizontally_overlaps:
            candidates.append(((top - b) + 20.0, name))

    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1][:160]


def inspect_active_window(
    *,
    title: str | None = None,
    limit: int = 36,
) -> UIActionResult:
    """Return a compact, model-friendly accessibility snapshot.

    Interactive controls are prioritized and receive short refs (e1, e2, ...).
    The refs can be used immediately by click/write operations, including for
    controls that expose no accessible label.
    """
    global _SNAPSHOT_ELEMENTS, _SNAPSHOT_WINDOW_TITLE

    window = None
    native_item: dict[str, Any] | None = None
    uia_error = ""

    try:
        window = _window_by_title(title) if title else _active_window()
    except Exception as exc:
        uia_error = str(exc)

    # UI Automation top-level enumeration can intermittently fail with
    # WinError 6 when a window disappears. Resolve the HWND with Win32, then
    # attach UIA directly to that stable handle instead of enumerating again.
    if window is None:
        attach_errors: list[str] = []
        for attempt in range(4):
            try:
                native_item = _native_target_window(title)
            except Exception as exc:
                native_error = str(exc)
                detail = (
                    f"UIA: {uia_error}; Win32: {native_error}"
                    if uia_error
                    else native_error
                )
                return UIActionResult(
                    False,
                    "Impossible d'inspecter la fenêtre.",
                    detail,
                )

            if native_item is not None:
                try:
                    window = _uia_window_from_handle(
                        int(native_item["handle"])
                    )
                    break
                except Exception as exc:
                    attach_errors.append(str(exc))

            if attempt < 3:
                time.sleep(0.15)

        if window is None:
            if native_item is None:
                if title:
                    detail = uia_error or title
                    return UIActionResult(
                        False,
                        f"Fenêtre introuvable: {title}.",
                        detail,
                    )
                return UIActionResult(
                    False,
                    "Aucune fenêtre active détectée.",
                    uia_error,
                )

            attach_error = attach_errors[-1] if attach_errors else ""
            combined = (
                f"{uia_error}; attach: {attach_error}"
                if uia_error
                else attach_error
            )
            return _native_only_inspection(
                native_item,
                uia_error=combined,
            )

    try:
        descendants = window.descendants()
    except Exception as exc:
        first_desc_error = exc

        if native_item is None:
            try:
                handle = int(getattr(window, "handle", 0) or 0)
                if handle:
                    native_item = next(
                        (
                            item
                            for item in _native_window_candidates(limit=60)
                            if int(item.get("handle") or 0) == handle
                        ),
                        None,
                    )
            except Exception:
                native_item = None

        descendants = None
        if native_item is not None:
            try:
                time.sleep(0.12)
                window = _uia_window_from_native_with_retry(
                    native_item,
                    attempts=3,
                    delay_s=0.12,
                )
                descendants = window.descendants()
            except Exception:
                descendants = None

        if descendants is None:
            if native_item is not None:
                combined = (
                    f"{uia_error}; descendants: {first_desc_error}"
                    if uia_error
                    else str(first_desc_error)
                )
                return _native_only_inspection(
                    native_item,
                    uia_error=combined,
                )
            return UIActionResult(
                False,
                "Impossible de lire les éléments de la fenêtre.",
                str(first_desc_error),
            )

    interactive: list[Any] = []
    documents: list[Any] = []
    informative: list[Any] = []
    document_rects: list[tuple[int, int, int, int]] = []
    seen: set[tuple[str, str, str, tuple[int, int, int, int]]] = set()

    for wrapper in descendants:
        if not _is_visible(wrapper):
            continue
        ctype = _control_type(wrapper)
        name = _element_name(wrapper)
        automation_id = _automation_id(wrapper)
        rect = _rect_tuple(wrapper)
        key = (
            normalize(name),
            normalize(ctype),
            normalize(automation_id),
            rect,
        )
        if key in seen:
            continue
        seen.add(key)

        if ctype == "Document":
            document_rects.append(rect)
            documents.append(wrapper)

        if ctype in _INTERACTIVE_TYPES:
            # Keep unlabeled interactive controls: their ref + type + position
            # can still let the agent operate them safely after inspection.
            interactive.append(wrapper)
        elif ctype == "Text" and name:
            informative.append(wrapper)

    # Browser accessibility trees contain lots of Chrome toolbar/bookmark
    # controls before the actual web page. Prefer controls physically inside
    # the largest Document region so page search boxes/buttons are not pushed
    # out of the compact snapshot.
    content_rect = None
    if document_rects:
        content_rect = max(
            document_rects,
            key=lambda value: max(0, value[2] - value[0]) * max(0, value[3] - value[1]),
        )

    def inside_content(wrapper: Any) -> bool:
        if content_rect is None:
            return False
        left, top, right, bottom = _rect_tuple(wrapper)
        cx = (left + right) / 2
        cy = (top + bottom) / 2
        return (
            content_rect[0] <= cx <= content_rect[2]
            and content_rect[1] <= cy <= content_rect[3]
        )

    content_controls = [item for item in interactive if inside_content(item)]
    chrome_controls = [item for item in interactive if not inside_content(item)]

    def visual_order(wrapper: Any) -> tuple[int, int, int, int]:
        left, top, right, bottom = _rect_tuple(wrapper)
        return (top, left, bottom, right)

    content_controls.sort(key=visual_order)
    chrome_controls.sort(key=visual_order)
    documents.sort(key=visual_order)
    informative.sort(key=visual_order)

    max_items = max(8, min(int(limit), 40))
    selected: list[Any] = []

    if content_rect is None:
        # Native dialogs (Save As/Open/confirmation windows) generally do not
        # expose a large Document control. Treat the whole dialog as content
        # instead of keeping only six "chrome" controls. Prioritize writable
        # fields and actionable buttons, then fill in visual order.
        priority_types = {"Edit", "ComboBox", "Button", "CheckBox", "RadioButton"}
        priority = [
            item
            for item in interactive
            if _control_type(item) in priority_types
        ]
        priority.sort(key=visual_order)
        for item in priority + sorted(interactive, key=visual_order):
            if item not in selected:
                selected.append(item)
            if len(selected) >= max_items:
                break
    else:
        selected.extend(content_controls[: max_items - 6])
        remaining = max_items - len(selected)
        if remaining > 0:
            selected.extend(chrome_controls[: min(remaining, 6)])

    remaining = max_items - len(selected)
    if remaining > 0:
        selected.extend(documents[: min(remaining, 2)])
    remaining = max_items - len(selected)
    if remaining > 0:
        selected.extend(informative[: min(remaining, 6)])

    _SNAPSHOT_ELEMENTS = {}
    _SNAPSHOT_WINDOW_TITLE = _element_name(window)

    controls: list[dict[str, Any]] = []
    writable_refs: list[dict[str, str]] = []
    actionable_refs: list[dict[str, str]] = []
    for index, wrapper in enumerate(selected, start=1):
        ref = f"e{index}"
        _SNAPSHOT_ELEMENTS[ref] = wrapper
        compact = _compact_control(ref, wrapper)
        if compact.get("writable"):
            label_hint = _nearest_text_label(wrapper, informative)
            if label_hint and not compact.get("name"):
                compact["label"] = label_hint
            elif label_hint:
                compact["label_hint"] = label_hint
            writable_refs.append(
                {
                    "ref": ref,
                    "label": str(
                        compact.get("label")
                        or compact.get("label_hint")
                        or compact.get("name")
                        or compact.get("id")
                        or compact.get("type")
                        or ""
                    )[:120],
                }
            )
        if compact.get("type") in {
            "Button",
            "CheckBox",
            "Hyperlink",
            "MenuItem",
            "RadioButton",
            "TabItem",
            "TreeItem",
        }:
            actionable_refs.append(
                {
                    "ref": ref,
                    "label": str(
                        compact.get("name")
                        or compact.get("id")
                        or compact.get("type")
                        or ""
                    )[:120],
                }
            )
        controls.append(compact)

    payload = {
        "window": _compact_window(window),
        "controls": controls,
        "capabilities": {
            "writable": writable_refs[:16],
            "actionable": actionable_refs[:24],
        },
        "snapshot": {
            "total_interactive": len(interactive),
            "selected_interactive": sum(
                1 for item in selected if item in interactive
            ),
            "truncated": len(interactive) > sum(
                1 for item in selected if item in interactive
            ),
            "has_document_region": content_rect is not None,
            "vision_recommended": (
                len(interactive) > len(selected)
                or (
                    not writable_refs
                    and any(
                        "nom du fichier" in normalize(_element_name(item))
                        or "file name" in normalize(_element_name(item))
                        for item in informative
                    )
                )
            ),
        },
        "fallback": "win32_handle_to_uia" if native_item is not None else None,
        "note": (
            "Utiliser ref pour un contrôle sans nom. "
            "Les refs restent valables jusqu'à la prochaine inspection."
        ),
    }
    return UIActionResult(
        True,
        f"Fenêtre inspectée: {_SNAPSHOT_WINDOW_TITLE or 'sans titre'}.",
        _json(payload),
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

    # Window titles vary in punctuation/typography across locales
    # (Bloc-notes, Bloc‑notes, en/em dashes, etc.). Compare a compact form too.
    compact_wanted = re.sub(r"[^a-z0-9]+", "", wanted)
    compact_name = re.sub(r"[^a-z0-9]+", "", name)
    if compact_wanted and compact_name:
        if compact_wanted == compact_name:
            return 0.99
        if compact_wanted in compact_name or compact_name in compact_wanted:
            return 0.95
        compact_score = difflib.SequenceMatcher(
            None,
            compact_wanted,
            compact_name,
        ).ratio()
    else:
        compact_score = 0.0

    return max(
        difflib.SequenceMatcher(None, wanted, name).ratio(),
        compact_score,
    )


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

    names = [
        _element_name(wrapper) or _automation_id(wrapper) or _control_type(wrapper)
        for _score, wrapper in ranked[:5]
    ]
    top_score = ranked[0][0]
    if top_score < 0.82:
        return None, names

    if len(ranked) > 1 and top_score - ranked[1][0] < 0.035:
        first = normalize(_element_name(ranked[0][1]))
        second = normalize(_element_name(ranked[1][1]))
        if first and second and first != second:
            return None, names

    return ranked[0][1], names


def _snapshot_element(ref: str):
    key = (ref or "").strip().lower()
    return _SNAPSHOT_ELEMENTS.get(key)


def click_ui_element(
    name: str = "",
    *,
    ref: str = "",
    control_type: str | None = None,
) -> UIActionResult:
    wrapper = None
    alternatives: list[str] = []

    if ref:
        wrapper = _snapshot_element(ref)
        if wrapper is None:
            return UIActionResult(
                False,
                "Référence UI inconnue ou expirée. Inspectez à nouveau la fenêtre.",
                ref,
            )
    else:
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
                    _json(alternatives),
                )
            return UIActionResult(False, f"Élément introuvable: {target}.")

    label = _element_name(wrapper) or _automation_id(wrapper) or ref or name
    try:
        wrapper.set_focus()
        try:
            wrapper.invoke()
        except Exception:
            wrapper.click_input()
    except Exception as exc:
        return UIActionResult(False, f"Impossible d'activer {label}.", str(exc))

    return UIActionResult(
        True,
        f"Élément activé: {label}.",
        "Action envoyée. Réinspecter si le résultat visuel final est important.",
    )


def activate_window(title: str) -> UIActionResult:
    target = (title or "").strip()
    if len(normalize(target)) < 2:
        return UIActionResult(False, "Le nom de la fenêtre est trop vague.")

    try:
        wrapper = _window_by_title(target)
    except Exception as exc:
        return UIActionResult(False, "Impossible de rechercher cette fenêtre.", str(exc))

    if wrapper is None:
        return UIActionResult(False, "Fenêtre introuvable ou ambiguë.", target)

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


def close_window(title: str | None = None) -> UIActionResult:
    """Close a top-level window and verify whether it actually disappeared."""
    target = (title or "").strip()
    try:
        wrapper = _window_by_title(target) if target else _active_window()
    except Exception as exc:
        return UIActionResult(False, "Impossible de rechercher cette fenêtre.", str(exc))

    if wrapper is None:
        return UIActionResult(False, "Fenêtre introuvable.")

    label = _element_name(wrapper) or target or "fenêtre active"
    try:
        handle = int(getattr(wrapper, "handle", 0) or 0)
    except Exception:
        handle = 0

    try:
        wrapper.close()
    except Exception as exc:
        return UIActionResult(False, f"Impossible de fermer {label}.", str(exc))

    time.sleep(0.25)
    try:
        remaining = _desktop().windows(
            visible_only=True,
            top_level_only=True,
        )
        if handle:
            still_open = any(
                int(getattr(item, "handle", 0) or 0) == handle
                for item in remaining
            )
        else:
            still_open = any(
                normalize(_element_name(item)) == normalize(label)
                for item in remaining
            )
    except Exception:
        still_open = True

    if still_open:
        return UIActionResult(
            False,
            f"{label} est encore ouverte.",
            "Une boîte de dialogue ou un état non enregistré peut bloquer la fermeture.",
        )

    return UIActionResult(
        True,
        f"Fenêtre fermée: {label}.",
        "Fermeture vérifiée.",
    )


def _is_selected_tab(wrapper: Any) -> bool:
    try:
        selected = getattr(wrapper, "is_selected", None)
        if callable(selected):
            return bool(selected())
    except Exception:
        pass
    try:
        iface = getattr(wrapper, "iface_selection_item", None)
        if iface is not None:
            return bool(iface.CurrentIsSelected)
    except Exception:
        pass
    return False


def close_tab(name: str = "") -> UIActionResult:
    """Close a tab inside the active application without closing its window."""
    window = _active_window()
    if window is None:
        return UIActionResult(False, "Aucune fenêtre active détectée.")

    try:
        descendants = window.descendants()
    except Exception as exc:
        return UIActionResult(
            False,
            "Impossible de lire les onglets de la fenêtre active.",
            str(exc),
        )

    window_bounds = _rect_tuple(window)
    top_limit = window_bounds[1] + 120
    tabs = [
        wrapper
        for wrapper in descendants
        if (
            _control_type(wrapper) == "TabItem"
            and _is_visible(wrapper)
            and _rect_tuple(wrapper)[1] <= top_limit
        )
    ]
    if not tabs:
        return UIActionResult(
            False,
            "Aucun onglet contrôlable n'a été détecté dans la fenêtre active.",
        )

    target = (name or "").strip()
    chosen = None
    if target:
        ranked = sorted(
            (
                (
                    max(
                        _score_name(target, _element_name(wrapper)),
                        _window_identity_score(
                            target,
                            _element_name(wrapper),
                        ),
                    ),
                    wrapper,
                )
                for wrapper in tabs
            ),
            key=lambda pair: -pair[0],
        )
        if ranked and ranked[0][0] >= 0.82:
            if (
                len(ranked) == 1
                or ranked[0][0] - ranked[1][0] >= 0.035
                or normalize(_element_name(ranked[0][1]))
                == normalize(_element_name(ranked[1][1]))
            ):
                chosen = ranked[0][1]
    else:
        for wrapper in tabs:
            if _is_selected_tab(wrapper):
                chosen = wrapper
                break

    if chosen is None:
        if target:
            alternatives = [
                _element_name(wrapper)
                for wrapper in tabs[:8]
                if _element_name(wrapper)
            ]
            return UIActionResult(
                False,
                "Onglet introuvable ou ambigu.",
                _json({"target": target, "visible_tabs": alternatives}),
            )
        return UIActionResult(
            False,
            "Impossible d'identifier l'onglet actif.",
        )

    label = _element_name(chosen) or target or "onglet actif"

    if target:
        try:
            chosen.click_input()
            time.sleep(0.08)
        except Exception:
            try:
                chosen.set_focus()
            except Exception as exc:
                return UIActionResult(
                    False,
                    f"Impossible d'activer l'onglet {label}.",
                    str(exc),
                )

    try:
        _send_keys("^w")
    except Exception as exc:
        return UIActionResult(
            False,
            f"Impossible de fermer l'onglet {label}.",
            str(exc),
        )

    time.sleep(0.20)

    try:
        current = _active_window()
        if current is None:
            return UIActionResult(
                True,
                f"Onglet fermé: {label}.",
                _json(
                    {
                        "target": label,
                        "verified": True,
                        "window_closed_as_last_tab": True,
                    }
                ),
            )
        current_bounds = _rect_tuple(current)
        current_top_limit = current_bounds[1] + 120
        remaining = [
            wrapper
            for wrapper in current.descendants()
            if (
                _control_type(wrapper) == "TabItem"
                and _is_visible(wrapper)
                and _rect_tuple(wrapper)[1] <= current_top_limit
            )
        ]
        if target:
            still_present = any(
                max(
                    _score_name(target, _element_name(wrapper)),
                    _window_identity_score(
                        target,
                        _element_name(wrapper),
                    ),
                )
                >= 0.90
                for wrapper in remaining
            )
            if still_present:
                return UIActionResult(
                    False,
                    f"L'onglet {label} semble encore ouvert.",
                    _json(
                        {
                            "target": label,
                            "verified": False,
                            "visible_tabs": [
                                _element_name(wrapper)
                                for wrapper in remaining[:8]
                            ],
                        }
                    ),
                )
    except Exception:
        return UIActionResult(
            True,
            f"Onglet fermé: {label}.",
            _json(
                {
                    "target": label,
                    "verified": False,
                    "note": "Fermeture envoyée; vérification UIA indisponible.",
                }
            ),
        )

    return UIActionResult(
        True,
        f"Onglet fermé: {label}.",
        _json({"target": label, "verified": True}),
    )


def _editable_target(wrapper: Any):
    if wrapper is None:
        return None
    if _control_type(wrapper) == "Edit" and hasattr(wrapper, "set_edit_text"):
        return wrapper
    try:
        for child in wrapper.descendants():
            if _control_type(child) == "Edit" and hasattr(child, "set_edit_text"):
                return child
    except Exception:
        pass
    if hasattr(wrapper, "set_edit_text"):
        return wrapper
    return None


def _type_text_into_focused_control(wrapper: Any, value: str) -> bool:
    """Fallback only for controls that can reasonably accept text input."""
    if _control_type(wrapper) not in {"Edit", "Document", "ComboBox"}:
        return False

    try:
        wrapper.set_focus()
    except Exception:
        pass
    try:
        wrapper.click_input()
    except Exception:
        pass

    type_keys = getattr(wrapper, "type_keys", None)
    if callable(type_keys):
        try:
            type_keys(
                "^a",
                set_foreground=True,
            )
        except Exception:
            pass
        try:
            type_keys(
                value,
                with_spaces=True,
                set_foreground=True,
            )
            return True
        except Exception:
            return False
    return False


def write_ui_element(
    name: str,
    text: str,
    *,
    ref: str = "",
    mode: str = "replace",
) -> UIActionResult:
    target = (name or "").strip()
    value = str(text or "")
    normalized_mode = normalize(mode or "replace")
    if normalized_mode not in {"replace", "append", "insert"}:
        return UIActionResult(
            False,
            "Mode d'écriture invalide. Utilisez replace, append ou insert.",
            normalized_mode,
        )
    if len(value) > 4000:
        return UIActionResult(False, "Le texte est trop long pour une saisie UI directe.")

    wrapper = None
    alternatives: list[str] = []

    if ref:
        wrapper = _snapshot_element(ref)
        if wrapper is None:
            return UIActionResult(
                False,
                "Référence UI inconnue ou expirée. Inspectez à nouveau la fenêtre.",
                ref,
            )
    else:
        if len(normalize(target)) < 2:
            return UIActionResult(False, "Le nom du champ est trop vague.")
        try:
            wrapper, alternatives = _find_active_element(
                target,
                control_type="Edit",
            )
            if wrapper is None:
                wrapper, alternatives = _find_active_element(target)
        except Exception as exc:
            return UIActionResult(False, "Impossible de rechercher ce champ.", str(exc))

    if wrapper is None:
        if alternatives:
            return UIActionResult(
                False,
                "Champ ambigu. Précisez la cible.",
                _json(alternatives),
            )
        return UIActionResult(False, f"Champ introuvable: {target}.")

    control_type = _control_type(wrapper)
    if control_type not in {"Edit", "Document", "ComboBox"}:
        label = _element_name(wrapper) or _automation_id(wrapper) or ref or target
        return UIActionResult(
            False,
            f"L'élément {label} n'accepte pas la saisie directe.",
            control_type,
        )

    editable = _editable_target(wrapper)
    label = (
        _element_name(wrapper)
        or _automation_id(wrapper)
        or ref
        or target
    )
    before = _control_value(editable or wrapper)

    if normalized_mode == "insert":
        active = editable or wrapper
        try:
            active.set_focus()
        except Exception:
            pass
        type_keys = getattr(active, "type_keys", None)
        if not callable(type_keys):
            return UIActionResult(
                False,
                f"Impossible d'insérer du texte dans {label}.",
                control_type,
            )
        try:
            type_keys(value, with_spaces=True, set_foreground=True)
        except Exception as exc:
            return UIActionResult(
                False,
                f"Impossible d'insérer du texte dans {label}.",
                str(exc),
            )
    elif editable is not None:
        desired = value if normalized_mode == "replace" else before + value
        try:
            editable.set_focus()
            editable.set_edit_text(desired)
        except Exception as exc:
            return UIActionResult(
                False,
                f"Impossible d'écrire dans {label}.",
                str(exc),
            )
    else:
        if normalized_mode == "append":
            try:
                wrapper.set_focus()
                wrapper.click_input()
                type_keys = getattr(wrapper, "type_keys", None)
                if not callable(type_keys):
                    raise RuntimeError("type_keys indisponible")
                type_keys("{END}", set_foreground=True)
                type_keys(value, with_spaces=True, set_foreground=True)
            except Exception as exc:
                return UIActionResult(
                    False,
                    f"Impossible d'ajouter du texte dans {label}.",
                    str(exc),
                )
        elif not _type_text_into_focused_control(wrapper, value):
            return UIActionResult(
                False,
                f"L'élément {label} n'accepte pas la saisie directe.",
                control_type,
            )

    after = _control_value(editable or wrapper)
    if normalized_mode == "replace":
        verified = bool(after) and after == value
    elif normalized_mode == "append":
        verified = bool(after) and after.endswith(value) and len(after) >= len(before)
    else:
        verified = bool(after) and value in after

    return UIActionResult(
        True,
        f"Texte saisi dans {label}.",
        _json(
            {
                "mode": normalized_mode,
                "verified": verified,
                "before": before[:500],
                "value": after[:500],
                "value_length": len(after),
            }
        ),
    )


def type_text_active_window(
    text: str,
    *,
    title: str = "",
    mode: str = "insert",
) -> UIActionResult:
    """Fallback typing when UIA cannot expose an editable control.

    This sends text only to the currently focused control in the requested
    foreground window. It is intentionally generic and should be used only
    after UIA inspection failed or returned no writable controls.
    """
    value = str(text or "")
    if not value:
        return UIActionResult(False, "Le texte à saisir est vide.")
    if len(value) > 4000:
        return UIActionResult(False, "Le texte est trop long pour une saisie directe.")

    normalized_mode = normalize(mode or "insert")
    if normalized_mode not in {"replace", "append", "insert"}:
        return UIActionResult(
            False,
            "Mode d'écriture invalide. Utilisez replace, append ou insert.",
            normalized_mode,
        )

    target = (title or _SNAPSHOT_WINDOW_TITLE or "").strip()
    if target:
        activation = activate_window(target)
        if not activation.success:
            return UIActionResult(
                False,
                "Impossible d'activer la fenêtre avant la saisie.",
                activation.detail or target,
            )
        time.sleep(0.08)

    try:
        import win32clipboard

        previous_text: str | None = None
        try:
            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(
                    win32clipboard.CF_UNICODETEXT
                ):
                    previous_text = win32clipboard.GetClipboardData(
                        win32clipboard.CF_UNICODETEXT
                    )
            except Exception:
                previous_text = None
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            previous_text = None

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(
                value,
                win32clipboard.CF_UNICODETEXT,
            )
        finally:
            win32clipboard.CloseClipboard()

        if normalized_mode == "replace":
            _send_keys("^a")
        elif normalized_mode == "append":
            _send_keys("^{END}")

        _send_keys("^v")
        time.sleep(0.08)

        if previous_text is not None:
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(
                        previous_text,
                        win32clipboard.CF_UNICODETEXT,
                    )
                finally:
                    win32clipboard.CloseClipboard()
            except Exception:
                pass
    except Exception as exc:
        return UIActionResult(
            False,
            "Impossible de saisir le texte dans la fenêtre active.",
            str(exc),
        )

    return UIActionResult(
        True,
        "Texte saisi dans le contrôle actuellement au focus.",
        _json(
            {
                "mode": normalized_mode,
                "target_window": target,
                "verified": False,
                "note": (
                    "Saisie clavier de secours effectuée. "
                    "Réinspecter si une preuve du contenu est nécessaire."
                ),
            }
        ),
    )


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
    "altleft": "%{LEFT}",
    "altright": "%{RIGHT}",
    "ctrls": "^s",
    "ctrlshifts": "^+s",
    "ctrlf": "^f",
    "ctrll": "^l",
    "ctrlc": "^c",
    "ctrlv": "^v",
    "ctrla": "^a",
    "ctrlz": "^z",
    "ctrly": "^y",
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
        if (
            _SNAPSHOT_WINDOW_TITLE
            and normalize(_SNAPSHOT_WINDOW_TITLE) != "jarvis personal"
        ):
            try:
                target_window = _window_by_title(_SNAPSHOT_WINDOW_TITLE)
                if target_window is not None:
                    try:
                        target_window.restore()
                    except Exception:
                        pass
                    target_window.set_focus()
            except Exception:
                pass
        _send_keys(sequence)
    except Exception as exc:
        return UIActionResult(False, f"Impossible d'envoyer la touche {key}.", str(exc))
    return UIActionResult(
        True,
        f"Touche envoyée: {key}.",
        (
            f"Touche envoyée: {key}. Effet final non vérifié; "
            "réinspecter l'interface avant d'affirmer un changement."
        ),
    )
