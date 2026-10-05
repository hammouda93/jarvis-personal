"""Interactive live primitive console; JSONL proof recording for Windows gates.

This explicitly executes commands typed by the user. No scenario/contact/site
is embedded. Keep this process alive for computer reference freshness.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    args = parser.parse_args()
    from .foundation_tools import build_foundation_tools
    from .native_tools import NATIVE_TOOLS
    tools = build_foundation_tools(NATIVE_TOOLS)
    output = Path(args.record)
    output.parent.mkdir(parents=True, exist_ok=True)
    print("Live JSON console. Commands: {tool,arguments}. Special: windows, quit. Refs expire after actions.")
    with output.open("a",encoding="utf-8") as trace:
        for line in __import__("sys").stdin:
            if line.strip() == "quit": break
            try:
                if line.strip() == "windows":
                    from .windows_perception import _native_window_candidates
                    print(json.dumps(_native_window_candidates(limit=40),ensure_ascii=True),flush=True)
                    continue
                command = json.loads(line)
                name = command["tool"]
                # Browser scope cannot fall through to OS mutation commands.
                if name.startswith("browser_"):
                    tools.begin_turn("browser")
                elif name.startswith("computer_"):
                    tools.begin_turn("inspect application window")
                started = time.perf_counter()
                action = tools.execute(name, command.get("arguments",{}))
                record = {"timestamp":time.time(),"request":command,"result":json.loads(action.as_json()),
                          "seconds":time.perf_counter()-started,"evidence_kind":"live_local"}
                if name.startswith("computer_") and tools.computer and tools.computer._observation:
                    window = tools.computer._observation["window"]
                    try:
                        path = output.parent/(output.stem+"-"+str(time.time_ns())+".png")
                        path.write_bytes(tools.computer.backend.capture(window["window_id"]))
                        record["screenshot"] = str(path.resolve())
                    except Exception as exc:
                        record["screenshot_error"] = str(exc)
                trace.write(json.dumps(record,ensure_ascii=False)+"\n"); trace.flush()
                print(json.dumps(record,ensure_ascii=True),flush=True)
            except Exception as exc:
                print(json.dumps({"error":str(exc)}),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
