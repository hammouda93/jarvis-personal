from __future__ import annotations

import argparse
import json

from .config import settings
from .kernel_contracts import SyscallStatus
from .shadow_kernel_runtime import KernelShadowObserver


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect the passive Kernel shadow of the live Jarvis runtime."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    missions = sub.add_parser("missions", help="Show recent shadow missions.")
    missions.add_argument("--limit", type=int, default=20)

    trace = sub.add_parser("trace", help="Show one shadow mission event trace.")
    trace.add_argument("mission_id")

    graph = sub.add_parser("graph", help="Show one persisted shadow task graph.")
    graph.add_argument("mission_id")

    sub.add_parser("stats", help="Show shadow store counters and safety state.")
    return parser


def _graph_payload(observer: KernelShadowObserver, mission_id: str):
    graph = observer.stack.graph_store.load(mission_id)
    if graph is None:
        return None
    return {
        "mission_id": graph.mission_id,
        "summary": graph.summary(),
        "nodes": [
            {
                "task_id": node.task_id,
                "capability": node.capability,
                "agent_id": node.agent_id,
                "dependencies": sorted(node.dependencies),
                "status": node.status.value,
                "priority": node.priority,
                "payload": dict(node.payload),
                "result": dict(node.result),
                "error": node.error,
            }
            for node in graph.nodes()
        ],
    }


def main() -> int:
    args = _parser().parse_args()
    observer = KernelShadowObserver.from_settings(settings)

    if args.command == "missions":
        value = observer.stack.journal.recent_missions(
            limit=max(1, min(int(args.limit), 200))
        )
    elif args.command == "trace":
        value = observer.stack.journal.mission_trace(args.mission_id)
    elif args.command == "graph":
        value = _graph_payload(observer, args.mission_id)
    elif args.command == "stats":
        queued = observer.stack.request_store.by_status(
            SyscallStatus.QUEUED
        )
        running = observer.stack.request_store.by_status(
            SyscallStatus.RUNNING
        )
        value = {
            "shadow_enabled_in_config": bool(
                settings.kernel_shadow_enabled
            ),
            "events": observer.stack.journal.stats(),
            "queued_kernel_requests": len(queued),
            "running_kernel_requests": len(running),
            "recovery_required_requests": len(
                observer.stack.request_store.recovery_required()
            ),
            "safety_expectation": {
                "authoritative": False,
                "dispatcher_active": False,
                "expected_queued_requests": 0,
                "expected_running_requests": 0,
            },
        }
    else:
        return 2

    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
