#!/usr/bin/env python3
from __future__ import annotations

import sys

from jarvis_agent.session_logging import install_session_logging
from jarvis_agent.ui import run_ui


if __name__ == "__main__":
    install_session_logging()
    sys.exit(run_ui())
