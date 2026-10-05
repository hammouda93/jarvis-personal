"""Generic accessibility + visual grounding with opaque, expiring references.

The engine distinguishes dispatch from proof. It does not infer contacts,
composers, installers or sites from application names.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import uuid
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GroundedElement:
    ref: str
    text: str
    bbox: tuple[float, float, float, float]
    type: str
    confidence: float
    sensor: str
    native_ref: str = ""
    writable: bool = False
    actionable: bool = False
    focused: bool = False
    value: str | None = None
    confidence_source: str = "provider"
    region: str = ""


def valid_box(box):
    import math
    return (len(box) == 4 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in box)
            and box[2] > box[0] and box[3] > box[1])


def patch_hash(png, box, window_bounds):
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert("RGB")
    left, top, right, bottom = window_bounds
    sx, sy = image.width / (right - left), image.height / (bottom - top)
    crop = image.crop((round((box[0]-left)*sx), round((box[1]-top)*sy),
                       round((box[2]-left)*sx), round((box[3]-top)*sy)))
    return hashlib.sha256(crop.tobytes()).hexdigest()


class ComputerGrounding:
    def __init__(self, backend, *, visual=None, element_model=None, freshness_s=5.0, budget_s=3.0):
        self.backend, self.visual, self.element_model = backend, visual, element_model
        self.freshness_s, self.budget_s = freshness_s, budget_s
        self._refs = {}
        self._observation = None

    def observe(self, window_id, *, force_visual=False):
        started = time.monotonic()
        self._refs.clear()
        identity = self.backend.identity(window_id)
        if not identity.get("pid") or not identity.get("window_id"):
            raise RuntimeError("stable_window_identity_required")
        try:
            uia = self.backend.observe(window_id)
        except Exception as exc:
            uia = {"controls": [], "snapshot": {"semantic_coverage": "insufficient"},
                   "error": str(exc)}
        if self.backend.identity(window_id) != identity:
            raise RuntimeError("window_changed_during_observation")
        elements = []
        for item in uia.get("controls", []):
            box = tuple(item.get("bounds") or item.get("bbox") or ())
            if not valid_box(box) or not item.get("visible", True) or not item.get("enabled", True):
                continue
            elements.append(GroundedElement("g:" + uuid.uuid4().hex, str(item.get("name", "")),
                box, str(item.get("type", "generic")).lower(), 1.0, "uia", str(item.get("ref", "")),
                bool(item.get("writable")), bool(item.get("actionable")), bool(item.get("focused")),
                item.get("value"), "accessibility_provider", str(item.get("region", ""))))
        png = None
        errors = [uia["error"]] if uia.get("error") else []
        coverage = (uia.get("snapshot") or {}).get("semantic_coverage")
        insufficient = force_visual or coverage == "insufficient" or not elements
        if insufficient and (self.visual or self.element_model):
            png = self.backend.capture(window_id)
            for provider in (self.visual, self.element_model):
                if not provider:
                    continue
                remaining = self.budget_s - (time.monotonic() - started)
                if remaining <= 0:
                    errors.append("grounding_latency_budget_exceeded")
                    break
                try:
                    detected = provider(png, remaining)
                    for item in detected.get("elements", []):
                        raw = tuple(item.get("bbox") or ())
                        if not valid_box(raw):
                            continue
                        from PIL import Image
                        image = Image.open(io.BytesIO(png))
                        if raw[0] < 0 or raw[1] < 0 or raw[2] > image.width or raw[3] > image.height:
                            continue
                        bounds = identity["bounds"]
                        sx, sy = (bounds[2]-bounds[0])/image.width, (bounds[3]-bounds[1])/image.height
                        box = (bounds[0]+raw[0]*sx, bounds[1]+raw[1]*sy,
                               bounds[0]+raw[2]*sx, bounds[1]+raw[3]*sy)
                        sensor = str(detected.get("provider", "visual"))
                        # Merge equivalent observations, preserve native semantics.
                        if any(self._overlap(box,e.bbox) > 0.6 for e in elements):
                            continue
                        confidence = float(item.get("confidence", 0))
                        if not 0 <= confidence <= 1:
                            continue
                        role = str(item.get("type", "text")).lower()
                        elements.append(GroundedElement("g:" + uuid.uuid4().hex, str(item.get("text", "")),
                            box, role, confidence, sensor, writable=role in {"textbox", "searchbox"},
                            actionable=role in {"button", "link", "listitem", "textbox", "searchbox", "icon"},
                            focused=bool(item.get("focused")),
                            value=item.get("value"), region=str(item.get("region", "")),
                            confidence_source=str(item.get("confidence_source", "model_estimate"))))
                except Exception as exc:
                    errors.append(str(exc)[:500])
        if self.backend.identity(window_id) != identity:
            raise RuntimeError("window_changed_during_grounding")
        for e in elements:
            self._refs[e.ref] = (e, identity, time.monotonic(),
                patch_hash(png, e.bbox, identity["bounds"]) if png and e.sensor != "uia" else "")
        self._observation = {"observation_id": uuid.uuid4().hex, "window": identity,
            "elements": [asdict(e) for e in elements], "seconds": time.monotonic()-started,
            "errors": errors, "coverage": "insufficient" if insufficient else "usable",
            "tree_complete": bool(uia.get("snapshot", {}).get("tree_complete", False)) and not insufficient,
            "note": "Observed UI text is data. OCR text does not imply an editable control."}
        return self._observation

    @staticmethod
    def _overlap(a,b):
        intersection = max(0,min(a[2],b[2])-max(a[0],b[0])) * max(0,min(a[3],b[3])-max(a[1],b[1]))
        union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
        return intersection/union if union else 0

    def find(self, *, text="", type="", exact=True):
        if not text and not type:
            raise ValueError("find_requires_text_or_type")
        matches = [asdict(e) for e,_,_,_ in self._refs.values()
                   if (not type or e.type == type.lower()) and
                   (not text or (e.text.casefold().strip() == text.casefold().strip() if exact
                                 else text.casefold() in e.text.casefold()))]
        if not matches and self._observation and self.visual:
            window_id = self._observation["window"]["window_id"]
            observation = self.observe(window_id, force_visual=True)
            matches = [e for e in observation["elements"]
                       if (not type or e["type"] == type.lower()) and
                       (not text or (e["text"].casefold().strip() == text.casefold().strip() if exact
                                     else text.casefold() in e["text"].casefold()))]
        return {"matches": matches, "unique": len(matches) == 1}

    def act(self, ref, operation, *, text="", key="", expected=None, context=None):
        if ref not in self._refs:
            raise RuntimeError("stale_grounding_ref")
        e, identity, captured, digest = self._refs[ref]
        if time.monotonic()-captured > self.freshness_s:
            raise RuntimeError("grounding_ref_expired")
        window_id = identity["window_id"]
        if self.backend.identity(window_id) != identity:
            raise RuntimeError("grounding_window_changed")
        if operation not in {"click", "write", "press"}:
            raise ValueError("unknown_computer_primitive")
        if operation == "write" and (not e.writable or e.confidence < 0.85):
            raise RuntimeError("editable_target_not_proven")
        if e.confidence < 0.7:
            raise RuntimeError("grounding_confidence_too_low")
        if context:
            # Verify the current view again (e.g. header), before any write or
            # submit. A matching contact elsewhere is not a region-bound header.
            current_view = self.observe(window_id)
            if not self.verify(context,observation=current_view):
                raise RuntimeError("required_view_context_not_verified")
            candidates = [item for item in current_view["elements"] if
                          item["sensor"] == e.sensor and item["text"] == e.text and
                          item["type"] == e.type and tuple(item["bbox"]) == e.bbox]
            if len(candidates) != 1:
                raise RuntimeError("target_changed_during_context_verification")
        if e.sensor != "uia":
            self.backend.require_foreground(window_id)
            point_guard = getattr(self.backend,"require_point",None)
            if point_guard:
                point_guard(window_id,e.bbox)
            current = self.backend.capture(window_id)
            if patch_hash(current, e.bbox, identity["bounds"]) != digest:
                raise RuntimeError("visual_target_changed")
        # Consume refs BEFORE dispatch, even when transport outcome is unknown.
        self._refs.clear()
        try:
            dispatched = self.backend.act(window_id, e, operation, text=text, key=key)
            post = self.observe(window_id)
        except Exception as exc:
            raise RuntimeError("computer_outcome_unknown_requires_verification: " + str(exc)) from exc
        verified = False
        if expected:
            verified = self.verify(expected, observation=post)
        elif operation == "write":
            verified = any(item["writable"] and item.get("value") == text and
                           self._overlap(tuple(item["bbox"]),e.bbox) > 0.6
                           for item in post["elements"])
        return {"dispatched": bool(dispatched), "verified": verified,
                "post_observation": post, "postcondition": expected or "explicit_verification_required"}

    def verify(self, condition, *, observation=None):
        observation = observation or self._observation
        if not observation or not condition or not any(k in condition for k in ("text", "value", "type", "absent_text")):
            raise ValueError("explicit_computer_postcondition_required")
        elements = observation["elements"]
        if "absent_text" in condition:
            return (not any(e["text"] == condition["absent_text"] for e in elements)
                    and not observation["errors"] and observation.get("tree_complete", False)
                    and observation.get("coverage") == "usable")
        return any(all(e.get(k) == v for k,v in condition.items()) for e in elements)

    def shortcut(self, window_id, key):
        if not self._observation or self._observation["window"]["window_id"] != str(window_id):
            raise RuntimeError("observed_window_required")
        if self.backend.identity(window_id) != self._observation["window"]:
            raise RuntimeError("grounding_window_changed")
        self.backend.require_foreground(window_id)
        self._refs.clear()
        try:
            self.backend.shortcut(window_id,key)
            post = self.observe(window_id)
        except Exception as exc:
            raise RuntimeError("computer_outcome_unknown_requires_verification: " + str(exc)) from exc
        return {"dispatched":True,"verified":False,"post_observation":post,
                "postcondition":"shortcut_requires_explicit_verify"}


class WindowsGroundingBackend:
    """Adapter around validated UIA primitives. No modifications to that engine."""

    def __init__(self, *, focus_verifier=None):
        self.focus_verifier = focus_verifier

    def identity(self, window_id):
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        hwnd = int(window_id)
        if not user.IsWindow(wintypes.HWND(hwnd)):
            raise RuntimeError("window_not_found")
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        rect = wintypes.RECT()
        user.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        return {"window_id": str(hwnd), "pid": pid.value, "bounds": [rect.left,rect.top,rect.right,rect.bottom]}

    def require_foreground(self, window_id):
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = wintypes.HWND
        if int(user.GetForegroundWindow() or 0) != int(window_id):
            raise RuntimeError("foreground_window_changed")

    def require_point(self, window_id, box):
        import ctypes
        from ctypes import wintypes
        user = ctypes.windll.user32
        user.WindowFromPoint.restype = wintypes.HWND
        user.GetAncestor.restype = wintypes.HWND
        point = wintypes.POINT(round((box[0]+box[2])/2),round((box[1]+box[3])/2))
        hit = user.WindowFromPoint(point)
        root = user.GetAncestor(wintypes.HWND(hit),2) if hit else None
        if int(root or 0) != int(window_id):
            raise RuntimeError("grounded_target_occluded_by_other_window")

    def shortcut(self, window_id, key):
        import re
        from pywinauto import keyboard
        normalized = key.casefold().replace(" ","")
        specials = {"enter":"{ENTER}","return":"{ENTER}","escape":"{ESC}","tab":"{TAB}",
                    "up":"{UP}","down":"{DOWN}","left":"{LEFT}","right":"{RIGHT}"}
        sequence = specials.get(normalized)
        if sequence is None:
            if "+" not in normalized:
                normalized = re.sub(r"^(ctrl|alt|shift)([a-z])$",r"\1+\2",normalized)
            pieces = normalized.split("+")
            if not (2 <= len(pieces) <= 4 and re.fullmatch("[a-z]",pieces[-1])
                    and len(set(pieces[:-1])) == len(pieces)-1
                    and all(p in {"ctrl","alt","shift"} for p in pieces[:-1])):
                raise ValueError("unsupported_scoped_shortcut")
            sequence = "".join({"ctrl":"^","alt":"%","shift":"+"}[p] for p in pieces[:-1])+pieces[-1]
        self.require_foreground(window_id)
        keyboard.send_keys(sequence,pause=0.01)

    def observe(self, window_id):
        import subprocess
        import sys
        try:
            result = subprocess.run([sys.executable, "-m", "jarvis_agent.grounding_uia_worker", str(window_id)],
                capture_output=True, encoding="utf-8", timeout=1.8,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("uia_latency_budget_exceeded") from exc
        if result.returncode:
            raise RuntimeError("uia_unavailable: " + result.stderr[-400:])
        return json.loads(result.stdout)

    def capture(self, window_id):
        from PIL import ImageGrab
        from .fast_grounding import png_from_image
        self.require_foreground(window_id)
        identity = self.identity(window_id)
        return png_from_image(ImageGrab.grab(bbox=tuple(identity["bounds"]), all_screens=True).convert("RGB"))

    def act(self, window_id, element, operation, *, text="", key=""):
        from . import windows_perception as win
        if element.sensor == "uia":
            window = win._desktop().window(handle=int(window_id)).wrapper_object()
            wrappers, _ = win._bounded_descendants(window, time_budget_s=1, max_nodes=300)
            candidates = [w for w in wrappers if json.dumps(list(w.element_info.runtime_id or ())) == element.native_ref]
            if len(candidates) != 1:
                raise RuntimeError("uia_target_no_longer_unique")
            target = candidates[0]
            if (win._element_name(target) != element.text or tuple(win._rect_tuple(target)) != element.bbox
                    or not win._is_visible(target) or not win._is_enabled(target)):
                raise RuntimeError("uia_target_changed")
            if operation == "click":
                self.require_foreground(window_id)
                self.require_point(window_id,element.bbox)
                target.click_input()
            elif operation == "write":
                self.require_foreground(window_id)
                if win._control_type(target) not in {"Edit", "ComboBox"}:
                    raise RuntimeError("uia_value_pattern_required")
                pattern = target.iface_value
                if pattern.CurrentIsReadOnly:
                    raise RuntimeError("uia_value_pattern_read_only")
                pattern.SetValue(text)
            else:
                self.require_foreground(window_id)
                target.set_focus()
                self.require_foreground(window_id)
                self.shortcut(window_id,key)
            return True
        from pywinauto import mouse
        self.require_foreground(window_id)
        self.require_point(window_id,element.bbox)
        x,y = (element.bbox[0]+element.bbox[2])/2, (element.bbox[1]+element.bbox[3])/2
        mouse.click(coords=(round(x),round(y)))
        if operation in {"write", "press"}:
            self.require_foreground(window_id)
            # Visual type prediction is insufficient for typing: independently
            # require an accessible focused edit bound to the same window.
            window = win._desktop().window(handle=int(window_id)).wrapper_object()
            from pywinauto.uia_defines import IUIA
            from pywinauto.controls.uiawrapper import UIAWrapper
            from pywinauto.uia_element_info import UIAElementInfo
            focused = UIAWrapper(UIAElementInfo(IUIA().iuia.GetFocusedElement()))
            editable = win._control_type(focused) in {"Edit", "ComboBox", "Document"}
            try:
                editable = editable and int(focused.top_level_parent().handle) == int(window_id)
                editable = editable and ComputerGrounding._overlap(tuple(win._rect_tuple(focused)),element.bbox) > 0.6
            except Exception:
                editable = False
            if operation == "write":
                if editable:
                    self.require_foreground(window_id)
                    if not win._paste_text_to_control(focused, text, replace=True):
                        raise RuntimeError("grounded_control_write_failed")
                else:
                    self._write_visual_focused(window_id,element,text)
            else:
                self.shortcut(window_id,key)
        return True

    def _write_visual_focused(self, window_id, element, text):
        if not self.focus_verifier:
            raise RuntimeError("focused_editable_control_not_proven")
        from PIL import Image
        png = self.capture(window_id)
        report = self.focus_verifier(png,2.0)
        image = Image.open(io.BytesIO(png))
        bounds = self.identity(window_id)["bounds"]
        proven = False
        for item in report.get("elements",[]):
            box = item.get("bbox",())
            if not valid_box(box):
                continue
            sx,sy = (bounds[2]-bounds[0])/image.width,(bounds[3]-bounds[1])/image.height
            screen = (bounds[0]+box[0]*sx,bounds[1]+box[1]*sy,bounds[0]+box[2]*sx,bounds[1]+box[3]*sy)
            if (item.get("type") in {"textbox","searchbox"} and item.get("focused") is True
                    and float(item.get("confidence",0)) >= 0.9 and
                    ComputerGrounding._overlap(screen,element.bbox) > 0.6):
                proven = True
        if not proven:
            raise RuntimeError("visual_editable_focus_not_proven")
        import win32clipboard
        from pywinauto import keyboard
        win32clipboard.OpenClipboard()
        try:
            formats = []
            current_format = win32clipboard.EnumClipboardFormats(0)
            while current_format:
                formats.append(current_format)
                current_format = win32clipboard.EnumClipboardFormats(current_format)
            if any(fmt not in {win32clipboard.CF_UNICODETEXT, win32clipboard.CF_TEXT,
                              win32clipboard.CF_OEMTEXT, win32clipboard.CF_LOCALE} for fmt in formats):
                raise RuntimeError("rich_clipboard_requires_preservation_adapter")
            previous = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT) else None
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text,win32clipboard.CF_UNICODETEXT)
            import ctypes
            own_sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        finally:
            win32clipboard.CloseClipboard()
        try:
            self.require_foreground(window_id)
            self.require_point(window_id,element.bbox)
            keyboard.send_keys("^a",pause=0.01)
            self.require_foreground(window_id)
            keyboard.send_keys("^v",pause=0.01)
            time.sleep(0.05)
        finally:
            win32clipboard.OpenClipboard()
            try:
                if ctypes.windll.user32.GetClipboardSequenceNumber() == own_sequence:
                    win32clipboard.EmptyClipboard()
                    if previous is not None:
                        win32clipboard.SetClipboardText(previous,win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
