"""Fail-fast dependency check for the live Personal AI runtime."""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys


CORE_IMPORTS = (
    ("openai", "OpenAI-compatible model client"),
    ("PySide6.QtCore", "Qt desktop UI"),
    ("sounddevice", "audio I/O"),
    ("dotenv", "environment loading"),
)

WINDOWS_IMPORTS = (
    ("pythoncom", "Windows COM"),
    ("win32com.client", "Windows SAPI/COM"),
    ("pywinauto", "Windows UI Automation"),
)


def _check(module_name: str, purpose: str) -> dict[str, str | bool]:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        return {
            "module": module_name,
            "purpose": purpose,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "module": module_name,
        "purpose": purpose,
        "ok": True,
        "error": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-windows-automation", action="store_true")
    args = parser.parse_args()

    checks = [_check(*item) for item in CORE_IMPORTS]
    if platform.system().lower() == "windows":
        checks.extend(_check(*item) for item in WINDOWS_IMPORTS)

    failures = [item for item in checks if not item["ok"]]
    payload = {
        "ok": not failures,
        "python": sys.executable,
        "version": sys.version.split()[0],
        "platform": platform.platform(),
        "checks": checks,
        "repair": (
            ".\\scripts\\repair_live_environment.ps1 "
            f'-PythonExe "{sys.executable}"'
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
