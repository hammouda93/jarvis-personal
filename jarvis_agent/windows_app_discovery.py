from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class WindowsAppCandidate:
    name: str
    source: str
    score: float
    app_id: str = ""
    executable: str = ""

    @property
    def launch_kind(self) -> str:
        if self.app_id:
            return "aumid"
        if self.executable:
            return "executable"
        return "unknown"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "score": round(float(self.score), 4),
            "app_id": self.app_id,
            "executable": self.executable,
            "launch_kind": self.launch_kind,
        }


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower().strip())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s.-]", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def score_application_name(query: str, candidate: str) -> float:
    """Score an application name conservatively enough to fail closed.

    Substring matches are useful for related product names but dangerous when
    a very short executable name happens to occur inside an unrelated long
    request. Treat containment as a strong match only when the shorter name
    represents a substantial part of the longer one.
    """
    wanted = _normalize(query)
    name = _normalize(candidate)
    if not wanted or not name:
        return 0.0
    if wanted == name:
        return 1.0

    wanted_tokens = tuple(token for token in wanted.split() if len(token) >= 2)
    name_tokens = set(name.split())
    if wanted_tokens and all(token in name_tokens for token in wanted_tokens):
        return 0.97

    if wanted in name or name in wanted:
        shorter = min(len(wanted), len(name))
        longer = max(len(wanted), len(name))
        coverage = shorter / max(1, longer)
        if shorter >= 4 and coverage >= 0.60:
            return 0.94

    return difflib.SequenceMatcher(None, wanted, name).ratio()


def _score_name(query: str, candidate: str) -> float:
    return score_application_name(query, candidate)


_START_APPS_CACHE: tuple[float, tuple[tuple[str, str], ...]] = (0.0, ())
_START_APPS_TTL_S = 30.0


def _default_powershell_runner(script: str, timeout_s: float = 4.0) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(0.5, float(timeout_s)),
        creationflags=flags,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return str(completed.stdout or "").strip()


def _start_apps(
    *,
    runner: Callable[[str, float], str] | None = None,
    force_refresh: bool = False,
) -> tuple[tuple[str, str], ...]:
    global _START_APPS_CACHE

    now = time.monotonic()
    cached_at, cached = _START_APPS_CACHE
    if (
        runner is None
        and not force_refresh
        and cached
        and now - cached_at <= _START_APPS_TTL_S
    ):
        return cached

    command_runner = runner or _default_powershell_runner
    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
    )
    try:
        raw = command_runner(script, 4.0)
    except (OSError, subprocess.SubprocessError, TimeoutError):
        return ()

    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()

    rows = parsed if isinstance(parsed, list) else [parsed]
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or row.get("name") or "").strip()
        app_id = str(
            row.get("AppID")
            or row.get("AppId")
            or row.get("appid")
            or ""
        ).strip()
        if not name or not app_id:
            continue
        key = (name.casefold(), app_id.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append((name, app_id))

    value = tuple(result)
    if runner is None and value:
        _START_APPS_CACHE = (now, value)
    return value


def installed_start_app_names(
    *,
    runner: Callable[[str, float], str] | None = None,
    limit: int = 120,
) -> tuple[str, ...]:
    names = {
        name.strip()
        for name, _app_id in _start_apps(runner=runner)
        if name.strip()
    }
    return tuple(
        sorted(
            names,
            key=lambda value: (len(value), value.casefold()),
        )[: max(1, min(int(limit), 300))]
    )


def discover_start_apps(
    query: str,
    *,
    runner: Callable[[str, float], str] | None = None,
) -> tuple[WindowsAppCandidate, ...]:
    candidates: list[WindowsAppCandidate] = []
    for name, app_id in _start_apps(runner=runner):
        score = _score_name(query, name)
        if score < 0.72:
            continue
        candidates.append(
            WindowsAppCandidate(
                name=name,
                source="start_apps",
                score=score,
                app_id=app_id,
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda item: (-item.score, len(item.name), item.name.casefold()),
        )
    )


def _registry_app_paths() -> tuple[tuple[str, str], ...]:
    if os.name != "nt":
        return ()
    try:
        import winreg
    except ImportError:
        return ()

    root_specs = (
        (winreg.HKEY_CURRENT_USER, 0),
        (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_64KEY", 0)),
        (winreg.HKEY_LOCAL_MACHINE, getattr(winreg, "KEY_WOW64_32KEY", 0)),
    )
    key_path = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for root, view_flag in root_specs:
        access = winreg.KEY_READ | view_flag
        try:
            with winreg.OpenKey(root, key_path, 0, access) as parent:
                subkey_count = winreg.QueryInfoKey(parent)[0]
                for index in range(subkey_count):
                    try:
                        subkey_name = winreg.EnumKey(parent, index)
                        with winreg.OpenKey(
                            parent,
                            subkey_name,
                            0,
                            access,
                        ) as child:
                            executable, _kind = winreg.QueryValueEx(child, None)
                    except OSError:
                        continue
                    executable = os.path.expandvars(
                        str(executable or "").strip().strip('"')
                    )
                    if not executable:
                        continue
                    key = executable.casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append((Path(subkey_name).stem, executable))
        except OSError:
            continue

    return tuple(found)


def discover_app_paths(query: str) -> tuple[WindowsAppCandidate, ...]:
    candidates: list[WindowsAppCandidate] = []
    for name, executable in _registry_app_paths():
        score = max(
            _score_name(query, name),
            _score_name(query, Path(executable).stem),
        )
        if score < 0.72:
            continue
        candidates.append(
            WindowsAppCandidate(
                name=name,
                source="app_paths",
                score=score,
                executable=executable,
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda item: (-item.score, len(item.name), item.name.casefold()),
        )
    )


def resolve_registered_app(
    query: str,
    *,
    runner: Callable[[str, float], str] | None = None,
) -> tuple[WindowsAppCandidate | None, tuple[WindowsAppCandidate, ...]]:
    candidates = [
        *discover_start_apps(query, runner=runner),
        *discover_app_paths(query),
    ]

    deduped: dict[tuple[str, str, str], WindowsAppCandidate] = {}
    for item in candidates:
        key = (
            item.name.casefold(),
            item.app_id.casefold(),
            item.executable.casefold(),
        )
        previous = deduped.get(key)
        if previous is None or item.score > previous.score:
            deduped[key] = item

    ranked = tuple(
        sorted(
            deduped.values(),
            key=lambda item: (
                -item.score,
                0 if item.source == "start_apps" else 1,
                len(item.name),
                item.name.casefold(),
            ),
        )
    )
    if not ranked:
        return None, ()

    best = ranked[0]
    if best.score < 0.88:
        return None, ranked[:5]

    if len(ranked) > 1:
        second = ranked[1]
        if best.score < 0.999 and second.score >= best.score - 0.025:
            return None, ranked[:5]

    return best, ranked[:5]


def launch_registered_app(candidate: WindowsAppCandidate) -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if candidate.app_id:
            subprocess.Popen(
                [
                    "explorer.exe",
                    "shell:AppsFolder\\" + candidate.app_id,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            return True

        if candidate.executable:
            executable = os.path.expandvars(candidate.executable)
            subprocess.Popen(
                [executable],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            return True
    except OSError:
        return False

    return False
