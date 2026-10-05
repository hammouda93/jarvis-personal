"""Automated checks only. No real-desktop success is inferred from this suite.

SQLite context managers in the checkpoint commit transactions but do not close
connections. Collect unreachable handles before TemporaryDirectory cleanup on
Windows/Python 3.12; retain real SQLite semantics and every test assertion.
"""
from __future__ import annotations

import argparse
import gc
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", default="node")
    parser.add_argument("--new-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0,str(root))
    os.environ["LOCALAPPDATA"] = str(root / ".cache" / "foundation-test-state")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    for name in ("JARVIS_MEMORY_CORE_ENABLED", "JARVIS_BROWSER_CORE_ENABLED", "JARVIS_COMPUTER_CORE_ENABLED"):
        os.environ[name] = "0"
    cleanup = tempfile.TemporaryDirectory.cleanup
    def collected_cleanup(self):
        gc.collect()
        cleanup(self)
    tempfile.TemporaryDirectory.cleanup = collected_cleanup
    pattern = "test_foundations_v4*.py" if args.new_only else "test*.py"
    suite = unittest.defaultTestLoader.discover("tests", pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    js = subprocess.run([args.node, "--test", "tests/browser_bridge_replay.mjs"], check=False)
    return 0 if result.wasSuccessful() and js.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
