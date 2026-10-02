from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .connector_gateway import ConnectorGateway
from .kernel_contracts import KernelRequest, SyscallKind
from .knowledge_broker import KnowledgeBroker, KnowledgePrincipal
from .tool_gateway import ScopedToolGateway


@dataclass(frozen=True)
class ManagerExecutionResult:
    success: bool
    result: dict[str, Any]
    error: str = ""


class ExecutionManager(Protocol):
    syscall_kind: SyscallKind

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        ...


class ToolExecutionManager:
    syscall_kind = SyscallKind.TOOL

    def __init__(self, gateway: ScopedToolGateway):
        self.gateway = gateway

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        tool_name = str(request.payload.get("tool_name") or "")
        arguments = dict(request.payload.get("arguments") or {})
        response = self.gateway.execute(
            agent_id=request.agent_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        return ManagerExecutionResult(
            success=response.success,
            result={
                "tool_name": response.tool_name,
                "message": response.message,
                "detail": response.detail or {},
            },
            error=response.error,
        )


class ConnectorExecutionManager:
    syscall_kind = SyscallKind.CONNECTOR

    def __init__(self, gateway: ConnectorGateway):
        self.gateway = gateway

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        connector_id = str(request.payload.get("connector_id") or "")
        capability = str(
            request.payload.get("connector_capability")
            or request.capability
        )
        arguments = dict(request.payload.get("arguments") or {})
        approved = bool(request.payload.get("approved"))
        response = self.gateway.execute(
            connector_id=connector_id,
            capability=capability,
            arguments=arguments,
            approved=approved,
        )
        return ManagerExecutionResult(
            success=response.success,
            result={
                "connector_id": response.connector_id,
                "capability": response.capability,
                "message": response.message,
                "data": response.data or {},
            },
            error=response.error,
        )


class MemoryExecutionManager:
    syscall_kind = SyscallKind.MEMORY

    def __init__(self, broker: KnowledgeBroker):
        self.broker = broker

    @staticmethod
    def _principal(request: KernelRequest) -> KnowledgePrincipal:
        return KnowledgePrincipal(
            user_id=request.user_id,
            agent_id=request.agent_id,
            organization_id=(
                request.payload.get("organization_id")
                if isinstance(request.payload, dict)
                else None
            ),
        )

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        operation = str(request.payload.get("operation") or "search")
        if operation == "search":
            query = str(request.payload.get("query") or "")
            records = self.broker.search(
                query,
                principal=self._principal(request),
                limit=int(request.payload.get("limit") or 8),
            )
            return ManagerExecutionResult(
                success=True,
                result={
                    "records": [
                        {
                            "knowledge_id": item.knowledge_id,
                            "content": item.content,
                            "identity": item.identity.as_dict(),
                            "metadata": dict(item.metadata),
                            "relevance": item.relevance,
                        }
                        for item in records
                    ]
                },
            )
        return ManagerExecutionResult(
            success=False,
            result={},
            error="unsupported_memory_operation",
        )


class CallbackExecutionManager:
    """Small adapter for LLM/storage/test/replay managers."""

    def __init__(
        self,
        syscall_kind: SyscallKind,
        callback: Callable[[KernelRequest], ManagerExecutionResult],
    ):
        self.syscall_kind = syscall_kind
        self.callback = callback

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        return self.callback(request)


class ExecutionManagerRegistry:
    def __init__(self):
        self._managers: dict[SyscallKind, ExecutionManager] = {}

    def register(self, manager: ExecutionManager) -> None:
        self._managers[manager.syscall_kind] = manager

    def get(self, kind: SyscallKind) -> ExecutionManager | None:
        return self._managers.get(kind)

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        manager = self.get(request.syscall_kind)
        if manager is None:
            return ManagerExecutionResult(
                success=False,
                result={},
                error="execution_manager_unavailable",
            )
        return manager.execute(request)
