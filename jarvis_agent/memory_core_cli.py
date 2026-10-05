"""Exercise deterministic memory routing in a fresh process, without an LLM."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


class _NoModel:
    def run(self, *args, **kwargs):
        raise ValueError("This CLI only accepts deterministic memory requests.")

    def reset(self): pass
    def warm_up(self, **kwargs): pass
    def record_external_turn(self, *args, **kwargs): pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("text")
    args = parser.parse_args()
    from .foundation_tools import FoundationToolAdapter
    from .memory_core_store import MemoryCoreStore
    from .memory_router import MemoryRoutingRuntime
    memory = MemoryCoreStore(Path(args.db))
    # delegate=None deliberately makes all non-memory operations unavailable.
    runtime = MemoryRoutingRuntime(_NoModel(), FoundationToolAdapter(None, memory=memory))
    result = runtime.run(args.text, log=lambda line: print(line, file=__import__("sys").stderr))
    print(json.dumps({"text": result.text, "actions": [json.loads(a.as_json()) for a in result.actions]}, ensure_ascii=True))
    return 0 if all(a.success for a in result.actions) else 1


if __name__ == "__main__":
    raise SystemExit(main())
