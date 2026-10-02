from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .kernel_contracts import (
    KernelRequest,
    KernelResponse,
    SyscallKind,
    SyscallStatus,
)


@dataclass(order=True)
class _QueueItem:
    priority: int
    sequence: int
    request_id: str = field(compare=False)


@dataclass
class ScheduledRequest:
    request: KernelRequest
    status: SyscallStatus
    queued_at: float
    started_at: float | None = None
    ended_at: float | None = None
    error: str = ""


class MissionScheduler:
    """Small priority/FIFO scheduler contract for future multi-agent execution."""

    def __init__(
        self,
        *,
        concurrency_limits: dict[str, int] | None = None,
        resource_limits: dict[SyscallKind | str, int] | None = None,
    ):
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._sequence = 0
        self._queue: list[_QueueItem] = []
        self._items: dict[str, ScheduledRequest] = {}
        self._active_by_agent: dict[str, int] = {}
        self._active_by_kind: dict[str, int] = {}
        self._concurrency_limits = {
            str(agent_id): max(1, int(limit))
            for agent_id, limit in dict(
                concurrency_limits or {}
            ).items()
        }
        self._resource_limits = {
            (
                kind.value
                if isinstance(kind, SyscallKind)
                else str(kind)
            ): max(1, int(limit))
            for kind, limit in dict(resource_limits or {}).items()
        }

    def set_concurrency_limit(
        self,
        agent_id: str,
        limit: int,
    ) -> None:
        with self._condition:
            self._concurrency_limits[str(agent_id)] = max(
                1,
                int(limit),
            )
            self._condition.notify_all()

    def set_resource_limit(
        self,
        kind: SyscallKind | str,
        limit: int,
    ) -> None:
        key = kind.value if isinstance(kind, SyscallKind) else str(kind)
        with self._condition:
            self._resource_limits[key] = max(1, int(limit))
            self._condition.notify_all()

    def _resource_has_capacity(self, kind: SyscallKind) -> bool:
        key = kind.value
        limit = self._resource_limits.get(key)
        if limit is None:
            return True
        return self._active_by_kind.get(key, 0) < limit

    def _agent_has_capacity(self, agent_id: str) -> bool:
        key = str(agent_id)
        limit = self._concurrency_limits.get(key)
        if limit is None:
            return True
        return self._active_by_agent.get(key, 0) < limit

    def submit(self, request: KernelRequest) -> None:
        with self._condition:
            if request.request_id in self._items:
                raise ValueError("duplicate_request_id")
            now = time.time()
            if request.created_at is None:
                request.created_at = now
            self._sequence += 1
            scheduled = ScheduledRequest(
                request=request,
                status=SyscallStatus.QUEUED,
                queued_at=now,
            )
            self._items[request.request_id] = scheduled
            heapq.heappush(
                self._queue,
                _QueueItem(
                    priority=int(request.priority),
                    sequence=self._sequence,
                    request_id=request.request_id,
                ),
            )
            self._condition.notify_all()

    def next_request(
        self,
        *,
        timeout_s: float | None = None,
        allowed_agents: set[str] | None = None,
    ) -> ScheduledRequest | None:
        deadline = (
            None
            if timeout_s is None
            else time.monotonic() + max(0.0, float(timeout_s))
        )
        with self._condition:
            while True:
                skipped: list[_QueueItem] = []
                chosen: _QueueItem | None = None
                while self._queue:
                    candidate = heapq.heappop(self._queue)
                    scheduled = self._items.get(candidate.request_id)
                    if scheduled is None:
                        continue
                    if scheduled.status != SyscallStatus.QUEUED:
                        continue
                    if (
                        allowed_agents is not None
                        and scheduled.request.agent_id not in allowed_agents
                    ):
                        skipped.append(candidate)
                        continue
                    if not self._agent_has_capacity(
                        scheduled.request.agent_id
                    ):
                        skipped.append(candidate)
                        continue
                    if not self._resource_has_capacity(
                        scheduled.request.syscall_kind
                    ):
                        skipped.append(candidate)
                        continue
                    chosen = candidate
                    break

                for item in skipped:
                    heapq.heappush(self._queue, item)

                if chosen is not None:
                    scheduled = self._items[chosen.request_id]
                    scheduled.status = SyscallStatus.RUNNING
                    scheduled.started_at = time.time()
                    agent_id = scheduled.request.agent_id
                    self._active_by_agent[agent_id] = (
                        self._active_by_agent.get(agent_id, 0) + 1
                    )
                    kind_key = scheduled.request.syscall_kind.value
                    self._active_by_kind[kind_key] = (
                        self._active_by_kind.get(kind_key, 0) + 1
                    )
                    return scheduled

                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._condition.wait(timeout=remaining)
                else:
                    return None

    def cancel(self, request_id: str) -> bool:
        with self._condition:
            scheduled = self._items.get(str(request_id))
            if scheduled is None:
                return False
            if scheduled.status in {
                SyscallStatus.SUCCEEDED,
                SyscallStatus.FAILED,
                SyscallStatus.CANCELLED,
            }:
                return False
            was_running = scheduled.status == SyscallStatus.RUNNING
            scheduled.status = SyscallStatus.CANCELLED
            scheduled.ended_at = time.time()
            if was_running:
                agent_id = scheduled.request.agent_id
                self._active_by_agent[agent_id] = max(
                    0,
                    self._active_by_agent.get(agent_id, 0) - 1,
                )
                kind_key = scheduled.request.syscall_kind.value
                self._active_by_kind[kind_key] = max(
                    0,
                    self._active_by_kind.get(kind_key, 0) - 1,
                )
            self._condition.notify_all()
            return True

    def complete(
        self,
        request_id: str,
        *,
        success: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> KernelResponse:
        with self._condition:
            scheduled = self._items.get(str(request_id))
            if scheduled is None:
                raise KeyError("unknown_request_id")
            if scheduled.status == SyscallStatus.CANCELLED:
                raise RuntimeError("request_cancelled")

            now = time.time()
            if scheduled.started_at is None:
                scheduled.started_at = now
            scheduled.ended_at = now
            was_running = scheduled.status == SyscallStatus.RUNNING
            scheduled.status = (
                SyscallStatus.SUCCEEDED
                if success
                else SyscallStatus.FAILED
            )
            scheduled.error = str(error or "")
            if was_running:
                agent_id = scheduled.request.agent_id
                self._active_by_agent[agent_id] = max(
                    0,
                    self._active_by_agent.get(agent_id, 0) - 1,
                )
                kind_key = scheduled.request.syscall_kind.value
                self._active_by_kind[kind_key] = max(
                    0,
                    self._active_by_kind.get(kind_key, 0) - 1,
                )
            self._condition.notify_all()

            created = (
                scheduled.request.created_at
                if scheduled.request.created_at is not None
                else scheduled.queued_at
            )
            waiting_ms = max(
                0.0,
                (scheduled.started_at - scheduled.queued_at) * 1000.0,
            )
            turnaround_ms = max(
                0.0,
                (now - created) * 1000.0,
            )
            return KernelResponse(
                request_id=scheduled.request.request_id,
                mission_id=scheduled.request.mission_id,
                status=scheduled.status,
                success=success,
                result=dict(result or {}),
                error=str(error or ""),
                started_at=scheduled.started_at,
                ended_at=scheduled.ended_at,
                waiting_ms=waiting_ms,
                turnaround_ms=turnaround_ms,
            )

    def get(self, request_id: str) -> ScheduledRequest | None:
        with self._lock:
            return self._items.get(str(request_id))

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {
                status.value: 0 for status in SyscallStatus
            }
            for item in self._items.values():
                counts[item.status.value] += 1
            counts["queue_depth"] = sum(
                1
                for item in self._items.values()
                if item.status == SyscallStatus.QUEUED
            )
            counts["active_total"] = sum(
                self._active_by_agent.values()
            )
            for kind, active in self._active_by_kind.items():
                counts[f"active_{kind}"] = int(active)
            return counts
