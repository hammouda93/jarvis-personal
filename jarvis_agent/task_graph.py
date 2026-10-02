from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    WAITING_EXTERNAL = "waiting_external"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskNode:
    task_id: str
    mission_id: str
    capability: str
    agent_id: str
    dependencies: set[str] = field(default_factory=set)
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 100
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class MissionTaskGraph:
    """Dependency-aware mission DAG, deliberately separate from scheduler."""

    def __init__(self, mission_id: str):
        self.mission_id = str(mission_id)
        self._nodes: dict[str, TaskNode] = {}

    def add(self, node: TaskNode) -> None:
        if node.mission_id != self.mission_id:
            raise ValueError("task_mission_mismatch")
        if node.task_id in self._nodes:
            raise ValueError("duplicate_task_id")
        if node.task_id in node.dependencies:
            raise ValueError("task_cannot_depend_on_itself")
        self._nodes[node.task_id] = node
        self._validate_acyclic()

    def get(self, task_id: str) -> TaskNode | None:
        return self._nodes.get(str(task_id))

    def nodes(self) -> list[TaskNode]:
        return sorted(
            self._nodes.values(),
            key=lambda item: (item.priority, item.task_id),
        )

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                raise ValueError("task_dependency_cycle")
            visiting.add(task_id)
            node = self._nodes[task_id]
            for dep in node.dependencies:
                if dep in self._nodes:
                    visit(dep)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in list(self._nodes):
            visit(task_id)

    def unresolved_dependencies(self, task_id: str) -> set[str]:
        node = self._nodes[str(task_id)]
        unresolved: set[str] = set()
        for dep_id in node.dependencies:
            dep = self._nodes.get(dep_id)
            if dep is None or dep.status != TaskStatus.COMPLETED:
                unresolved.add(dep_id)
        return unresolved

    def ready(self) -> list[TaskNode]:
        ready: list[TaskNode] = []
        for node in self._nodes.values():
            if node.status not in {TaskStatus.PENDING, TaskStatus.READY}:
                continue
            if not self.unresolved_dependencies(node.task_id):
                node.status = TaskStatus.READY
                ready.append(node)
        return sorted(
            ready,
            key=lambda item: (item.priority, item.task_id),
        )

    def mark_running(self, task_id: str) -> None:
        node = self._nodes[str(task_id)]
        if self.unresolved_dependencies(task_id):
            raise RuntimeError("task_dependencies_not_completed")
        if node.status not in {TaskStatus.PENDING, TaskStatus.READY}:
            raise RuntimeError("task_not_runnable")
        node.status = TaskStatus.RUNNING

    def mark_completed(
        self,
        task_id: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        node = self._nodes[str(task_id)]
        if node.status not in {
            TaskStatus.RUNNING,
            TaskStatus.WAITING_USER,
            TaskStatus.WAITING_EXTERNAL,
        }:
            raise RuntimeError("task_not_active")
        node.status = TaskStatus.COMPLETED
        node.result = dict(result or {})
        node.error = ""

    def mark_failed(self, task_id: str, error: str) -> None:
        node = self._nodes[str(task_id)]
        node.status = TaskStatus.FAILED
        node.error = str(error or "")[:1200]

    def pause_for_user(self, task_id: str) -> None:
        node = self._nodes[str(task_id)]
        if node.status != TaskStatus.RUNNING:
            raise RuntimeError("task_not_running")
        node.status = TaskStatus.WAITING_USER

    def pause_for_external(self, task_id: str) -> None:
        node = self._nodes[str(task_id)]
        if node.status != TaskStatus.RUNNING:
            raise RuntimeError("task_not_running")
        node.status = TaskStatus.WAITING_EXTERNAL

    def cancel_downstream(self, failed_task_id: str) -> list[str]:
        cancelled: list[str] = []
        changed = True
        blocked = {str(failed_task_id)}
        while changed:
            changed = False
            for node in self._nodes.values():
                if node.status in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }:
                    continue
                if node.dependencies & blocked:
                    node.status = TaskStatus.CANCELLED
                    blocked.add(node.task_id)
                    cancelled.append(node.task_id)
                    changed = True
        return cancelled

    def summary(self) -> dict[str, int]:
        result = {status.value: 0 for status in TaskStatus}
        for node in self._nodes.values():
            result[node.status.value] += 1
        return result
