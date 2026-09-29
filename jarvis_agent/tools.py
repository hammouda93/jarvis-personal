from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import unicodedata
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolIntent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    success: bool
    message: str
    detail: str = ""
    should_exit: bool = False


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", (text or "").lower().strip())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s'-]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()

    for prefix in ("hey jarvis ", "jarvis ", "jervis "):
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return value


def route(text: str) -> ToolIntent:
    cmd = normalize(text)

    if any(
        phrase in cmd
        for phrase in ("arrete jarvis", "eteins jarvis", "quitte jarvis")
    ):
        return ToolIntent("assistant.stop")

    if any(word in cmd for word in ("ouvre", "ouvrir", "lance", "affiche")):
        if "youtube" in cmd:
            return ToolIntent("browser.open_url", {"url": "https://www.youtube.com"})
        if "google" in cmd:
            return ToolIntent("browser.open_url", {"url": "https://www.google.com"})
        if "spotify" in cmd:
            return ToolIntent("app.open", {"app": "spotify"})
        if "chrome" in cmd:
            return ToolIntent("app.open", {"app": "chrome"})
        if "cursor" in cmd:
            return ToolIntent("app.open", {"app": "cursor"})
        if any(x in cmd for x in ("visual studio code", "vs code", "vscode")):
            return ToolIntent("app.open", {"app": "vscode"})
        if any(x in cmd for x in ("telechargements", "downloads")):
            return ToolIntent("folder.open", {"folder": "downloads"})

    if any(
        phrase in cmd
        for phrase in (
            "quelle heure est il",
            "quelle heure",
            "il est quelle heure",
            "donne moi l heure",
        )
    ):
        return ToolIntent("system.time")

    match = re.search(
        r"(?:recherche|cherche)\s+(.+?)(?:\s+sur\s+(?:internet|google|le web))?$",
        cmd,
    )
    if match and match.group(1).strip():
        return ToolIntent("browser.search", {"query": match.group(1).strip()})

    return ToolIntent("unknown", {"text": text})


def _spawn(candidates: list[str]) -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        target = str(path) if path.is_file() else shutil.which(candidate)
        if not target:
            continue
        subprocess.Popen(
            [target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    return False


def _open_application(app: str) -> ToolResult:
    local = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")

    if app == "spotify":
        try:
            os.startfile("spotify:")
            return ToolResult(True, "C'est fait.", "Spotify protocol")
        except OSError:
            webbrowser.open("https://open.spotify.com")
            return ToolResult(True, "J'ai ouvert Spotify dans le navigateur.")

    candidates: dict[str, list[str]] = {
        "chrome": [
            os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(local, "Google", "Chrome", "Application", "chrome.exe") if local else "",
            "chrome",
        ],
        "cursor": [
            os.path.join(local, "Programs", "cursor", "Cursor.exe") if local else "",
            os.path.join(local, "Programs", "Cursor", "Cursor.exe") if local else "",
            "cursor",
        ],
        "vscode": [
            os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe") if local else "",
            "code",
        ],
    }

    ok = _spawn(candidates.get(app, []))
    if ok:
        return ToolResult(True, "C'est fait.", f"Application ouverte: {app}")
    return ToolResult(False, f"Je n'ai pas trouvé {app}.", f"Application introuvable: {app}")


def execute(intent: ToolIntent) -> ToolResult:
    if intent.name == "assistant.stop":
        return ToolResult(True, "À bientôt monsieur.", should_exit=True)

    if intent.name == "browser.open_url":
        url = str(intent.args["url"])
        ok = webbrowser.open(url)
        return ToolResult(bool(ok), "C'est fait." if ok else "Je n'ai pas pu ouvrir le navigateur.", url)

    if intent.name == "browser.search":
        query = str(intent.args["query"])
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
        ok = webbrowser.open(url)
        return ToolResult(bool(ok), f"Je recherche {query}.", url)

    if intent.name == "app.open":
        return _open_application(str(intent.args["app"]))

    if intent.name == "folder.open":
        folder = str(intent.args["folder"])
        path = Path.home() / ("Downloads" if folder == "downloads" else folder)
        if path.exists():
            os.startfile(str(path))
            return ToolResult(True, "C'est fait.", str(path))
        return ToolResult(False, "Je n'ai pas trouvé ce dossier.", str(path))

    if intent.name == "system.time":
        now = dt.datetime.now()
        return ToolResult(True, f"Il est {now:%H} heures {now:%M}.", now.isoformat())

    return ToolResult(
        False,
        "Je vous ai entendu, mais je n'ai pas encore l'outil pour cette demande.",
        f"Intent non pris en charge: {intent.args.get('text', '')}",
    )
