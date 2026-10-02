from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agent_knowledge import AGENT_KNOWLEDGE


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect/export Jarvis local operational knowledge."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="Show local knowledge counters.")

    export = sub.add_parser(
        "export",
        help="Create an anonymized JSON snapshot for debugging/sharing.",
    )
    export.add_argument(
        "--output",
        default="jarvis_agent_knowledge_export.json",
        help="Destination JSON file.",
    )
    export.add_argument(
        "--raw",
        action="store_true",
        help="Disable anonymization. Keep this file private.",
    )

    args = parser.parse_args()

    if args.command == "stats":
        print(
            json.dumps(
                AGENT_KNOWLEDGE.stats(),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "export":
        path = AGENT_KNOWLEDGE.export_snapshot(
            Path(args.output).expanduser().resolve(),
            anonymize=not args.raw,
        )
        print(path)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
