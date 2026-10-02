from __future__ import annotations

import uuid
from dataclasses import dataclass

from .kernel_contracts import (
    KernelRequest,
    MissionContext,
    MissionStatus,
    SyscallKind,
    SyscallStatus,
)
from .kernel_service import JarvisKernel, KernelSubmission
from .mission_context_store import MissionContextStore
from .task_graph import MissionTaskGraph, TaskNode, TaskStatus
from .task_graph_store import TaskGraphStore


@dataclass(frozen=True)
class OrchestrationStep:
    task_id: str
    request_id: str
    submission: KernelSubmission


class MissionOrchestrator:
    """Dependency-aware mission orchestration above the resource scheduler.

    This layer is intentionally passive until explicitly integrated with the
    live runtime. It turns ready DAG nodes into KernelRequests and reconciles
    completed syscalls back into mission/task state.
    """

    def __init__(
        self,
        *,
        kernel: JarvisKernel,
        context_store: MissionContextStore | None = None,
        graph_store: TaskGraphStore | None = None,
    ):
        self.kernel = kernel
        self.context_store = context_store or MissionContextStore()
        self.graph_store = graph_store or TaskGraphStore()
        self._graphs: dict[str, MissionTaskGraph] = {}
        self._request_to_task: dict[str, tuple[str, str]] = {}
        self._submitted_tasks: set[tuple[str, str]] = set()

    def register(
        self,
        context: MissionContext,
        graph: MissionTaskGraph,
    ) -> None:
        if context.mission_id != graph.mission_id:
            raise ValueError("mission_graph_context_mismatch")
        self._graphs[context.mission_id] = graph
        self.context_store.save(context)
        self.graph_store.save(graph)

    def graph(self, mission_id: str) -> MissionTaskGraph | None:
        key = str(mission_id)
        current = self._graphs.get(key)
        if current is not None:
            return current
        restored = self.graph_store.load(key)
        if restored is not None:
            self._graphs[key] = restored
        return restored

    @staticmethod
    def _kind_for(node: TaskNode) -> SyscallKind:
        raw = str(node.payload.get("syscall_kind") or "").strip()
        if raw:
            return SyscallKind(raw)
        if node.capability.startswith("communications."):
            return SyscallKind.CONNECTOR
        if node.capability.startswith("msf.") or node.capability.startswith("computer.") or node.capability.startswith("browser."):
            return SyscallKind.TOOL
        if node.capability.startswith("memory."):
            return SyscallKind.MEMORY
        if node.capability.startswith("developer.replay"):
            return SyscallKind.REPLAY
        if node.capability.startswith("developer.test"):
            return SyscallKind.TEST
        return SyscallKind.TOOL

    def dispatch_ready(
        self,
        mission_id: str,
    ) -> list[OrchestrationStep]:
        graph = self.graph(str(mission_id))
        if graph is None:
            raise KeyError("mission_graph_not_registered")

        loaded = self.context_store.load(mission_id)
        if loaded is None:
            raise KeyError("mission_context_not_found")
        context, version = loaded
        context.status = MissionStatus.RUNNING

        steps: list[OrchestrationStep] = []
        for node in graph.ready():
            key = (graph.mission_id, node.task_id)
            if key in self._submitted_tasks:
                continue
            request_id = f"r_{uuid.uuid4().hex}"
            payload = dict(node.payload)
            payload.setdefault("task_id", node.task_id)
            request = KernelRequest(
                request_id=request_id,
                mission_id=graph.mission_id,
                syscall_kind=self._kind_for(node),
                capability=node.capability,
                agent_id=node.agent_id,
                payload=payload,
                step_id=node.task_id,
                user_id=context.user_id,
                priority=node.priority,
            )
            submission = self.kernel.submit(
                request,
                approval_summary=str(
                    node.payload.get("approval_summary") or ""
                ),
            )
            if not submission.accepted:
                node.status = TaskStatus.FAILED
                node.error = submission.reason
                graph.cancel_downstream(node.task_id)
                context.status = MissionStatus.FAILED
            elif submission.requires_approval and not submission.queued:
                node.status = TaskStatus.WAITING_USER
                context.status = MissionStatus.WAITING_USER
                context.pending_confirmation = True
                context.pending_action = {
                    "task_id": node.task_id,
                    "request_id": request_id,
                    "approval_id": submission.approval_id,
                    "capability": node.capability,
                }
            else:
                node.status = TaskStatus.RUNNING
                context.current_step_id = node.task_id
                context.current_step = node.capability
            self._submitted_tasks.add(key)
            self._request_to_task[request_id] = (
                graph.mission_id,
                node.task_id,
            )
            steps.append(
                OrchestrationStep(
                    task_id=node.task_id,
                    request_id=request_id,
                    submission=submission,
                )
            )

        self.context_store.save(context, expected_version=version)
        self.graph_store.save(graph)
        return steps

    def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
    ) -> KernelSubmission | None:
        submission = self.kernel.resolve_approval(
            approval_id,
            approved=approved,
        )
        if submission is None:
            return None
        mapping = self._request_to_task.get(submission.request_id)
        if mapping is None:
            return submission
        mission_id, task_id = mapping
        graph = self._graphs.get(mission_id)
        loaded = self.context_store.load(mission_id)
        if graph is None or loaded is None:
            return submission
        context, version = loaded
        node = graph.get(task_id)
        if node is None:
            return submission

        context.pending_confirmation = False
        context.pending_action = {}
        if approved and submission.queued:
            node.status = TaskStatus.RUNNING
            context.status = MissionStatus.RUNNING
            context.current_step_id = task_id
            context.current_step = node.capability
        else:
            node.status = TaskStatus.FAILED
            node.error = submission.reason
            graph.cancel_downstream(task_id)
            context.status = MissionStatus.FAILED
        self.context_store.save(context, expected_version=version)
        self.graph_store.save(graph)
        return submission

    def complete_request(
        self,
        request_id: str,
        *,
        success: bool,
        result: dict | None = None,
        error: str = "",
    ):
        response = self.kernel.complete(
            request_id,
            success=success,
            result=result,
            error=error,
        )
        mapping = self._request_to_task.get(str(request_id))
        if mapping is None:
            return response
        mission_id, task_id = mapping
        graph = self._graphs[mission_id]
        node = graph.get(task_id)
        loaded = self.context_store.load(mission_id)
        if node is None or loaded is None:
            return response
        context, version = loaded

        if success:
            node.status = TaskStatus.COMPLETED
            node.result = dict(result or {})
            node.error = ""
            if graph.ready():
                context.status = MissionStatus.RUNNING
            elif all(
                item.status == TaskStatus.COMPLETED
                for item in graph.nodes()
            ):
                context.status = MissionStatus.COMPLETED
                context.current_step = ""
                context.current_step_id = None
            else:
                context.status = MissionStatus.RUNNING
        else:
            node.status = TaskStatus.FAILED
            node.error = str(error or "request_failed")[:1200]
            graph.cancel_downstream(task_id)
            context.status = MissionStatus.FAILED

        self.context_store.save(context, expected_version=version)
        self.graph_store.save(graph)
        return response

    def restore_runtime_state(self, mission_id: str) -> dict[str, list[str]]:
        """Rebuild in-memory request/task links after a process restart.

        QUEUED requests can be safely rehydrated by the Kernel. RUNNING
        requests are only reported for recovery; they are never auto-replayed.
        """
        graph = self.graph(mission_id)
        if graph is None:
            raise KeyError("mission_graph_not_registered")

        store = self.kernel.request_store
        if store is None:
            return {
                "queued_restored": [],
                "recovery_required": [],
                "waiting_approval": [],
            }

        rows = store.for_mission(
            mission_id,
            statuses=(
                SyscallStatus.QUEUED,
                SyscallStatus.RUNNING,
                SyscallStatus.WAITING_APPROVAL,
            ),
        )
        recovery_required: list[str] = []
        waiting_approval: list[str] = []

        for row in rows:
            request = row["request"]
            status = row["status"]
            task_id = str(request.step_id or "")
            if task_id:
                self._request_to_task[request.request_id] = (
                    str(mission_id),
                    task_id,
                )
                self._submitted_tasks.add(
                    (str(mission_id), task_id)
                )
            if status == SyscallStatus.RUNNING:
                recovery_required.append(request.request_id)
            elif status == SyscallStatus.WAITING_APPROVAL:
                waiting_approval.append(request.request_id)

        queued_restored = self.kernel.restore_queued_requests()
        return {
            "queued_restored": [
                request_id
                for request_id in queued_restored
                if any(
                    row["request"].request_id == request_id
                    for row in rows
                )
            ],
            "recovery_required": recovery_required,
            "waiting_approval": waiting_approval,
        }

    def resumable(self, *, user_id: str | None = None) -> list[dict]:
        return self.context_store.list_resumable(user_id=user_id)
