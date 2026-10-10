from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .connector_gateway import ConnectorGateway
from .kernel_contracts import (
    KernelRequest,
    KnowledgeIdentity,
    KnowledgeScope,
    SharingPolicy,
    SyscallKind,
)
from .knowledge_broker import (
    KnowledgeBroker,
    KnowledgePrincipal,
    ScopedKnowledgeRecord,
)
from .tool_gateway import ScopedToolGateway
from .workspace_storage import WorkspaceStorage
from .regression_runner import RegressionRunner
from .replay_sandbox import ReplayAction, ReplayPlan, ReplayRunner


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
        approved = bool(
            request.payload.get("_kernel_approved") is True
            and request.payload.get("_kernel_approval_id")
        )
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
                "outcome_unknown": response.outcome_unknown,
                "verified": response.verified,
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

        if operation == "write":
            raw = dict(request.payload.get("record") or {})
            identity_raw = dict(raw.get("identity") or {})
            try:
                identity = KnowledgeIdentity(
                    scope=KnowledgeScope(
                        str(identity_raw.get("scope") or "")
                    ),
                    owner_user_id=identity_raw.get("owner_user_id"),
                    owner_agent_id=identity_raw.get("owner_agent_id"),
                    organization_id=identity_raw.get("organization_id"),
                    app_id=identity_raw.get("app_id"),
                    domain=identity_raw.get("domain"),
                    skill_id=identity_raw.get("skill_id"),
                    sharing_policy=SharingPolicy(
                        str(
                            identity_raw.get("sharing_policy")
                            or SharingPolicy.PRIVATE.value
                        )
                    ),
                )
            except (TypeError, ValueError) as exc:
                return ManagerExecutionResult(
                    success=False,
                    result={},
                    error=f"invalid_knowledge_identity:{exc}",
                )

            record = ScopedKnowledgeRecord(
                knowledge_id=str(raw.get("knowledge_id") or ""),
                identity=identity,
                content=str(raw.get("content") or ""),
                metadata=dict(raw.get("metadata") or {}),
                relevance=float(raw.get("relevance") or 0.0),
            )
            if not record.knowledge_id or not record.content:
                return ManagerExecutionResult(
                    success=False,
                    result={},
                    error="knowledge_id_and_content_required",
                )

            written = self.broker.write(
                record,
                principal=self._principal(request),
            )
            return ManagerExecutionResult(
                success=written,
                result={
                    "knowledge_id": record.knowledge_id,
                    "written": bool(written),
                    "scope": record.identity.scope.value,
                },
                error="" if written else "knowledge_write_denied",
            )

        return ManagerExecutionResult(
            success=False,
            result={},
            error="unsupported_memory_operation",
        )


class StorageExecutionManager:
    syscall_kind = SyscallKind.STORAGE

    def __init__(self, storage: WorkspaceStorage):
        self.storage = storage

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        payload = dict(request.payload or {})
        operation = str(payload.get("operation") or "").strip()
        workspace_id = str(payload.get("workspace_id") or "").strip()
        if not workspace_id:
            return ManagerExecutionResult(
                success=False,
                result={},
                error="workspace_id_required",
            )

        try:
            if operation == "write_text":
                artifact = self.storage.write_text(
                    workspace_id,
                    str(payload.get("path") or ""),
                    str(payload.get("content") or ""),
                )
                return ManagerExecutionResult(
                    success=True,
                    result={
                        "workspace_id": artifact.workspace_id,
                        "path": artifact.relative_path,
                        "size": artifact.size,
                    },
                )

            if operation == "read_text":
                text = self.storage.read_text(
                    workspace_id,
                    str(payload.get("path") or ""),
                    max_chars=int(payload.get("max_chars") or 200000),
                )
                return ManagerExecutionResult(
                    success=True,
                    result={
                        "workspace_id": workspace_id,
                        "path": str(payload.get("path") or ""),
                        "content": text,
                    },
                )

            if operation == "list_files":
                items = self.storage.list_files(
                    workspace_id,
                    limit=int(payload.get("limit") or 500),
                )
                return ManagerExecutionResult(
                    success=True,
                    result={
                        "workspace_id": workspace_id,
                        "files": [
                            {
                                "path": item.relative_path,
                                "size": item.size,
                            }
                            for item in items
                        ],
                    },
                )

            if operation == "reset":
                self.storage.reset(workspace_id)
                return ManagerExecutionResult(
                    success=True,
                    result={
                        "workspace_id": workspace_id,
                        "reset": True,
                    },
                )
        except Exception as exc:
            return ManagerExecutionResult(
                success=False,
                result={},
                error=str(exc)[:1200],
            )

        return ManagerExecutionResult(
            success=False,
            result={},
            error="unsupported_storage_operation",
        )


class TestExecutionManager:
    syscall_kind = SyscallKind.TEST

    def __init__(self, runner: RegressionRunner):
        self.runner = runner

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        payload = dict(request.payload or {})
        test_ids = [
            str(item)
            for item in payload.get("test_ids") or ()
            if str(item)
        ]
        if not test_ids:
            return ManagerExecutionResult(
                success=False,
                result={},
                error="test_ids_required",
            )

        results = self.runner.run_many(
            test_ids,
            stop_on_failure=bool(
                payload.get("stop_on_failure", True)
            ),
        )
        return ManagerExecutionResult(
            success=bool(results) and all(item.success for item in results),
            result={
                "tests": [
                    {
                        "test_id": item.test_id,
                        "success": item.success,
                        "returncode": item.returncode,
                        "duration_s": round(item.duration_s, 3),
                        "stdout": item.stdout,
                        "stderr": item.stderr,
                        "error": item.error,
                    }
                    for item in results
                ]
            },
            error=(
                ""
                if results and all(item.success for item in results)
                else "regression_failed"
            ),
        )


class ReplayExecutionManager:
    syscall_kind = SyscallKind.REPLAY

    def __init__(self, runner: ReplayRunner):
        self.runner = runner

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        payload = dict(request.payload or {})
        replay_id = str(payload.get("replay_id") or "").strip()
        environment_id = str(
            payload.get("environment_id") or ""
        ).strip()
        if not replay_id or not environment_id:
            return ManagerExecutionResult(
                success=False,
                result={},
                error="replay_identity_required",
            )

        actions = [
            ReplayAction(
                action_type=str(item.get("action_type") or ""),
                arguments=dict(item.get("arguments") or {}),
            )
            for item in payload.get("actions") or ()
            if isinstance(item, dict)
            and str(item.get("action_type") or "")
        ]
        plan = ReplayPlan(
            replay_id=replay_id,
            mission_id=request.mission_id,
            environment_id=environment_id,
            actions=actions,
            expected_state=dict(payload.get("expected_state") or {}),
            test_ids=[
                str(item)
                for item in payload.get("test_ids") or ()
                if str(item)
            ],
            record_video=bool(payload.get("record_video")),
        )
        result = self.runner.run(plan)
        return ManagerExecutionResult(
            success=result.success,
            result=result.as_dict(),
            error=result.error,
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
