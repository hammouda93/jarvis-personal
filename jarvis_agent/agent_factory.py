from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

from .capability_registry import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
)
from .kernel_contracts import AgentManifest


class AgentLifecycle(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


class AgentInstance(Protocol):
    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        ...


AgentBuilder = Callable[[], AgentInstance]


@dataclass
class AgentProcess:
    process_id: str
    agent_id: str
    mission_id: str
    parent_process_id: str | None
    lifecycle: AgentLifecycle
    created_at: float
    updated_at: float
    result: dict[str, Any] | None = None
    error: str = ""


class AgentFactory:
    """Explicit in-process agent factory with lifecycle bookkeeping.

    No arbitrary module import occurs here. Builders must be registered by
    trusted Jarvis code before an agent can be spawned.
    """

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
    ):
        self.registry = registry or DEFAULT_CAPABILITY_REGISTRY
        self._builders: dict[str, AgentBuilder] = {}
        self._instances: dict[str, AgentInstance] = {}
        self._processes: dict[str, AgentProcess] = {}
        self._lock = threading.RLock()

    def register_builder(
        self,
        agent_id: str,
        builder: AgentBuilder,
    ) -> None:
        manifest = self.registry.get_agent(agent_id)
        if manifest is None:
            raise ValueError("agent_manifest_not_registered")
        if not callable(builder):
            raise TypeError("agent_builder_not_callable")
        with self._lock:
            self._builders[str(agent_id)] = builder

    def manifest(self, agent_id: str) -> AgentManifest | None:
        return self.registry.get_agent(agent_id)

    def _active_count(self, agent_id: str) -> int:
        return sum(
            1
            for process in self._processes.values()
            if process.agent_id == agent_id
            and process.lifecycle
            in {
                AgentLifecycle.CREATED,
                AgentLifecycle.RUNNING,
                AgentLifecycle.SUSPENDED,
            }
        )

    def spawn(
        self,
        *,
        agent_id: str,
        mission_id: str,
        parent_process_id: str | None = None,
    ) -> AgentProcess:
        manifest = self.registry.get_agent(agent_id)
        if manifest is None:
            raise ValueError("agent_manifest_not_registered")
        builder = self._builders.get(str(agent_id))
        if builder is None:
            raise RuntimeError("agent_builder_not_registered")

        with self._lock:
            if self._active_count(agent_id) >= max(
                1,
                int(manifest.max_concurrency),
            ):
                raise RuntimeError("agent_concurrency_limit")
            instance = builder()
            process_id = f"p_{uuid.uuid4().hex}"
            now = time.time()
            process = AgentProcess(
                process_id=process_id,
                agent_id=str(agent_id),
                mission_id=str(mission_id),
                parent_process_id=parent_process_id,
                lifecycle=AgentLifecycle.CREATED,
                created_at=now,
                updated_at=now,
            )
            self._instances[process_id] = instance
            self._processes[process_id] = process
            return process

    def execute(
        self,
        process_id: str,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            process = self._processes.get(str(process_id))
            instance = self._instances.get(str(process_id))
            if process is None or instance is None:
                raise KeyError("unknown_agent_process")
            if process.lifecycle == AgentLifecycle.SUSPENDED:
                raise RuntimeError("agent_process_suspended")
            if process.lifecycle == AgentLifecycle.RUNNING:
                raise RuntimeError("agent_process_already_running")
            if process.lifecycle in {
                AgentLifecycle.COMPLETED,
                AgentLifecycle.FAILED,
                AgentLifecycle.TERMINATED,
            }:
                raise RuntimeError("agent_process_not_active")
            process.lifecycle = AgentLifecycle.RUNNING
            process.updated_at = time.time()

        try:
            result = dict(instance.run(dict(task or {})) or {})
        except Exception as exc:
            with self._lock:
                if process.lifecycle != AgentLifecycle.TERMINATED:
                    process.lifecycle = AgentLifecycle.FAILED
                process.error = str(exc)[:1200]
                process.updated_at = time.time()
                self._instances.pop(str(process_id), None)
            raise

        with self._lock:
            if process.lifecycle != AgentLifecycle.TERMINATED:
                process.lifecycle = AgentLifecycle.COMPLETED
            process.result = result
            process.updated_at = time.time()
            self._instances.pop(str(process_id), None)
        return result

    def suspend(self, process_id: str) -> bool:
        with self._lock:
            process = self._processes.get(str(process_id))
            if process is None:
                return False
            if process.lifecycle not in {
                AgentLifecycle.CREATED,
            }:
                return False
            process.lifecycle = AgentLifecycle.SUSPENDED
            process.updated_at = time.time()
            return True

    def resume(self, process_id: str) -> bool:
        with self._lock:
            process = self._processes.get(str(process_id))
            if process is None:
                return False
            if process.lifecycle != AgentLifecycle.SUSPENDED:
                return False
            process.lifecycle = AgentLifecycle.CREATED
            process.updated_at = time.time()
            return True

    def terminate(self, process_id: str) -> bool:
        with self._lock:
            process = self._processes.get(str(process_id))
            if process is None:
                return False
            if process.lifecycle in {
                AgentLifecycle.COMPLETED,
                AgentLifecycle.FAILED,
                AgentLifecycle.TERMINATED,
            }:
                return False
            process.lifecycle = AgentLifecycle.TERMINATED
            process.updated_at = time.time()
            self._instances.pop(str(process_id), None)
            return True

    def get(self, process_id: str) -> AgentProcess | None:
        with self._lock:
            return self._processes.get(str(process_id))

    def children(self, parent_process_id: str) -> list[AgentProcess]:
        with self._lock:
            return sorted(
                [
                    process
                    for process in self._processes.values()
                    if process.parent_process_id == parent_process_id
                ],
                key=lambda item: item.created_at,
            )
