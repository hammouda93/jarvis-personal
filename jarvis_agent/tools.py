from __future__ import annotations

import datetime as dt
import difflib
import json
import os
from functools import lru_cache
import re
import shutil
import subprocess
import unicodedata
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .windows_app_discovery import (
    installed_start_app_names,
    launch_registered_app,
    resolve_registered_app,
)


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
    end_session: bool = False
    follow_up: str | None = None


_LAST_BROWSER_URL = ""


def _remember_browser_url(url: str) -> None:
    global _LAST_BROWSER_URL
    value = str(url or "").strip()
    if value:
        _LAST_BROWSER_URL = value


def _browser_window_hint(context_url: str = "") -> str:
    raw = str(context_url or _LAST_BROWSER_URL or "").strip()
    try:
        host = urllib.parse.urlparse(raw).netloc.casefold()
    except Exception:
        host = ""
    if host.endswith("youtube.com"):
        return "YouTube"
    if host == "web.whatsapp.com":
        return "WhatsApp"
    if "google." in host:
        return "Google Chrome"
    return "Google Chrome"


def _contextual_site_search_url(query: str, context_url: str = "") -> tuple[str, str]:
    """Return a deterministic in-site search URL when the current site supports one."""
    value = str(query or "").strip()
    if not value:
        return "", ""
    raw = str(context_url or _LAST_BROWSER_URL or "").strip()
    try:
        host = urllib.parse.urlparse(raw).netloc.casefold()
    except Exception:
        host = ""

    encoded = urllib.parse.quote_plus(value)
    if host.endswith("youtube.com") or host.endswith("www.youtube.com"):
        return (
            f"https://www.youtube.com/results?search_query={encoded}",
            "YouTube",
        )
    if "google." in host:
        return (
            f"https://www.google.com/search?q={encoded}",
            "Google",
        )
    return "", ""


def _explicit_site_search_url(site: str, query: str) -> tuple[str, str]:
    normalized_site = normalize(site)
    value = str(query or "").strip()
    if not value:
        return "", ""
    encoded = urllib.parse.quote_plus(value)
    if "youtube" in normalized_site:
        return (
            f"https://www.youtube.com/results?search_query={encoded}",
            "YouTube",
        )
    if "google" in normalized_site:
        return (
            f"https://www.google.com/search?q={encoded}",
            "Google",
        )
    return "", ""


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", (text or "").lower().strip())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("’", "'")
    value = re.sub(r"[^\w\s'-]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()

    # Conservative corrections for recurring French STT joins/substitutions.
    # These only normalize phrases that already sound like our supported
    # commands; they do not create new capabilities.
    corrections = (
        (r"\bquelleur\b", "quelle heure"),
        (r"\bquelheur\b", "quelle heure"),
        (r"\bauvre\b", "ouvre"),
        (r"\boufre\b", "ouvre"),
        (r"\bouvres\b", "ouvre"),
        (r"\bouvrez\b", "ouvre"),
        (r"\bouvriez\b", "ouvre"),
        (r"\btelechargement\b", "telechargements"),
    )
    for pattern, replacement in corrections:
        value = re.sub(pattern, replacement, value)

    for prefix in ("hey jarvis ", "jarvis ", "jervis "):
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
            break
    return value


def _search_query_from_command(cmd: str) -> str:
    body = re.sub(r"^(?:recherche|cherche)\b", "", cmd).strip()
    body = re.sub(
        r"^(?:sur\s+)?(?:internet|google|le\s+web|web)\b",
        "",
        body,
    ).strip()
    body = re.sub(
        r"^(?:a\s+propos\s+(?:des|du|de|d')|au\s+sujet\s+(?:des|du|de|d'))\s*",
        "",
        body,
    ).strip()
    body = re.sub(
        r"\s+(?:sur\s+)?(?:internet|google|le\s+web|web)$",
        "",
        body,
    ).strip()
    return body


def route(text: str) -> ToolIntent:
    cmd = normalize(text)

    if any(
        phrase in cmd
        for phrase in (
            "arrete jarvis",
            "eteins jarvis",
            "quitte jarvis",
            "ferme jarvis",
            "fermez jarvis",
        )
    ):
        return ToolIntent("assistant.stop")

    if cmd in {
        "merci",
        "c est tout",
        "c'est tout",
        "tu peux dormir",
        "dors",
        "retourne en veille",
        "a plus",
        "a plus jarvis",
        "au revoir",
        "bonne nuit",
    }:
        return ToolIntent("assistant.sleep")

    # Time is checked before application commands so a noisy transcription
    # containing an extra verb does not accidentally launch a browser.
    if "heure" in cmd and any(
        token in cmd
        for token in (
            "quelle",
            "quel",
            "est il",
            "est-il",
            "donne moi",
            "donnes moi",
            "dis moi",
            "dit moi",
        )
    ):
        return ToolIntent("system.time")

    screenshot_terms = (
        "capture ecran",
        "capture d ecran",
        "capture d'ecran",
        "outil capture",
        "snipping tool",
    )
    if any(term in cmd for term in screenshot_terms):
        if any(word in cmd for word in ("ouvre", "ouvrir", "lance", "affiche", "outil")):
            return ToolIntent("app.open", {"app": "snippingtool"})

    if (
        "dossier" in cmd
        and any(
            phrase in cmd
            for phrase in (
                "ouvre",
                "ouvrir",
                "je veux ouvrir",
                "je voudrais ouvrir",
            )
        )
        and not any(
            known in cmd
            for known in (
                "telechargement",
                "telechargements",
                "downloads",
            )
        )
    ):
        match = re.search(r"\bdossier\s+(.+)$", cmd)
        if match:
            target = match.group(1).strip()
            target = re.sub(
                r"^(?:nomme|nommee|appele|appelee|qui s appelle)\s+",
                "",
                target,
            ).strip()
            vague = {
                "specifique",
                "un specifique",
                "particulier",
                "un particulier",
                "precis",
                "un precis",
            }
            if target and target not in vague and len(target) >= 3:
                relation = re.match(
                    r"^(.+?)\s+(?:qui\s+est\s+)?dans\s+(.+)$",
                    target,
                )
                if relation:
                    child = relation.group(1).strip()
                    parent = relation.group(2).strip()
                    if child and parent:
                        return ToolIntent(
                            "folder.open_named",
                            {"query": child, "within": parent},
                        )
                return ToolIntent("folder.open_named", {"query": target})

        return ToolIntent("folder.open_prompt")

    close_tab_match = re.search(
        r"\b(?:ferme|fermes|fermer|fermez)\b.*\bonglet\b\s*(.*)$",
        cmd,
    )
    if close_tab_match:
        target = re.sub(
            r"\b(?:uniquement|seulement|juste)\b",
            "",
            close_tab_match.group(1) or "",
        ).strip()
        return ToolIntent("browser.close_tab", {"name": target})

    if any(
        phrase in cmd
        for phrase in (
            "retour arriere",
            "retour en arriere",
            "reviens en arriere",
            "revenir en arriere",
            "page precedente",
            "precedent dans le navigateur",
        )
    ):
        return ToolIntent("browser.back")

    compound_search = re.search(
        r"\b(?:ouvre|ouvrir|lance|affiche)\b.*\b(youtube|google)\b"
        r".*\b(?:recherche|cherche)\b\s+(.+)$",
        cmd,
    )
    if compound_search:
        return ToolIntent(
            "browser.search_site",
            {
                "site": compound_search.group(1),
                "query": compound_search.group(2).strip(),
            },
        )

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
        if any(x in cmd for x in ("bloc notes", "bloc-notes", "notepad")):
            return ToolIntent("app.open", {"app": "notepad"})
        if any(
            x in cmd
            for x in (
                "telechargements",
                "downloads",
                "les chargements",
                "chargements",
            )
        ):
            return ToolIntent("folder.open", {"folder": "downloads"})

    if re.match(r"^(?:recherche|cherche)\b", cmd):
        query = _search_query_from_command(cmd)
        if not query:
            return ToolIntent("browser.search_prompt")
        explicit_web = bool(
            re.search(
                r"\b(?:internet|google|le web|web)\b",
                cmd,
            )
        )
        return ToolIntent(
            "browser.search",
            {
                "query": query,
                "scope": "web" if explicit_web else "context",
            },
        )

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


def _chrome_candidates() -> list[str]:
    local = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return [
        os.path.join(program_files, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(program_files_x86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe")
        if local else "",
        "chrome",
    ]


def _chrome_executable() -> str | None:
    for candidate in _chrome_candidates():
        if not candidate:
            continue
        path = Path(candidate)
        target = str(path) if path.is_file() else shutil.which(candidate)
        if target:
            return target
    return None


def _chrome_profile_directory() -> str | None:
    """Return Chrome's most recently used real profile directory.

    This avoids launching Chrome's profile chooser when Jarvis opens a website.
    The value is discovered dynamically from Chrome's own Local State file.
    """
    local = os.getenv("LOCALAPPDATA", "")
    if not local:
        return None

    state_path = (
        Path(local)
        / "Google"
        / "Chrome"
        / "User Data"
        / "Local State"
    )
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None

    profile = data.get("profile") or {}
    last_used = str(profile.get("last_used") or "").strip()
    if last_used:
        return last_used

    last_active = profile.get("last_active_profiles") or []
    if isinstance(last_active, list):
        for value in last_active:
            value = str(value or "").strip()
            if value:
                return value

    info_cache = profile.get("info_cache") or {}
    if isinstance(info_cache, dict) and info_cache:
        def activity(item):
            _directory, meta = item
            try:
                return float((meta or {}).get("active_time") or 0)
            except (TypeError, ValueError):
                return 0.0

        directory, _meta = max(info_cache.items(), key=activity)
        return str(directory or "").strip() or None

    return None


def _launch_chrome(*, url: str | None = None) -> bool:
    executable = _chrome_executable()
    if not executable:
        return False

    args = [
        executable,
        "--force-renderer-accessibility=complete",
    ]
    browser_enabled = (os.getenv("JARVIS_BROWSER_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    browser_profile = (os.getenv("JARVIS_BROWSER_PROFILE_DIR") or "").strip()
    browser_port = (os.getenv("JARVIS_BROWSER_CDP_PORT") or "9222").strip()
    if browser_enabled and browser_profile:
        Path(browser_profile).mkdir(parents=True, exist_ok=True)
        args.extend(
            [
                f"--remote-debugging-port={browser_port}",
                f"--user-data-dir={browser_profile}",
            ]
        )
    else:
        profile_directory = _chrome_profile_directory()
        if profile_directory:
            args.append(f"--profile-directory={profile_directory}")
    if url:
        args.append(url)

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    except OSError:
        return False


def _open_browser_url(url: str) -> bool:
    # Prefer the user's most recently used Chrome profile when Chrome exists.
    # Fall back to the Windows default browser otherwise.
    if _launch_chrome(url=url):
        return True
    return bool(webbrowser.open(url))


def _navigate_current_browser_url(url: str) -> bool:
    """Navigate the currently grounded browser tab without spawning a new tab.

    This is the historical-browser fallback used when DOM/CDP is disabled.
    It relies only on standard browser/window primitives: foreground the
    current browser, focus its omnibox, paste the URL into the already-focused
    control, then submit. If any step fails, callers may fall back to opening
    the URL normally.
    """
    try:
        from .windows_perception import (
            activate_window,
            press_key,
            type_text_active_window,
        )

        hint = _browser_window_hint()
        activation = activate_window(hint)
        if not activation.success and hint != "Google Chrome":
            activation = activate_window("Google Chrome")
        if not activation.success:
            return False

        focus = press_key("ctrll")
        if not focus.success:
            return False

        typed = type_text_active_window(
            url,
            mode="replace",
            reactivate=False,
        )
        if not typed.success:
            return False

        submitted = press_key("enter")
        return bool(submitted.success)
    except Exception:
        return False


def _open_application(app: str) -> ToolResult:
    local = os.getenv("LOCALAPPDATA", "")
    windir = os.getenv("WINDIR", r"C:\Windows")
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")

    if app == "spotify":
        try:
            os.startfile("spotify:")
            return ToolResult(True, "C'est fait.", "Spotify protocol")
        except OSError:
            webbrowser.open("https://open.spotify.com")
            return ToolResult(True, "J'ai ouvert Spotify dans le navigateur.")

    if app == "snippingtool":
        candidates = [
            os.path.join(windir, "System32", "SnippingTool.exe"),
            "SnippingTool.exe",
        ]
        if _spawn(candidates):
            return ToolResult(True, "C'est fait.", "Outil Capture d'écran ouvert")
        try:
            os.startfile("ms-screenclip:")
            return ToolResult(True, "C'est fait.", "Capture d'écran Windows ouverte")
        except OSError:
            return ToolResult(
                False,
                "Je n'ai pas trouvé l'outil Capture d'écran.",
                "SnippingTool introuvable",
            )

    if app == "chrome":
        ok = _launch_chrome()
        if ok:
            return ToolResult(
                True,
                "C'est fait.",
                "Chrome ouvert avec le dernier profil utilisé.",
            )
        return ToolResult(
            False,
            "Je n'ai pas trouvé Chrome.",
            "Application introuvable: chrome",
        )

    candidates: dict[str, list[str]] = {
        "cursor": [
            os.path.join(local, "Programs", "cursor", "Cursor.exe") if local else "",
            os.path.join(local, "Programs", "Cursor", "Cursor.exe") if local else "",
            "cursor",
        ],
        "vscode": [
            os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe") if local else "",
            os.path.join(program_files, "Microsoft VS Code", "Code.exe"),
            "code",
        ],
        "notepad": [
            os.path.join(windir, "System32", "notepad.exe"),
            "notepad.exe",
            "notepad",
        ],
    }

    ok = _spawn(candidates.get(app, []))
    if ok:
        return ToolResult(True, "C'est fait.", f"Application ouverte: {app}")

    # Install locations differ across classic Win32, MSIX/Store and package
    # manager setups. Ask Windows' registered application inventory before
    # scanning the filesystem. Get-StartApps/AUMID is the authoritative launch
    # route for many packaged applications that do not expose a stable .exe.
    discovery_names = {
        "cursor": "Cursor",
        "vscode": "Visual Studio Code",
        "notepad": "Notepad",
    }
    query = discovery_names.get(app, app)
    registered, registered_matches = resolve_registered_app(query)
    if registered is not None and launch_registered_app(registered):
        return ToolResult(
            True,
            "C'est fait.",
            json.dumps(
                {
                    "application": registered.name,
                    "source": registered.source,
                    "launch_kind": registered.launch_kind,
                    "app_id": registered.app_id,
                    "executable": registered.executable,
                },
                ensure_ascii=False,
            ),
        )

    path, matches = _find_named_app(query)
    if path is not None:
        try:
            os.startfile(str(path))
            return ToolResult(
                True,
                "C'est fait.",
                f"Application ouverte via raccourci: {path}",
            )
        except OSError as exc:
            return ToolResult(
                False,
                f"Je n'ai pas pu ouvrir {query}.",
                str(exc),
            )

    if matches:
        choices = " | ".join(str(item) for item in matches[:3])
        return ToolResult(
            False,
            f"J'ai trouvé plusieurs applications proches de {query}.",
            choices,
        )
    if registered_matches:
        choices = " | ".join(
            f"{item.name} [{item.source}]"
            for item in registered_matches[:3]
        )
        return ToolResult(
            False,
            f"J'ai trouvé plusieurs applications Windows proches de {query}.",
            choices,
        )
    return ToolResult(
        False,
        f"Je n'ai pas trouvé {query}.",
        f"Application introuvable: {app}",
    )


def _normalize_path_name(value: str) -> str:
    value = value.replace("_", " ").replace("-", " ")
    return normalize(value)


def _app_search_roots() -> list[Path]:
    appdata = os.getenv("APPDATA", "")
    programdata = os.getenv("PROGRAMDATA", r"C:\ProgramData")
    roots = [
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if appdata else None,
        Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path.home() / "Desktop",
    ]
    return [
        root for root in roots
        if root is not None and root.exists() and root.is_dir()
    ]


@lru_cache(maxsize=1)
def application_speech_hints(limit: int = 40) -> tuple[str, ...]:
    """Return generic installed-app names to bias speech recognition.

    Names come from Windows' registered Start-app inventory plus shortcuts,
    never from a hard-coded application dictionary.
    """
    names: list[str] = []
    seen: set[str] = set()

    for name in installed_start_app_names(limit=120):
        key = normalize(name)
        if name and key and key not in seen:
            seen.add(key)
            names.append(name)

    for root in _app_search_roots():
        base_depth = len(root.parts)
        visited = 0
        try:
            for current, dirs, files in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.parts) - base_depth
                if depth >= 3:
                    dirs[:] = []
                    continue
                for filename in files:
                    path = Path(filename)
                    if path.suffix.lower() != ".lnk":
                        continue
                    lowered = filename.lower()
                    if any(
                        bad in lowered
                        for bad in (
                            "uninstall",
                            "update",
                            "updater",
                            "service",
                            "server",
                            "helper",
                        )
                    ):
                        continue
                    name = path.stem.strip()
                    key = normalize(name)
                    if name and key and key not in seen:
                        seen.add(key)
                        names.append(name)
                    visited += 1
                    if visited >= 1200:
                        dirs[:] = []
                        break
                if visited >= 1200:
                    break
        except OSError:
            continue

    names.sort(key=lambda value: (len(value), value.lower()))
    return tuple(names[: max(1, min(int(limit), 80))])


def _app_binary_roots() -> list[Path]:
    local = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.getenv(
        "ProgramFiles(x86)",
        r"C:\Program Files (x86)",
    )
    roots = [
        Path(local) / "Programs" if local else None,
        Path(program_files),
        Path(program_files_x86),
    ]
    return [
        root
        for root in roots
        if root is not None and root.exists() and root.is_dir()
    ]


def _find_named_app(query: str) -> tuple[Path | None, list[Path]]:
    wanted = _normalize_path_name(query)
    compact = wanted.replace(" ", "")
    blocked = {
        "app",
        "application",
        "programme",
        "program",
        "browser",
        "navigateur",
        "com",
        "exe",
    }
    if not wanted or len(compact) < 4 or wanted in blocked:
        return None, []

    scored: list[tuple[float, Path]] = []
    seen: set[str] = set()

    def consider(path: Path) -> None:
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        name = _normalize_path_name(path.stem)
        if not name:
            return
        if wanted == name:
            score = 1.0
        elif (
            len(wanted) >= 5
            and len(name) >= 4
            and (wanted in name or name in wanted)
        ):
            shorter = min(len(wanted), len(name))
            longer = max(len(wanted), len(name))
            containment_ratio = shorter / float(longer)
            # A very short executable name that merely occurs inside a much
            # longer requested application name is not trustworthy identity
            # evidence (for example "test.exe" inside
            # "PersonalAIUnknownTest"). Keep fuzzy filesystem discovery
            # fail-closed unless the contained name represents a substantial
            # portion of the request.
            score = (
                0.94
                if containment_ratio >= 0.45
                else difflib.SequenceMatcher(None, wanted, name).ratio()
            )
        else:
            score = difflib.SequenceMatcher(None, wanted, name).ratio()
        if score >= 0.80:
            scored.append((score, path))

    for root in _app_search_roots():
        base_depth = len(root.parts)
        visited = 0
        try:
            for current, dirs, files in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.parts) - base_depth
                if depth >= 3:
                    dirs[:] = []
                    continue
                for filename in files:
                    suffix = Path(filename).suffix.lower()
                    if suffix != ".lnk":
                        continue
                    lowered = filename.lower()
                    if any(
                        bad in lowered
                        for bad in (
                            "uninstall",
                            "update",
                            "updater",
                            "service",
                            "server",
                            "helper",
                        )
                    ):
                        continue
                    consider(current_path / filename)
                    visited += 1
                    if visited >= 1200:
                        dirs[:] = []
                        break
                if visited >= 1200:
                    break
        except OSError:
            continue

    # Some apps (including package-manager installs) have no usable Start
    # Menu shortcut. Search common application roots for matching executables,
    # with strict depth/visit limits so this remains a bounded discovery step.
    for root in _app_binary_roots():
        base_depth = len(root.parts)
        visited = 0
        try:
            for current, dirs, files in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.parts) - base_depth
                if depth >= 4:
                    dirs[:] = []
                    continue
                for filename in files:
                    path = Path(filename)
                    if path.suffix.lower() != ".exe":
                        continue
                    lowered = filename.lower()
                    if any(
                        bad in lowered
                        for bad in (
                            "uninstall",
                            "update",
                            "updater",
                            "service",
                            "server",
                            "helper",
                            "crashpad",
                        )
                    ):
                        continue
                    consider(current_path / filename)
                    visited += 1
                    if visited >= 2500:
                        dirs[:] = []
                        break
                if visited >= 2500:
                    break
        except OSError:
            continue

    scored.sort(key=lambda item: (-item[0], len(str(item[1]))))
    matches = [path for _score, path in scored[:5]]
    if not matches:
        return None, []

    if scored[0][0] >= 0.90:
        return scored[0][1], matches
    return None, matches


def _file_search_roots() -> list[Path]:
    roots = [
        Path.home() / "Downloads",
        Path.home() / "Desktop",
        Path.home() / "Documents",
    ]
    return [
        root
        for root in roots
        if root.exists() and root.is_dir()
    ]


def _find_named_file(
    query: str,
    *,
    within: str | None = None,
) -> tuple[Path | None, list[Path]]:
    wanted = _normalize_path_name(query)
    tokens = [
        token
        for token in wanted.split()
        if len(token) >= 3 and token not in {"file", "fichier"}
    ]
    if not wanted:
        return None, []

    roots = _file_search_roots()
    if within:
        within_key = normalize(within)
        if within_key in {"downloads", "telechargements", "telechargement"}:
            candidate = Path.home() / "Downloads"
            roots = [candidate] if candidate.exists() else []
        else:
            parent, _ = _find_named_folder(within)
            if parent is not None:
                roots = [parent]

    scored: list[tuple[float, float, Path]] = []
    seen: set[str] = set()

    for root in roots:
        base_depth = len(root.parts)
        visited = 0
        try:
            for current, dirs, files in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.parts) - base_depth
                if depth >= 3:
                    dirs[:] = []
                    continue
                for filename in files:
                    path = current_path / filename
                    key = str(path).lower()
                    if key in seen:
                        continue
                    seen.add(key)

                    candidate = _normalize_path_name(filename)
                    candidate_stem = _normalize_path_name(path.stem)
                    if wanted == candidate or wanted == candidate_stem:
                        score = 1.0
                    elif tokens and all(
                        token in candidate for token in tokens
                    ):
                        score = 0.96
                    else:
                        score = max(
                            difflib.SequenceMatcher(
                                None,
                                wanted,
                                candidate,
                            ).ratio(),
                            difflib.SequenceMatcher(
                                None,
                                wanted,
                                candidate_stem,
                            ).ratio(),
                        )
                    if score < 0.72:
                        continue
                    try:
                        modified = float(path.stat().st_mtime)
                    except OSError:
                        modified = 0.0
                    scored.append((score, modified, path))
                    visited += 1
                    if visited >= 3000:
                        dirs[:] = []
                        break
                if visited >= 3000:
                    break
        except OSError:
            continue

    scored.sort(key=lambda item: (-item[0], -item[1], len(str(item[2]))))
    matches = [path for _score, _modified, path in scored[:8]]
    if not scored:
        return None, []

    best_score = scored[0][0]
    if best_score >= 0.90:
        return scored[0][2], matches
    return None, matches


def _folder_search_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.getenv("JARVIS_FOLDER_ROOTS", "").strip()
    if configured:
        for raw in configured.split(";"):
            raw = os.path.expandvars(os.path.expanduser(raw.strip()))
            if raw:
                roots.append(Path(raw))

    defaults = [
        Path(r"D:\Django_Projects"),
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path.home() / "Downloads",
    ]
    for root in defaults:
        if root not in roots:
            roots.append(root)

    return [root for root in roots if root.exists() and root.is_dir()]


def _find_named_folder(
    query: str,
    *,
    within: str | None = None,
) -> tuple[Path | None, list[Path]]:
    wanted = _normalize_path_name(query)
    if not wanted:
        return None, []

    search_roots = _folder_search_roots()
    if within:
        parent, _parent_matches = _find_named_folder(within)
        if parent is not None:
            search_roots = [parent]

    scored: list[tuple[float, Path]] = []
    seen: set[str] = set()

    def consider(path: Path) -> None:
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        name = _normalize_path_name(path.name)
        if not name:
            return
        if wanted == name:
            score = 1.0
        elif wanted in name or name in wanted:
            score = 0.93
        else:
            score = difflib.SequenceMatcher(None, wanted, name).ratio()
        if score >= 0.68:
            scored.append((score, path))

    for root in search_roots:
        consider(root)
        base_depth = len(root.parts)
        visited = 0
        try:
            for current, dirs, _files in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.parts) - base_depth
                if depth >= 3:
                    dirs[:] = []
                    continue
                for dirname in dirs:
                    consider(current_path / dirname)
                    visited += 1
                    if visited >= 600:
                        dirs[:] = []
                        break
                if visited >= 600:
                    break
        except OSError:
            continue

    scored.sort(key=lambda item: (-item[0], len(str(item[1]))))
    matches = [path for _score, path in scored[:5]]
    if not matches:
        return None, []

    best_score = scored[0][0]
    if best_score >= 0.84:
        return scored[0][1], matches

    return None, matches


def execute(intent: ToolIntent) -> ToolResult:
    if intent.name == "assistant.stop":
        return ToolResult(True, "À bientôt monsieur.", should_exit=True)

    if intent.name == "assistant.sleep":
        return ToolResult(True, "Très bien.", end_session=True)

    if intent.name == "browser.open_url":
        url = str(intent.args["url"])
        ok = _open_browser_url(url)
        if ok:
            _remember_browser_url(url)
        return ToolResult(
            bool(ok),
            "C'est fait." if ok else "Je n'ai pas pu ouvrir le navigateur.",
            url,
        )

    if intent.name == "browser.search_prompt":
        return ToolResult(
            True,
            "Que voulez-vous rechercher ?",
            "En attente du sujet de recherche",
            follow_up="search_query",
        )

    if intent.name == "browser.search":
        query = str(intent.args["query"]).strip()
        if not query:
            return ToolResult(
                True,
                "Que voulez-vous rechercher ?",
                "En attente du sujet de recherche",
                follow_up="search_query",
            )
        scope = str(intent.args.get("scope") or "context").strip().casefold()
        url = ""
        site_name = ""
        if scope != "web":
            url, site_name = _contextual_site_search_url(query)
        if not url:
            url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
            site_name = "Google"
        reuse_current_tab = (
            scope != "web"
            and bool(_LAST_BROWSER_URL)
            and bool(url)
        )
        ok = (
            _navigate_current_browser_url(url)
            if reuse_current_tab
            else _open_browser_url(url)
        )
        if not ok and reuse_current_tab:
            ok = _open_browser_url(url)
        if ok:
            _remember_browser_url(url)
        message = (
            f"Je recherche {query} sur {site_name}."
            if site_name
            else f"Je recherche {query}."
        )
        return ToolResult(bool(ok), message, url)

    if intent.name == "browser.search_site":
        site = str(intent.args.get("site") or "").strip()
        query = str(intent.args.get("query") or "").strip()
        url, site_name = _explicit_site_search_url(site, query)
        if not url:
            return ToolResult(
                False,
                f"Je n'ai pas de recherche directe fiable pour {site or 'ce site'}.",
                site,
            )
        ok = _open_browser_url(url)
        if ok:
            _remember_browser_url(url)
        return ToolResult(
            bool(ok),
            f"Je recherche {query} sur {site_name}.",
            url,
        )

    if intent.name == "browser.close_tab":
        from .windows_perception import activate_window, close_tab

        target = str(intent.args.get("name") or "").strip()
        hint = _browser_window_hint()
        activation = activate_window(hint)
        if not activation.success and hint != "Google Chrome":
            activation = activate_window("Google Chrome")
        if not activation.success:
            return ToolResult(
                False,
                "Je n'ai pas trouvé la fenêtre du navigateur.",
                activation.detail or activation.message,
            )
        # The historical reliable path closes the currently selected tab
        # after the browser window has been explicitly restored. Passing the
        # site label again can make Chrome tab matching ambiguous when titles
        # contain memory/status suffixes.
        result = close_tab("")
        return ToolResult(
            result.success,
            result.message,
            result.detail,
        )

    if intent.name == "browser.back":
        from .windows_perception import activate_window, press_key

        hint = "Google Chrome"
        raw = str(_LAST_BROWSER_URL or "")
        if "youtube.com" in raw.casefold():
            hint = "YouTube"
        elif "web.whatsapp.com" in raw.casefold():
            hint = "WhatsApp"
        activation = activate_window(hint)
        if not activation.success and hint != "Google Chrome":
            activation = activate_window("Google Chrome")
        if not activation.success:
            return ToolResult(
                False,
                "Je n'ai pas trouvé la fenêtre du navigateur.",
                activation.detail or activation.message,
            )
        key_result = press_key("altleft")
        return ToolResult(
            key_result.success,
            "Je reviens à la page précédente."
            if key_result.success
            else key_result.message,
            key_result.detail,
        )

    if intent.name == "app.open":
        return _open_application(str(intent.args["app"]))

    if intent.name == "app.open_named":
        query = str(intent.args.get("query", "")).strip()

        registered, registered_matches = resolve_registered_app(query)
        if registered is not None and launch_registered_app(registered):
            return ToolResult(
                True,
                f"J'ai ouvert {registered.name}.",
                json.dumps(
                    {
                        "application": registered.name,
                        "source": registered.source,
                        "launch_kind": registered.launch_kind,
                        "app_id": registered.app_id,
                        "executable": registered.executable,
                    },
                    ensure_ascii=False,
                ),
            )

        path, matches = _find_named_app(query)
        if path is not None:
            try:
                os.startfile(str(path))
                return ToolResult(
                    True,
                    f"J'ai ouvert {path.stem}.",
                    str(path),
                )
            except OSError as exc:
                return ToolResult(
                    False,
                    f"Je n'ai pas pu ouvrir {path.stem}.",
                    str(exc),
                )
        if matches:
            choices = ", ".join(item.stem for item in matches[:3])
            return ToolResult(
                False,
                f"J'ai trouvé plusieurs applications proches : {choices}. Pouvez-vous préciser ?",
                " | ".join(str(item) for item in matches[:3]),
            )
        if registered_matches:
            choices = ", ".join(
                item.name for item in registered_matches[:3]
            )
            return ToolResult(
                False,
                f"J'ai trouvé plusieurs applications Windows proches : {choices}. Pouvez-vous préciser ?",
                json.dumps(
                    [item.as_dict() for item in registered_matches[:3]],
                    ensure_ascii=False,
                ),
            )
        return ToolResult(
            False,
            f"Je n'ai pas trouvé d'application correspondant à {query}.",
            query,
        )


    if intent.name == "file.open_named":
        query = str(intent.args.get("query", "")).strip()
        within = str(intent.args.get("within", "")).strip() or None
        path, matches = _find_named_file(query, within=within)
        if path is not None:
            try:
                os.startfile(str(path))
                return ToolResult(
                    True,
                    f"J'ai ouvert {path.name}.",
                    str(path),
                )
            except OSError as exc:
                return ToolResult(
                    False,
                    f"Je n'ai pas pu ouvrir {path.name}.",
                    str(exc),
                )
        if matches:
            choices = " | ".join(str(item) for item in matches[:5])
            return ToolResult(
                False,
                "Plusieurs fichiers correspondent. Précisez le fichier.",
                choices,
            )
        return ToolResult(
            False,
            f"Je n'ai pas trouvé de fichier correspondant à {query}.",
            query,
        )

    if intent.name == "folder.open_prompt":
        return ToolResult(
            True,
            "Quel dossier voulez-vous ouvrir ?",
            "En attente du nom du dossier",
            follow_up="folder_name",
        )

    if intent.name == "folder.open_named":
        query = str(intent.args.get("query", "")).strip()
        within = str(intent.args.get("within", "")).strip() or None
        path, matches = _find_named_folder(query, within=within)
        if path is not None:
            os.startfile(str(path))
            return ToolResult(
                True,
                f"J'ai ouvert le dossier {path.name}.",
                str(path),
            )
        if matches:
            choices = ", ".join(item.name for item in matches[:3])
            return ToolResult(
                False,
                f"J'ai trouvé plusieurs dossiers proches : {choices}. "
                "Pouvez-vous préciser ?",
                " | ".join(str(item) for item in matches[:3]),
            )
        return ToolResult(
            False,
            f"Je n'ai pas trouvé de dossier correspondant à {query}.",
            query,
        )

    if intent.name == "folder.open":
        folder = str(intent.args["folder"])
        path = Path.home() / ("Downloads" if folder == "downloads" else folder)
        if path.exists():
            os.startfile(str(path))
            return ToolResult(True, "C'est fait.", str(path))
        return ToolResult(False, "Je n'ai pas trouvé ce dossier.", str(path))

    if intent.name == "system.time":
        now = dt.datetime.now()
        return ToolResult(
            True,
            f"Il est {now:%H} heures {now:%M}.",
            now.isoformat(),
        )

    return ToolResult(
        False,
        "Je vous ai entendu, mais je n'ai pas encore l'outil pour cette demande.",
        f"Intent non pris en charge: {intent.args.get('text', '')}",
    )
