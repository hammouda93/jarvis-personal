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

    reset = sub.add_parser(
        "reset-operational",
        help="Clear skills, lessons, app profiles and proof runs only.",
    )
    reset.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation. Personal memory is never touched.",
    )

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

    if args.command == "reset-operational":
        if not args.yes:
            print(
                "Refusé: ajoutez --yes pour effacer uniquement "
                "la mémoire opérationnelle."
            )
            return 2
        before = AGENT_KNOWLEDGE.clear_operational_knowledge()
        print(
            json.dumps(
                {
                    "cleared": before,
                    "after": AGENT_KNOWLEDGE.stats(),
                    "personal_memory_touched": False,
                },
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
