from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Iterable


@dataclass(frozen=True)
class RegressionSpec:
    test_id: str
    command: str
    tags: tuple[str, ...]
    watched_paths: tuple[str, ...] = ()
    level: str = "unit"
    description: str = ""

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        data["watched_paths"] = list(self.watched_paths)
        return data


class RegressionRegistry:
    def __init__(self):
        self._tests: dict[str, RegressionSpec] = {}

    def register(self, spec: RegressionSpec) -> None:
        self._tests[spec.test_id] = spec

    def get(self, test_id: str) -> RegressionSpec | None:
        return self._tests.get(str(test_id))

    def all(self) -> list[RegressionSpec]:
        return sorted(self._tests.values(), key=lambda item: item.test_id)

    def select(
        self,
        *,
        tags: Iterable[str] = (),
        changed_paths: Iterable[str] = (),
    ) -> list[RegressionSpec]:
        wanted_tags = {str(tag) for tag in tags if str(tag)}
        paths = [
            str(PurePosixPath(str(path).replace("\\", "/")))
            for path in changed_paths
            if str(path)
        ]

        selected: list[RegressionSpec] = []
        for spec in self._tests.values():
            tag_match = bool(wanted_tags & set(spec.tags))
            path_match = any(
                _path_matches(path, watched)
                for path in paths
                for watched in spec.watched_paths
            )
            if not wanted_tags and not paths:
                continue
            if tag_match or path_match:
                selected.append(spec)
        return sorted(selected, key=lambda item: item.test_id)


def _path_matches(path: str, watched: str) -> bool:
    normalized_path = str(PurePosixPath(path))
    normalized_watched = str(PurePosixPath(watched))
    if normalized_watched.endswith("/*"):
        prefix = normalized_watched[:-1]
        return normalized_path.startswith(prefix)
    return (
        normalized_path == normalized_watched
        or normalized_path.startswith(normalized_watched.rstrip("/") + "/")
    )


def build_default_regression_registry() -> RegressionRegistry:
    registry = RegressionRegistry()

    registry.register(
        RegressionSpec(
            test_id="TEST-WIN-BASELINE",
            command=(
                "powershell -ExecutionPolicy Bypass "
                "-File .\\scripts\\run_baseline_regression.ps1"
            ),
            tags=("windows", "uia", "runtime", "baseline"),
            watched_paths=(
                "jarvis_agent/windows_perception.py",
                "jarvis_agent/native_tools.py",
                "jarvis_agent/agent_runtime.py",
            ),
            level="suite",
            description=(
                "Historical Windows/tool/runtime non-regression suite."
            ),
        )
    )
    registry.register(
        RegressionSpec(
            test_id="TEST-VISION-LAYER",
            command=(
                "powershell -ExecutionPolicy Bypass "
                "-File .\\scripts\\run_vision_validation.ps1"
            ),
            tags=("vision", "uia", "runtime"),
            watched_paths=(
                "jarvis_agent/screen_vision.py",
                "jarvis_agent/windows_perception.py",
                "jarvis_agent/agent_runtime.py",
            ),
            level="suite",
            description=(
                "Staged UIA plus local vision validation."
            ),
        )
    )
    registry.register(
        RegressionSpec(
            test_id="TEST-KERNEL-FOUNDATIONS",
            command=(
                "powershell -ExecutionPolicy Bypass "
                "-File .\\scripts\\run_architecture_foundations_validation.ps1"
            ),
            tags=(
                "kernel",
                "scheduler",
                "multi_agent",
                "observability",
                "permissions",
                "connectors",
                "replay",
            ),
            watched_paths=(
                "jarvis_agent/kernel_contracts.py",
                "jarvis_agent/kernel_service.py",
                "jarvis_agent/kernel_stack.py",
                "jarvis_agent/kernel_policy.py",
                "jarvis_agent/kernel_request_store.py",
                "jarvis_agent/kernel_dispatcher.py",
                "jarvis_agent/mission_scheduler.py",
                "jarvis_agent/task_graph.py",
                "jarvis_agent/task_graph_store.py",
                "jarvis_agent/mission_orchestrator.py",
                "jarvis_agent/event_bus.py",
                "jarvis_agent/event_journal.py",
                "jarvis_agent/mission_context_store.py",
                "jarvis_agent/approval_manager.py",
                "jarvis_agent/context_broker.py",
                "jarvis_agent/context_injector.py",
                "jarvis_agent/knowledge_policy.py",
                "jarvis_agent/knowledge_broker.py",
                "jarvis_agent/agent_knowledge_adapter.py",
                "jarvis_agent/capability_registry.py",
                "jarvis_agent/agent_router.py",
                "jarvis_agent/agent_factory.py",
                "jarvis_agent/tool_gateway.py",
                "jarvis_agent/connector_registry.py",
                "jarvis_agent/connector_gateway.py",
                "jarvis_agent/execution_managers.py",
                "jarvis_agent/model_router.py",
                "jarvis_agent/llm_manager.py",
                "jarvis_agent/replay_sandbox.py",
                "jarvis_agent/replay_adapter.py",
                "jarvis_agent/local_rpc_security.py",
            ),
            level="suite",
            description=(
                "AIOS-inspired Kernel, multi-agent, permissions, context and "
                "replay foundations plus historical non-regression."
            ),
        )
    )
    registry.register(
        RegressionSpec(
            test_id="TEST-MEMORY-KNOWLEDGE",
            command=(
                "python -m unittest tests.test_agent_knowledge "
                "tests.test_agent_runtime -v"
            ),
            tags=("memory", "knowledge", "learning", "runtime"),
            watched_paths=(
                "jarvis_agent/agent_knowledge.py",
                "jarvis_agent/agent_runtime.py",
            ),
            level="suite",
            description=(
                "Persistent memory/knowledge and learning runtime coverage."
            ),
        )
    )

    return registry


DEFAULT_REGRESSION_REGISTRY = build_default_regression_registry()
