"""Cumulative automated tests; no personal accounts or live acceptance scenarios."""
from __future__ import annotations

import os
import argparse
from pathlib import Path
import sys
import tempfile
import faulthandler
import gc
import unittest


WINDOWS_ONLY = frozenset({
    "test_learned_app_profile_is_reused_before_rescanning_windows",
    "test_cursor_falls_back_to_dynamic_windows_discovery",
    "test_inspection_recovers_from_uia_enumeration_failure",
    "test_named_inspection_prefers_stable_native_handle",
})


def platform_suite(suite: unittest.TestSuite) -> unittest.TestSuite:
    result = unittest.TestSuite()
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            result.addTest(platform_suite(test))
        elif sys.platform != "win32" and test.id().split(".")[-1] in WINDOWS_ONLY:
            # Keep OS-specific tests visible in the report as explicit skips.
            def skip(test_id=test.id()):
                raise unittest.SkipTest("Windows API contract: " + test_id)
            result.addTest(unittest.FunctionTestCase(skip, description=test.id()))
        else:
            result.addTest(test)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="test_*.py")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["JARVIS_CUA_DRIVER_ENABLED"] = "0"
    os.environ["JARVIS_HERMES_RELIABILITY_ENABLED"] = "0"
    os.environ["JARVIS_RUNTIME_CONVERGENCE_ENABLED"] = "0"
    os.environ["JARVIS_ACTIVE_SUPERVISOR_ENABLED"] = "0"
    # Global memory stores initialize at import. Never use personal data in tests.
    (root / ".cache").mkdir(exist_ok=True)
    faulthandler.dump_traceback_later(180, repeat=False)
    with tempfile.TemporaryDirectory(prefix="regression-", dir=root / ".cache") as isolated:
        os.environ["JARVIS_DATA_DIR"] = isolated
        os.environ["JARVIS_MCP_CONFIG_PATH"] = str(Path(isolated) / "mcp_servers.json")
        for key in ("CEREBRAS_API_KEY", "CEREBRAS_SECONDARY_API_KEY", "GROQ_API_KEY",
                    "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
            os.environ[key] = ""
        suite = unittest.defaultTestLoader.discover("tests", pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=1).run(platform_suite(suite))
        gc.collect()
    faulthandler.cancel_dump_traceback_later()
    print(f"Automated regression: {result.testsRun} tests, "
          f"{len(result.failures)} failures, {len(result.errors)} errors, "
          f"{len(result.skipped)} explicit skips. Live acceptance NOT performed.")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
