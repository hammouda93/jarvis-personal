"""P0 launch bootstrap kept in a file for Windows PowerShell argv compatibility."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys

from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", required=True)
    parser.add_argument("--bridge-config", required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    load_dotenv(args.env_path, override=False)
    # This explicit acceptance profile applies only to this child process.
    os.environ.update({
        "JARVIS_MEMORY_CORE_ENABLED": "1",
        "JARVIS_SEMANTIC_MEMORY_V5_ENABLED": "1",
        "JARVIS_MEMORY_AGENT_TOOLS_ENABLED": "1",
        "JARVIS_MEMORY_DIRECT_AGENT_ENABLED": "1",
        "JARVIS_MEMORY_SCOPE_GUARD_ENABLED": "1",
        "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "1",
        "JARVIS_EFFICIENT_CONTEXT_ENABLED": "1",
        "JARVIS_BROWSER_CORE_ENABLED": "1",
        "JARVIS_BROWSER_ENABLED": "0",
        "JARVIS_BROWSER_BRIDGE_CONFIG": args.bridge_config,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    sys.argv = ["runtime_preflight"]
    from jarvis_agent.runtime_preflight import main as preflight
    if preflight():
        raise SystemExit("Runtime dependencies need repair; see runtime_preflight output.")
    print("P0 profile: direct SQLite memory, durable missions, normal-profile Chrome bridge.")
    print("Existing data/provider preferences retained. No keys printed or configuration copied.")
    if args.check_only:
        print("CHECK ONLY: launcher dependencies validated; UI, browser connection and models not started.")
        return 0
    if not Path(args.bridge_config).is_file():
        raise SystemExit("Browser bridge configuration missing; follow docs/P0_WINDOWS_ACCEPTANCE.md.")
    from jarvis_agent.browser_bridge_doctor import main as bridge_doctor
    sys.argv = ["browser_bridge_doctor", "--config", args.bridge_config]
    if bridge_doctor():
        raise SystemExit("Connect the P0 extension in normal Chrome before launching Jarvis.")
    sys.argv = ["run_jarvis.py"]
    runpy.run_path(str(root / "run_jarvis.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
