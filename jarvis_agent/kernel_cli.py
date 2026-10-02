from __future__ import annotations

import argparse
import json

from .approval_manager import HumanApprovalManager
from .capability_registry import DEFAULT_CAPABILITY_REGISTRY
from .connector_registry import DEFAULT_CONNECTOR_REGISTRY
from .component_registry import DEFAULT_COMPONENT_REGISTRY
from .correction_store import CorrectionCandidateStore
from .event_journal import StructuredEventJournal
from .incident_bundle import IncidentBundleBuilder
from .kernel_request_store import KernelRequestStore
from .mission_context_store import MissionContextStore
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
    sub.add_parser("missions", help="List resumable mission contexts.")
    sub.add_parser("corrections", help="List pending correction candidates.")
    sub.add_parser("approvals", help="List pending human approvals.")
    sub.add_parser("components", help="List known Jarvis code components.")
    sub.add_parser(
        "recovery",
        help="List kernel requests that require post-crash recovery.",
    )
    sub.add_parser("stats", help="Show passive architecture store counters.")

    incident = sub.add_parser(
        "incident",
        help="Build a Dev Supervisor incident bundle for one mission.",
    )
    incident.add_argument("mission_id")

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
    elif args.command == "missions":
        value = MissionContextStore().list_resumable(limit=100)
    elif args.command == "corrections":
        value = CorrectionCandidateStore().pending(limit=100)
    elif args.command == "approvals":
        value = [
            {
                "approval_id": item.approval_id,
                "mission_id": item.mission_id,
                "request_id": item.request_id,
                "agent_id": item.agent_id,
                "capability": item.capability,
                "summary": item.summary,
                "risk": item.risk.value,
                "status": item.status.value,
                "created_at": item.created_at,
                "expires_at": item.expires_at,
            }
            for item in HumanApprovalManager().pending()
        ]
    elif args.command == "components":
        value = [
            {
                "component_id": item.component_id,
                "description": item.description,
                "watched_paths": list(item.watched_paths),
                "tags": list(item.tags),
                "capabilities": list(item.capabilities),
                "default_test_ids": list(item.default_test_ids),
            }
            for item in DEFAULT_COMPONENT_REGISTRY.all()
        ]
    elif args.command == "recovery":
        value = [
            item.as_dict()
            for item in KernelRequestStore().recovery_required()
        ]
    elif args.command == "incident":
        journal = StructuredEventJournal()
        bundle = IncidentBundleBuilder(journal).build(
            args.mission_id
        )
        value = {
            "mission_id": bundle.mission_id,
            "user_inputs": list(bundle.user_inputs),
            "decisions": list(bundle.decisions),
            "tool_events": list(bundle.tool_events),
            "observations": list(bundle.observations),
            "proofs": list(bundle.proofs),
            "feedback": list(bundle.feedback),
            "errors": list(bundle.errors),
            "component_ids": list(bundle.component_ids),
            "event_ids": list(bundle.event_ids),
            "summary": dict(bundle.summary),
        }
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
            "resumable_missions": len(
                MissionContextStore().list_resumable(limit=500)
            ),
            "pending_corrections": len(
                CorrectionCandidateStore().pending(limit=500)
            ),
            "pending_approvals": len(
                HumanApprovalManager().pending()
            ),
            "recovery_required_requests": len(
                KernelRequestStore().recovery_required()
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
