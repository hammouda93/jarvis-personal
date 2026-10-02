from __future__ import annotations

import argparse
import json

from .capability_registry import DEFAULT_CAPABILITY_REGISTRY
from .connector_registry import DEFAULT_CONNECTOR_REGISTRY
from .event_journal import StructuredEventJournal
from .model_telemetry import ModelTelemetryStore
from .regression_registry import DEFAULT_REGRESSION_REGISTRY


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Jarvis AIOS-inspired architecture foundations."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("agents", help="List declared specialized agents.")
    sub.add_parser("capabilities", help="List declared capabilities.")
    sub.add_parser("connectors", help="List connector contracts.")
    sub.add_parser("tests", help="List regression test registry.")
    sub.add_parser("stats", help="Show passive architecture store counters.")

    trace = sub.add_parser("trace", help="Show one structured mission trace.")
    trace.add_argument("mission_id")

    model = sub.add_parser(
        "models",
        help="Show passive provider/model telemetry summary.",
    )
    model.add_argument("--hours", type=float, default=24.0)
    model.add_argument("--task-class", default="")

    return parser


def main() -> int:
    args = _parser().parse_args()

    if args.command == "agents":
        value = [
            manifest.as_dict()
            for manifest in DEFAULT_CAPABILITY_REGISTRY.agents()
        ]
    elif args.command == "capabilities":
        value = [
            {
                "provider_agent_id": item.provider_agent_id,
                **item.spec.as_dict(),
            }
            for item in DEFAULT_CAPABILITY_REGISTRY.capabilities()
        ]
    elif args.command == "connectors":
        value = [
            spec.as_dict()
            for spec in DEFAULT_CONNECTOR_REGISTRY.all()
        ]
    elif args.command == "tests":
        value = [
            spec.as_dict()
            for spec in DEFAULT_REGRESSION_REGISTRY.all()
        ]
    elif args.command == "stats":
        value = {
            "events": StructuredEventJournal().stats(),
            "models": ModelTelemetryStore().stats(),
            "agents": len(DEFAULT_CAPABILITY_REGISTRY.agents()),
            "capabilities": len(
                DEFAULT_CAPABILITY_REGISTRY.capabilities()
            ),
            "connectors": len(DEFAULT_CONNECTOR_REGISTRY.all()),
            "regression_tests": len(
                DEFAULT_REGRESSION_REGISTRY.all()
            ),
        }
    elif args.command == "trace":
        value = StructuredEventJournal().mission_trace(
            args.mission_id
        )
    elif args.command == "models":
        value = ModelTelemetryStore().summary(
            task_class=args.task_class or None,
            since_hours=args.hours,
        )
    else:
        return 2

    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
