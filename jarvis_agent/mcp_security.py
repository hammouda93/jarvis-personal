"""Explicit stdio trust and least-credential subprocess configuration.

This is not an OS sandbox. A trusted local server runs as the current user.
Never build a shell command, import arbitrary environment secrets or install
packages on behalf of an external tool description.
"""
from __future__ import annotations

import os
from pathlib import PureWindowsPath
import re
import shutil

_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_REF = re.compile(r"^JARVIS_MCP_[A-Z0-9_]{1,70}$")
_SHELLS = frozenset({"cmd", "powershell", "pwsh", "bash", "sh", "zsh", "fish",
                     "wscript", "cscript", "mshta", "rundll32", "reg", "schtasks"})
_INJECTORS = frozenset({"PATH", "COMSPEC", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                       "NODE_OPTIONS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"})
_BASE_ENV = frozenset({"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                      "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL"})


def validate_stdio(entry: dict) -> None:
    command, args = entry.get("command"), entry.get("args")
    legacy_hermes = command == "hermes" and args == ["mcp", "serve"]
    if not legacy_hermes and entry.get("trusted_stdio") is not True:
        raise ValueError("stdio_requires_explicit_program_trust")
    if (not isinstance(command, str) or not command.strip() or len(command) > 800
            or any(c in command for c in "\r\n\x00")):
        raise ValueError("invalid_stdio_command")
    basename = PureWindowsPath(command.replace("/", "\\")).name.lower()
    if PureWindowsPath(basename).suffix in {".cmd", ".bat", ".ps1"}:
        raise ValueError("stdio_shell_launcher_forbidden")
    if basename.removesuffix(".exe").removesuffix(".cmd").removesuffix(".bat") in _SHELLS:
        raise ValueError("stdio_shell_launcher_forbidden")
    if (not isinstance(args, list) or len(args) > 64 or
            any(not isinstance(a, str) or len(a) > 2000 or any(c in a for c in "\r\n\x00") for a in args)):
        raise ValueError("invalid_stdio_arguments")
    if any(a.lower() in {"-c", "-e", "--eval", "-command", "-encodedcommand", "/c", "/k"} for a in args):
        raise ValueError("stdio_inline_code_forbidden")
    refs = entry.get("env_refs", {})
    if not isinstance(refs, dict) or len(refs) > 20:
        raise ValueError("invalid_stdio_environment_refs")
    for name, source in refs.items():
        if (not isinstance(name, str) or not _ENV.fullmatch(name) or name in _INJECTORS
                or not isinstance(source, str) or not _REF.fullmatch(source)):
            raise ValueError("stdio_environment_requires_scoped_references")


def stdio_environment(entry: dict) -> dict[str, str]:
    validate_stdio(entry)  # Recheck persisted configuration immediately before spawn.
    result = {k: v for k, v in os.environ.items() if k.upper() in _BASE_ENV}
    for target, source in entry.get("env_refs", {}).items():
        value = os.getenv(source)
        if value is None:
            raise ValueError("stdio_scoped_credential_missing")
        result[target] = value
    return result


def stdio_command(entry: dict) -> str:
    validate_stdio(entry)
    command = shutil.which(entry["command"]) or entry["command"]
    # The SDK also resolves batch wrappers on Windows. Do not pass a bare name
    # through that fallback when it has not resolved to an executable here.
    if os.name == "nt" and PureWindowsPath(command).suffix.lower() != ".exe":
        raise ValueError("stdio_requires_windows_executable")
    legacy = entry.get("command") == "hermes" and entry.get("args") == ["mcp", "serve"]
    validate_stdio({**entry, "command": command, "trusted_stdio": legacy or entry.get("trusted_stdio") is True})
    return command
