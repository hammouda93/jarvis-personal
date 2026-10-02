#!/usr/bin/env python3
from __future__ import annotations

import sys


def _configure_console_streams() -> None:
    """Never let a Windows code page error terminate the Jarvis process."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="backslashreplace")
            except Exception:
                pass


_configure_console_streams()

from jarvis_agent.ui import run_ui


if __name__ == "__main__":
    sys.exit(run_ui())
