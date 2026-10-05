"""Fail-fast dependency check for the live Personal AI runtime."""
from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys
from pathlib import Path


CORE_IMPORTS = (
    ("openai", "OpenAI-compatible model client"),
    ("PySide6.QtCore", "Qt desktop UI"),
    ("sounddevice", "audio I/O"),
    ("dotenv", "environment loading"),
)

WINDOWS_BASE_IMPORTS = (
    ("pythoncom", "Windows COM"),
    ("win32com.client", "Windows SAPI/COM"),
)

WINDOWS_AUTOMATION_IMPORTS = (
    ("pywinauto", "Windows UI Automation"),
)

GROUNDING_IMPORTS = (
    ("winrt.windows.media.ocr", "Windows native OCR"),
    ("winrt.windows.graphics.imaging", "Windows image projection"),
    ("winrt.windows.storage.streams", "Windows WinRT streams"),
)

FORBIDDEN_LIVE_PATH_MARKERS = (
    ".cache\\foundation-test-deps",
    ".cache\\foundation-ocr-deps",
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


def _live_path_contamination() -> list[str]:
    bad = []
    for value in sys.path:
        normalized = str(value or "").replace("/", "\\").casefold()
        if any(marker.casefold() in normalized for marker in FORBIDDEN_LIVE_PATH_MARKERS):
            bad.append(str(value))
    return bad


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-windows-automation", action="store_true")
    parser.add_argument("--require-grounding", action="store_true")
    args = parser.parse_args()

    checks = [_check(*item) for item in CORE_IMPORTS]
    python_automation_compatible = True
    python_automation_reason = ""
    if platform.system().lower() == "windows":
        checks.extend(_check(*item) for item in WINDOWS_BASE_IMPORTS)
        needs_automation = (
            args.require_windows_automation or args.require_grounding
        )
        if needs_automation:
            # comtypes documents Python 3.8/3.9 runtime issues fixed in
            # CPython >=3.10.10 and >=3.11.2. Keep old V3 environments usable
            # for non-automation gates, but fail closed before V4 Computer Core.
            python_automation_compatible = sys.version_info >= (3, 10, 10)
            if not python_automation_compatible:
                python_automation_reason = (
                    "Windows automation requires Python >=3.10.10; "
                    "Python 3.11/3.12 is recommended for Foundations V4."
                )
            else:
                checks.extend(
                    _check(*item)
                    for item in WINDOWS_AUTOMATION_IMPORTS
                )
                if args.require_grounding:
                    checks.extend(
                        _check(*item)
                        for item in GROUNDING_IMPORTS
                    )

    contamination = _live_path_contamination()
    failures = [item for item in checks if not item["ok"]]
    payload = {
        "ok": not failures,
        "python": sys.executable,
        "version": sys.version.split()[0],
        "platform": platform.platform(),
        "checks": checks,
        "windows_automation_compatible": python_automation_compatible,
        "windows_automation_reason": python_automation_reason,
        "path_contamination": contamination,
        "repair": (
            ".\\scripts\\repair_live_environment.ps1 "
            f'-PythonExe "{sys.executable}"'
            + (" -Grounding" if args.require_grounding else "")
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return (
        0
        if not failures
        and not contamination
        and python_automation_compatible
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
