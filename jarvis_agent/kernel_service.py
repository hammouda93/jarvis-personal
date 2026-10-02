from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .approval_manager import HumanApprovalManager
from .event_bus import BusEvent, MissionEventBus
from .event_journal import StructuredEventJournal
from .kernel_contracts import EventKind, KernelRequest, KernelResponse
from .kernel_policy import AuthorizationDecision, KernelPolicy
from .mission_scheduler import MissionScheduler


@dataclass(frozen=True)
class KernelSubmission:
    accepted: bool
    request_id: str
    queued: bool
    reason: str
    approval_id: str | None = None
    requires_approval: bool = False


class JarvisKernel:
    """Passive composition root for future Jarvis kernel services.

    It does not execute tools/models itself. The current live runtime remains
    authoritative until this layer is explicitly enabled and integrated.
    """

    def __init__(
        self,
        *,
        policy: KernelPolicy | None = None,
        scheduler: MissionScheduler | None = None,
        approvals: HumanApprovalManager | None = None,
        event_bus: MissionEventBus | None = None,
        journal: StructuredEventJournal | None = None,
    ):
        self.policy = policy or KernelPolicy()
        self.scheduler = scheduler or MissionScheduler()
        self.approvals = approvals or HumanApprovalManager()
        self.event_bus = event_bus or MissionEventBus()
        self.journal = journal
        self._pending_approval_requests: dict[str, KernelRequest] = {}

    def _emit(
        self,
        request: KernelRequest,
        kind: EventKind,
        *,
        payload: dict[str, Any],
        success: bool | None = None,
    ) -> None:
        event = BusEvent(
            kind=kind.value,
            mission_id=request.mission_id,
            agent_id=request.agent_id,
            component="kernel",
            payload=dict(payload),
        )
        self.event_bus.publish(event)
        if self.journal is not None:
            try:
                self.journal.append_event(
                    mission_id=request.mission_id,
                    kind=kind,
                    agent_id=request.agent_id,
                    component="kernel",
                    success=success,
                    payload=dict(payload),
                )
            except Exception:
                # Observability must never block the control path.
                pass

    def submit(
        self,
        request: KernelRequest,
        *,
        approval_summary: str = "",
        approval_ttl_s: float | None = 300.0,
    ) -> KernelSubmission:
        decision: AuthorizationDecision = self.policy.authorize(request)
        if not decision.allowed:
            self._emit(
                request,
                EventKind.SYSCALL_COMPLETED,
                success=False,
                payload={
                    "request_id": request.request_id,
                    "status": "rejected",
                    "reason": decision.reason,
                },
            )
            return KernelSubmission(
                accepted=False,
                request_id=request.request_id,
                queued=False,
                reason=decision.reason,
                requires_approval=False,
            )

        if decision.requires_approval:
            approval = self.approvals.create(
                mission_id=request.mission_id,
                request_id=request.request_id,
                agent_id=request.agent_id,
                capability=request.capability,
                summary=(
                    approval_summary.strip()
                    or f"Autoriser {request.capability}"
                ),
                risk=decision.risk,
                ttl_s=approval_ttl_s,
            )
            self._pending_approval_requests[
                approval.approval_id
            ] = request
            self._emit(
                request,
                EventKind.APPROVAL_REQUESTED,
                payload={
                    "request_id": request.request_id,
                    "approval_id": approval.approval_id,
                    "capability": request.capability,
                    "risk": decision.risk.value,
                },
            )
            return KernelSubmission(
                accepted=True,
                request_id=request.request_id,
                queued=False,
                reason="approval_required",
                approval_id=approval.approval_id,
                requires_approval=True,
            )

        self.scheduler.submit(request)
        self._emit(
            request,
            EventKind.SYSCALL_QUEUED,
            payload={
                "request_id": request.request_id,
                "capability": request.capability,
                "priority": request.priority,
            },
        )
        return KernelSubmission(
            accepted=True,
            request_id=request.request_id,
            queued=True,
            reason="queued",
            requires_approval=False,
        )

    def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> KernelSubmission | None:
        request = self._pending_approval_requests.get(
            str(approval_id)
        )
        if request is None:
            return None

        if not self.approvals.resolve(
            approval_id,
            approved=approved,
        ):
            return None

        self._emit(
            request,
            EventKind.APPROVAL_RESOLVED,
            success=approved,
            payload={
                "request_id": request.request_id,
                "approval_id": approval_id,
                "approved": bool(approved),
            },
        )

        if not approved:
            self._pending_approval_requests.pop(
                str(approval_id),
                None,
            )
            return KernelSubmission(
                accepted=False,
                request_id=request.request_id,
                queued=False,
                reason="user_denied",
                approval_id=approval_id,
                requires_approval=True,
            )

        if not self.approvals.consume(approval_id):
            return None

        self.scheduler.submit(request)
        self._pending_approval_requests.pop(
            str(approval_id),
            None,
        )
        self._emit(
            request,
            EventKind.SYSCALL_QUEUED,
            payload={
                "request_id": request.request_id,
                "capability": request.capability,
                "priority": request.priority,
                "approved": True,
            },
        )
        return KernelSubmission(
            accepted=True,
            request_id=request.request_id,
            queued=True,
            reason="approved_and_queued",
            approval_id=approval_id,
            requires_approval=True,
        )

    def next_request(
        self,
        *,
        timeout_s: float | None = None,
        allowed_agents: set[str] | None = None,
    ):
        scheduled = self.scheduler.next_request(
            timeout_s=timeout_s,
            allowed_agents=allowed_agents,
        )
        if scheduled is not None:
            self._emit(
                scheduled.request,
                EventKind.SYSCALL_STARTED,
                payload={
                    "request_id": scheduled.request.request_id,
                    "capability": scheduled.request.capability,
                },
            )
        return scheduled

    def complete(
        self,
        request_id: str,
        *,
        success: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> KernelResponse:
        scheduled = self.scheduler.get(request_id)
        if scheduled is None:
            raise KeyError("unknown_request_id")

        response = self.scheduler.complete(
            request_id,
            success=success,
            result=result,
            error=error,
        )
        self._emit(
            scheduled.request,
            EventKind.SYSCALL_COMPLETED,
            success=success,
            payload={
                "request_id": request_id,
                "status": response.status.value,
                "waiting_ms": response.waiting_ms,
                "turnaround_ms": response.turnaround_ms,
                "error": error,
            },
        )
        return response
