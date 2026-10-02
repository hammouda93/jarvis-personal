from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .approval_manager import HumanApprovalManager
from .event_bus import BusEvent, MissionEventBus
from .event_journal import StructuredEventJournal
from .kernel_contracts import (
    EventKind,
    KernelRequest,
    KernelResponse,
    SyscallStatus,
)
from .kernel_policy import AuthorizationDecision, KernelPolicy
from .mission_scheduler import MissionScheduler
from .kernel_request_store import KernelRequestStore


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
        request_store: KernelRequestStore | None = None,
    ):
        self.policy = policy or KernelPolicy()
        if scheduler is None:
            concurrency_limits = {
                manifest.agent_id: manifest.max_concurrency
                for manifest in self.policy.registry.agents()
            }
            self.scheduler = MissionScheduler(
                concurrency_limits=concurrency_limits
            )
        else:
            self.scheduler = scheduler
        self.approvals = approvals or HumanApprovalManager()
        self.event_bus = event_bus or MissionEventBus()
        self.journal = journal
        self.request_store = request_store
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
            if self.request_store is not None:
                self.request_store.put(
                    request,
                    status=SyscallStatus.FAILED,
                    error=decision.reason,
                )
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
            if self.request_store is not None:
                self.request_store.put(
                    request,
                    status=SyscallStatus.WAITING_APPROVAL,
                )
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
        if self.request_store is not None:
            self.request_store.put(
                request,
                status=SyscallStatus.QUEUED,
            )
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
            if self.request_store is not None:
                self.request_store.mark(
                    request.request_id,
                    SyscallStatus.CANCELLED,
                    error="user_denied",
                )
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
        if self.request_store is not None:
            self.request_store.put(
                request,
                status=SyscallStatus.QUEUED,
            )
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
            if self.request_store is not None:
                self.request_store.mark(
                    scheduled.request.request_id,
                    SyscallStatus.RUNNING,
                )
            self._emit(
                scheduled.request,
                EventKind.SYSCALL_STARTED,
                payload={
                    "request_id": scheduled.request.request_id,
                    "capability": scheduled.request.capability,
                },
            )
        return scheduled

    def restore_pending_approvals(self) -> list[str]:
        """Rebuild approval_id -> request mapping from persistent stores."""
        if self.request_store is None:
            return []
        restored: list[str] = []
        for approval in self.approvals.pending():
            row = self.request_store.get(approval.request_id)
            if row is None:
                continue
            if row["status"] != SyscallStatus.WAITING_APPROVAL:
                continue
            request = row["request"]
            self._pending_approval_requests[
                approval.approval_id
            ] = request
            restored.append(approval.approval_id)
        return restored

    def restore_queued_requests(self) -> list[str]:
        """Rehydrate only requests known to be safely QUEUED.

        RUNNING requests are deliberately excluded because their external
        side effects may already have happened before a crash.
        """
        if self.request_store is None:
            return []
        restored: list[str] = []
        for request in self.request_store.by_status(
            SyscallStatus.QUEUED
        ):
            if self.scheduler.get(request.request_id) is not None:
                continue
            self.scheduler.submit(request)
            restored.append(request.request_id)
        return restored

    def recovery_required_requests(self) -> list[KernelRequest]:
        if self.request_store is None:
            return []
        return self.request_store.recovery_required()

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
        if self.request_store is not None:
            self.request_store.mark(
                request_id,
                response.status,
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
