"""P0 launch bootstrap kept in a file for Windows PowerShell argv compatibility."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import subprocess
import sys

from dotenv import load_dotenv


def apply_profile(bridge_config: str, *, full: bool = False) -> None:
    """Set the acceptance profile only in the launched Python process."""
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
        "JARVIS_BROWSER_BRIDGE_CONFIG": bridge_config,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    if full:
        from jarvis_agent.operational_preferences import set_session_preferences
        os.environ.update({
            "JARVIS_COMPUTER_CORE_ENABLED": "1",
            # An explicit empty value also prevents config.load_dotenv from
            # restoring an old model opt-in from the checkout's .env.
            "JARVIS_GROUNDING_MODEL": "",
            "JARVIS_GROUNDING_BUDGET_S": "3",
        })
        set_session_preferences({"skills_enabled": False, "learning_enabled": False})


def source_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=3, check=True,
        )
        return result.stdout.strip() or "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-path", required=True)
    parser.add_argument("--bridge-config", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--full", action="store_true",
                        help="V12 Windows profile: memory, Chrome, UIA/OCR; Skills/Learning off for this session")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    load_dotenv(args.env_path, override=False)
    apply_profile(args.bridge_config, full=args.full)
    print(f"Jarvis {'V12 full' if args.full else 'P0'} profile | commit={source_revision(root)}", flush=True)
    sys.argv = ["runtime_preflight"]
    if args.full:
        sys.argv += ["--require-windows-automation", "--require-grounding"]
    from jarvis_agent.runtime_preflight import main as preflight
    if preflight():
        raise SystemExit("Runtime dependencies need repair; see runtime_preflight output.")
    print("P0 profile: direct SQLite memory, durable missions, normal-profile Chrome bridge.")
    print("Existing data/provider preferences retained. No keys printed or configuration copied.")
    if args.full:
        print("V12: Windows UIA/OCR on; grounding model off; Skills/Learning off for this session.")
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
