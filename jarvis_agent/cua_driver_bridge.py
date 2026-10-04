from __future__ import annotations

import atexit
import difflib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from .config import settings
from .tools import normalize


@dataclass(frozen=True)
class CuaElement:
    token: str
    pid: int
    window_id: int
    role: str = ""
    label: str = ""
    value: str = ""
    enabled: bool | None = None
    selected: bool | None = None
    actions: tuple[str, ...] = ()
    frame: tuple[int, int, int, int] | None = None

    @property
    def writable(self) -> bool:
        role = normalize(self.role)
        actions = {normalize(item) for item in self.actions}
        return (
            "set value" in actions
            or "setvalue" in actions
            or "value" in actions
            or role in {
                "edit",
                "editable text",
                "entry",
                "search box",
                "searchbox",
                "text field",
                "textfield",
                "textbox",
            }
        )

    @property
    def actionable(self) -> bool:
        role = normalize(self.role)
        actions = {normalize(item) for item in self.actions}
        if actions:
            return True
        return role in {
            "button",
            "checkbox",
            "combo box",
            "combobox",
            "hyperlink",
            "link",
            "list item",
            "listitem",
            "menu item",
            "menuitem",
            "radio button",
            "radiobutton",
            "tab",
            "tab item",
            "tabitem",
            "tree item",
            "treeitem",
        }


@dataclass(frozen=True)
class CuaWindowSnapshot:
    pid: int
    window_id: int
    title: str
    app_name: str
    bounds: tuple[int, int, int, int]
    elements: tuple[CuaElement, ...]
    truncated: bool
    total_element_count: int
    returned_element_count: int
    degraded_reason: str = ""
    snapshot_id: str = ""
    capture_id: str = ""

    @property
    def has_meaningful_content(self) -> bool:
        """Reject snapshots that expose only title-bar/window chrome."""
        if any(item.writable for item in self.elements):
            return True

        chrome_labels = {
            "close",
            "fermer",
            "maximize",
            "maximise",
            "agrandir",
            "minimize",
            "minimise",
            "reduire",
            "réduire",
            "restore",
            "restaurer",
            "system",
            "systeme",
            "système",
        }
        content_roles = {
            "checkbox",
            "combo box",
            "combobox",
            "document",
            "edit",
            "entry",
            "hyperlink",
            "link",
            "list",
            "list item",
            "listitem",
            "menu",
            "radio button",
            "radiobutton",
            "search box",
            "searchbox",
            "tab",
            "tab item",
            "tabitem",
            "text field",
            "textfield",
            "textbox",
            "tree",
            "tree item",
            "treeitem",
        }
        left, top, right, bottom = self.bounds
        height = max(0, bottom - top)
        content_top = top + min(110, max(60, int(height * 0.14)))

        for item in self.elements:
            role = normalize(item.role)
            label = normalize(item.label)
            if label in chrome_labels:
                continue
            if role in content_roles:
                return True
            if item.actionable and item.frame is not None:
                if item.frame[3] > content_top:
                    return True
        return False


@dataclass(frozen=True)
class CuaActionResult:
    success: bool
    message: str
    detail: dict[str, Any]


class CuaDriverToolError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        recommended_delivery: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "")
        self.recommended_delivery = str(recommended_delivery or "")
        self.detail = dict(detail or {})


class CuaDriverBridge:
    """Optional persistent bridge to Cua Driver's local MCP server.

    Jarvis deliberately uses stdlib JSON-RPC instead of importing the Python
    SDK, so the existing Python 3.9 runtime remains supported. Element tokens
    stay valid because observe and act share one long-lived MCP process.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._stderr: threading.Thread | None = None
        self._seq = 0
        self._tools: set[str] = set()
        self._binary = ""
        self._last_error = ""
        atexit.register(self.close)

    @property
    def last_error(self) -> str:
        return self._last_error

    def executable(self) -> str:
        explicit = str(getattr(settings, "cua_driver_binary", "") or "").strip()
        if explicit:
            return explicit
        return shutil.which("cua-driver") or ""

    def available(self) -> bool:
        if not bool(getattr(settings, "cua_driver_enabled", True)):
            return False
        return bool(self.executable())

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is None:
            self._lines.put(None)
            return
        try:
            for line in stream:
                self._lines.put(line)
        finally:
            self._lines.put(None)

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[str]) -> None:
        stream = process.stderr
        if stream is None:
            return
        try:
            for _line in stream:
                pass
        except Exception:
            pass

    def _start_locked(self) -> None:
        if (
            self._process is not None
            and self._process.poll() is None
            and self._tools
        ):
            return

        self.close()
        binary = self.executable()
        if not binary:
            raise RuntimeError("cua-driver_not_installed")

        creationflags = 0
        if os.name == "nt":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

        env = dict(os.environ)
        env.setdefault("CUA_DRIVER_PERMISSION_MODE", "standard")
        process = subprocess.Popen(
            [binary, "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
        self._process = process
        self._binary = binary
        self._lines = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_stdout,
            args=(process,),
            name="JarvisCuaMcpStdout",
            daemon=True,
        )
        self._reader.start()
        self._stderr = threading.Thread(
            target=self._drain_stderr,
            args=(process,),
            name="JarvisCuaMcpStderr",
            daemon=True,
        )
        self._stderr.start()

        self._request_locked(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "jarvis-personal",
                    "version": "1",
                },
            },
        )
        self._notify_locked("notifications/initialized", {})
        listing = self._request_locked("tools/list", {})
        tools = listing.get("tools") if isinstance(listing, dict) else None
        self._tools = {
            str(item.get("name") or "")
            for item in (tools or [])
            if isinstance(item, dict) and item.get("name")
        }
        required = {"list_windows", "get_window_state", "click"}
        missing = required - self._tools
        if missing:
            self.close()
            raise RuntimeError(
                "cua-driver_missing_tools:" + ",".join(sorted(missing))
            )

    def _write_locked(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("cua-driver_transport_closed")
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _notify_locked(self, method: str, params: dict[str, Any]) -> None:
        self._write_locked(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    def _request_locked(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        self._seq += 1
        request_id = self._seq
        self._write_locked(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        timeout = float(
            timeout_s
            if timeout_s is not None
            else getattr(settings, "cua_driver_timeout_s", 6.0)
        )
        deadline = time.monotonic() + max(0.5, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"cua-driver_timeout:{method}")
            try:
                line = self._lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                process = self._process
                if process is None or process.poll() is not None:
                    raise RuntimeError("cua-driver_transport_closed")
                continue
            if line is None:
                raise RuntimeError("cua-driver_transport_closed")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") != request_id:
                continue
            if response.get("error"):
                raise RuntimeError(
                    "cua-driver_rpc_error:"
                    + json.dumps(response.get("error"), ensure_ascii=False)
                )
            result = response.get("result")
            return result if isinstance(result, dict) else {}

    def _call_locked(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        self._start_locked()
        if name not in self._tools:
            raise RuntimeError(f"cua-driver_tool_unavailable:{name}")
        result = self._request_locked(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_s=timeout_s,
        )
        if result.get("isError"):
            structured = result.get("structuredContent")
            structured = structured if isinstance(structured, dict) else {}
            refusal = structured.get("refusal")
            refusal = refusal if isinstance(refusal, dict) else {}
            escalation = structured.get("escalation")
            escalation = escalation if isinstance(escalation, dict) else {}
            code = str(
                structured.get("code")
                or refusal.get("code")
                or ""
            )
            recommended = str(escalation.get("recommended") or "")
            text_parts = [
                str(item.get("text") or "")
                for item in result.get("content", [])
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            raise CuaDriverToolError(
                "cua-driver_tool_error:"
                + (" ".join(part for part in text_parts if part).strip() or name),
                code=code,
                recommended_delivery=recommended,
                detail=structured,
            )
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            if structured.get("status") == "refused" or structured.get("refusal"):
                refusal = structured.get("refusal")
                refusal = refusal if isinstance(refusal, dict) else {}
                escalation = structured.get("escalation")
                escalation = escalation if isinstance(escalation, dict) else {}
                raise CuaDriverToolError(
                    "cua-driver_refused:"
                    + json.dumps(structured, ensure_ascii=False),
                    code=str(refusal.get("code") or structured.get("code") or ""),
                    recommended_delivery=str(
                        escalation.get("recommended") or ""
                    ),
                    detail=structured,
                )
            return structured
        texts = [
            str(item.get("text") or "")
            for item in result.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        for value in texts:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            try:
                result = self._call_locked(
                    name,
                    arguments,
                    timeout_s=timeout_s,
                )
                self._last_error = ""
                return result
            except Exception as exc:
                self._last_error = str(exc)[:500]
                # One recovery attempt for a dead MCP child. Never repeat a
                # mutating action because that could duplicate input.
                if name in {"list_windows", "get_window_state"}:
                    try:
                        self.close()
                        result = self._call_locked(
                            name,
                            arguments,
                            timeout_s=timeout_s,
                        )
                        self._last_error = ""
                        return result
                    except Exception as retry_exc:
                        self._last_error = str(retry_exc)[:500]
                raise

    @staticmethod
    def _window_score(title: str, app_name: str, query: str) -> float:
        wanted = normalize(query)
        if not wanted:
            return 0.0
        candidates = [normalize(title), normalize(app_name)]
        best = 0.0
        for candidate in candidates:
            if not candidate:
                continue
            if wanted == candidate:
                best = max(best, 1.0)
            elif wanted in candidate or candidate in wanted:
                best = max(best, 0.96)
            else:
                best = max(
                    best,
                    difflib.SequenceMatcher(None, wanted, candidate).ratio(),
                )
        return best

    def _resolve_window(self, title: str | None) -> dict[str, Any]:
        payload = self.call(
            "list_windows",
            {"on_screen_only": True},
        )
        windows = [
            item
            for item in (payload.get("windows") or [])
            if isinstance(item, dict)
            and item.get("pid")
            and item.get("window_id") is not None
            and normalize(str(item.get("title") or "")) != "jarvis personal"
        ]
        if not windows:
            raise RuntimeError("cua-driver_no_visible_windows")

        target = str(title or "").strip()
        if target:
            ranked = sorted(
                (
                    (
                        self._window_score(
                            str(item.get("title") or ""),
                            str(item.get("app_name") or ""),
                            target,
                        ),
                        item,
                    )
                    for item in windows
                ),
                key=lambda pair: -pair[0],
            )
            if not ranked or ranked[0][0] < 0.82:
                raise RuntimeError(f"cua-driver_window_not_found:{target}")
            if (
                len(ranked) > 1
                and ranked[0][0] - ranked[1][0] < 0.035
                and ranked[0][0] < 0.98
            ):
                raise RuntimeError(f"cua-driver_window_ambiguous:{target}")
            return ranked[0][1]

        z_ranked = [
            item
            for item in windows
            if isinstance(item.get("z_index"), int)
        ]
        if z_ranked:
            return max(z_ranked, key=lambda item: int(item["z_index"]))
        visible = [item for item in windows if item.get("is_on_screen") is not False]
        if len(visible) == 1:
            return visible[0]
        raise RuntimeError("cua-driver_active_window_ambiguous")

    @staticmethod
    def _element_frame(raw: Any) -> tuple[int, int, int, int] | None:
        if not isinstance(raw, dict):
            return None
        try:
            x = int(round(float(raw.get("x") or 0)))
            y = int(round(float(raw.get("y") or 0)))
            width = int(round(float(raw.get("w", raw.get("width")) or 0)))
            height = int(round(float(raw.get("h", raw.get("height")) or 0)))
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return (x, y, x + width, y + height)

    def _read_window_state(
        self,
        *,
        pid: int,
        window_id: int,
        max_elements: int,
        max_depth: int,
        timeout_ms: int,
    ) -> dict[str, Any]:
        return self.call(
            "get_window_state",
            {
                "pid": int(pid),
                "window_id": int(window_id),
                "include_screenshot": False,
                "max_elements": max(16, min(int(max_elements), 600)),
                "max_depth": max(2, min(int(max_depth), 16)),
                "timeout_ms": max(250, min(int(timeout_ms), 10000)),
            },
            timeout_s=max(
                float(getattr(settings, "cua_driver_timeout_s", 6.0)),
                (max(250, int(timeout_ms)) / 1000.0) + 2.0,
            ),
        )

    def launch_application(self, name: str) -> CuaActionResult:
        """Launch a Windows application through Driver's native app catalog.

        This is deliberately optional: if Driver is disabled/unavailable the
        caller keeps the existing Windows discovery path. A successful launch
        is grounded by Driver's returned pid, not by an optimistic shell call.
        """
        target = str(name or "").strip()
        if not target:
            return CuaActionResult(
                False,
                "Nom d'application vide.",
                {"provider": "cua_driver", "error": "empty_app_name"},
            )
        if not self.available():
            return CuaActionResult(
                False,
                "Cua Driver indisponible.",
                {"provider": "cua_driver", "error": "driver_unavailable"},
            )

        try:
            detail = self.call(
                "launch_app",
                {"name": target},
                timeout_s=max(
                    3.0,
                    float(getattr(settings, "cua_driver_timeout_s", 6.0)),
                ),
            )
        except Exception as exc:
            return CuaActionResult(
                False,
                f"Cua Driver n'a pas pu lancer {target}.",
                {
                    "provider": "cua_driver",
                    "error": str(exc)[:600],
                    "application": target,
                },
            )

        try:
            pid = int(detail.get("pid") or detail.get("process_id") or 0)
        except (TypeError, ValueError):
            pid = 0

        windows = [
            item
            for item in (detail.get("windows") or [])
            if isinstance(item, dict)
        ]

        # Driver launches Windows apps without stealing focus. For Jarvis'
        # explicit open_application primitive, fronting the target is the
        # intended user-visible effect. If the window is still materializing,
        # use bounded discovery instead of launching the app again.
        if pid > 0 and not windows and "list_windows" in self._tools:
            for attempt in range(3):
                try:
                    listed = self.call(
                        "list_windows",
                        {"pid": pid, "on_screen_only": False},
                        timeout_s=2.0,
                    )
                    windows = [
                        item
                        for item in (listed.get("windows") or [])
                        if isinstance(item, dict)
                    ]
                except Exception:
                    windows = []
                if windows:
                    break
                if attempt < 2:
                    time.sleep(0.15)

        foreground = False
        foreground_error = ""
        if pid > 0 and "bring_to_front" in self._tools:
            target_window = None
            if windows:
                with_z = [
                    item
                    for item in windows
                    if isinstance(item.get("z_index"), int)
                ]
                pool = with_z or windows
                if with_z:
                    target_window = max(
                        pool,
                        key=lambda item: int(item.get("z_index") or 0),
                    )
                else:
                    target_window = pool[0]
            bring_args: dict[str, Any] = {"pid": pid}
            if target_window is not None:
                try:
                    window_id = int(target_window.get("window_id") or 0)
                except (TypeError, ValueError):
                    window_id = 0
                if window_id > 0:
                    bring_args["window_id"] = window_id
            try:
                front = self.call(
                    "bring_to_front",
                    bring_args,
                    timeout_s=3.0,
                )
                foreground = bool(
                    front.get("now_fg_hwnd")
                    or front.get("success") is True
                    or front.get("effect") == "confirmed"
                )
            except Exception as exc:
                foreground_error = str(exc)[:400]

        success = pid > 0
        return CuaActionResult(
            success,
            (
                f"Application lancée via Cua Driver: {target}."
                if success
                else f"Cua Driver n'a pas confirmé le lancement de {target}."
            ),
            {
                "provider": "cua_driver",
                "application": target,
                "pid": pid or None,
                "name": detail.get("name"),
                "bundle_id": detail.get("bundle_id"),
                "windows": windows,
                "foreground": foreground,
                "foreground_error": foreground_error or None,
                "raw": detail,
            },
        )

    def inspect_window(
        self,
        title: str | None = None,
        *,
        max_elements: int = 240,
        max_depth: int = 8,
        timeout_ms: int = 2500,
    ) -> CuaWindowSnapshot:
        window = self._resolve_window(title)
        pid = int(window["pid"])
        window_id = int(window["window_id"])
        state = self._read_window_state(
            pid=pid,
            window_id=window_id,
            max_elements=max_elements,
            max_depth=max_depth,
            timeout_ms=timeout_ms,
        )
        elements: list[CuaElement] = []
        for raw in state.get("elements") or []:
            if not isinstance(raw, dict):
                continue
            token = str(raw.get("element_token") or "").strip()
            if not token:
                continue
            actions = tuple(
                str(item)
                for item in (raw.get("actions") or [])
                if str(item or "").strip()
            )
            elements.append(
                CuaElement(
                    token=token,
                    pid=pid,
                    window_id=window_id,
                    role=str(raw.get("role") or "")[:120],
                    label=str(
                        raw.get("label")
                        or raw.get("title")
                        or raw.get("name")
                        or ""
                    )[:300],
                    value=str(raw.get("value") or "")[:1000],
                    enabled=(
                        bool(raw["enabled"])
                        if raw.get("enabled") is not None
                        else None
                    ),
                    selected=(
                        bool(raw["selected"])
                        if raw.get("selected") is not None
                        else None
                    ),
                    actions=actions,
                    frame=self._element_frame(raw.get("frame")),
                )
            )

        bounds_raw = window.get("bounds") or {}
        try:
            left = int(round(float(bounds_raw.get("x") or 0)))
            top = int(round(float(bounds_raw.get("y") or 0)))
            width = int(round(float(bounds_raw.get("width") or 0)))
            height = int(round(float(bounds_raw.get("height") or 0)))
            bounds = (left, top, left + width, top + height)
        except (TypeError, ValueError):
            bounds = (0, 0, 0, 0)

        snapshot = CuaWindowSnapshot(
            pid=pid,
            window_id=window_id,
            title=str(
                state.get("window_title")
                or window.get("title")
                or ""
            )[:300],
            app_name=str(
                state.get("app_name")
                or window.get("app_name")
                or ""
            )[:200],
            bounds=bounds,
            elements=tuple(elements),
            truncated=bool(state.get("truncated")),
            total_element_count=int(
                state.get("total_element_count")
                or len(elements)
            ),
            returned_element_count=int(
                state.get("returned_element_count")
                or len(elements)
            ),
            degraded_reason=str(state.get("degraded_reason") or "")[:500],
            snapshot_id=str(state.get("snapshot_id") or "")[:160],
            capture_id=str(state.get("capture_id") or "")[:160],
        )

        if not snapshot.has_meaningful_content:
            # OpenClaw/Cua Driver's Windows workflow retries one fresh window
            # snapshot when the accessibility surface is sparse. A second
            # sparse snapshot is preserved as truthful evidence; we do not
            # invent keyboard shortcuts or pretend content was found.
            retry_state = self._read_window_state(
                pid=pid,
                window_id=window_id,
                max_elements=max_elements,
                max_depth=max_depth,
                timeout_ms=timeout_ms,
            )
            if retry_state != state:
                state = retry_state
                elements = []
                for raw in state.get("elements") or []:
                    if not isinstance(raw, dict):
                        continue
                    token = str(raw.get("element_token") or "").strip()
                    if not token:
                        continue
                    actions = tuple(
                        str(item)
                        for item in (raw.get("actions") or [])
                        if str(item or "").strip()
                    )
                    elements.append(
                        CuaElement(
                            token=token,
                            pid=pid,
                            window_id=window_id,
                            role=str(raw.get("role") or "")[:120],
                            label=str(
                                raw.get("label")
                                or raw.get("title")
                                or raw.get("name")
                                or ""
                            )[:300],
                            value=str(raw.get("value") or "")[:1000],
                            enabled=(
                                bool(raw["enabled"])
                                if raw.get("enabled") is not None
                                else None
                            ),
                            selected=(
                                bool(raw["selected"])
                                if raw.get("selected") is not None
                                else None
                            ),
                            actions=actions,
                            frame=self._element_frame(raw.get("frame")),
                        )
                    )
                snapshot = CuaWindowSnapshot(
                    pid=pid,
                    window_id=window_id,
                    title=str(
                        state.get("window_title")
                        or window.get("title")
                        or ""
                    )[:300],
                    app_name=str(
                        state.get("app_name")
                        or window.get("app_name")
                        or ""
                    )[:200],
                    bounds=bounds,
                    elements=tuple(elements),
                    truncated=bool(state.get("truncated")),
                    total_element_count=int(
                        state.get("total_element_count")
                        or len(elements)
                    ),
                    returned_element_count=int(
                        state.get("returned_element_count")
                        or len(elements)
                    ),
                    degraded_reason=str(
                        state.get("degraded_reason") or ""
                    )[:500],
                    snapshot_id=str(state.get("snapshot_id") or "")[:160],
                    capture_id=str(state.get("capture_id") or "")[:160],
                )

        return snapshot


    def click_element(
        self,
        element: CuaElement,
        *,
        delivery_mode: str = "background",
    ) -> CuaActionResult:
        mode = normalize(delivery_mode or "background")
        if mode not in {"background", "foreground"}:
            mode = "background"
        try:
            detail = self.call(
                "click",
                {
                    "pid": int(element.pid),
                    "element_token": element.token,
                    "delivery_mode": mode,
                },
            )
        except CuaDriverToolError as exc:
            return CuaActionResult(
                False,
                "Cua Driver a refusé ou n'a pas pu confirmer cette action.",
                {
                    "provider": "cua_driver",
                    "error": str(exc)[:600],
                    "code": exc.code or None,
                    "recommended_delivery": exc.recommended_delivery or None,
                    "delivery_mode": mode,
                    "requires_fresh_inspection": True,
                    "raw": exc.detail,
                },
            )
        except Exception as exc:
            return CuaActionResult(
                False,
                "Cua Driver n'a pas pu activer ce contrôle.",
                {
                    "provider": "cua_driver",
                    "error": str(exc)[:600],
                    "delivery_mode": mode,
                    "requires_fresh_inspection": True,
                },
            )
        effect = normalize(str(detail.get("effect") or ""))
        success = (
            effect in {"confirmed", "unverifiable", "partial"}
            or detail.get("success") is True
        )
        return CuaActionResult(
            success,
            (
                "Contrôle activé via Cua Driver."
                if success
                else "Cua Driver n'a pas confirmé l'action."
            ),
            {
                "provider": "cua_driver",
                "delivery_mode": mode,
                "effect": detail.get("effect"),
                "route": detail.get("route"),
                "unverifiable": bool(detail.get("unverifiable")),
                "partial": bool(detail.get("partial")),
                "requires_fresh_inspection": True,
                "raw": detail,
            },
        )

    def write_element(
        self,
        element: CuaElement,
        text: str,
        *,
        mode: str = "replace",
        delivery_mode: str = "background",
    ) -> CuaActionResult:
        value = str(text or "")
        normalized_mode = normalize(mode or "replace")
        delivery = normalize(delivery_mode or "background")
        if delivery not in {"background", "foreground"}:
            delivery = "background"

        tool = "type_text"
        arguments: dict[str, Any]
        if (
            normalized_mode in {"replace", "append"}
            and "set_value" in self._tools
        ):
            tool = "set_value"
            desired = (
                value
                if normalized_mode == "replace"
                else str(element.value or "") + value
            )
            arguments = {
                "pid": int(element.pid),
                "element_token": element.token,
                "value": desired,
            }
        else:
            arguments = {
                "pid": int(element.pid),
                "element_token": element.token,
                "text": value,
                "delivery_mode": delivery,
            }

        try:
            detail = self.call(tool, arguments)
        except CuaDriverToolError as exc:
            return CuaActionResult(
                False,
                "Cua Driver a refusé ou n'a pas pu confirmer cette saisie.",
                {
                    "provider": "cua_driver",
                    "tool": tool,
                    "error": str(exc)[:600],
                    "code": exc.code or None,
                    "recommended_delivery": exc.recommended_delivery or None,
                    "delivery_mode": delivery,
                    "requires_fresh_inspection": True,
                    "raw": exc.detail,
                },
            )
        except Exception as exc:
            return CuaActionResult(
                False,
                "Cua Driver n'a pas pu saisir le texte.",
                {
                    "provider": "cua_driver",
                    "tool": tool,
                    "error": str(exc)[:600],
                    "delivery_mode": delivery,
                    "requires_fresh_inspection": True,
                },
            )

        effect = normalize(str(detail.get("effect") or ""))
        success = (
            effect in {"confirmed", "unverifiable", "partial"}
            or detail.get("success") is True
        )
        return CuaActionResult(
            success,
            (
                "Texte saisi via Cua Driver."
                if success
                else "Cua Driver n'a pas confirmé la saisie."
            ),
            {
                "provider": "cua_driver",
                "tool": tool,
                "mode": normalized_mode,
                "delivery_mode": delivery,
                "effect": detail.get("effect"),
                "route": detail.get("route"),
                "unverifiable": bool(detail.get("unverifiable")),
                "partial": bool(detail.get("partial")),
                "requires_fresh_inspection": True,
                "raw": detail,
            },
        )

    def close(self) -> None:
        process = self._process
        self._process = None
        self._tools = set()
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        try:
            process.terminate()
            process.wait(timeout=1.5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


CUA_DRIVER = CuaDriverBridge()
