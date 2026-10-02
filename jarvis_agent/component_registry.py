from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable


@dataclass(frozen=True)
class ComponentSpec:
    component_id: str
    description: str
    watched_paths: tuple[str, ...]
    tags: tuple[str, ...]
    capabilities: tuple[str, ...] = ()
    default_test_ids: tuple[str, ...] = ()


class ComponentRegistry:
    """Maps incidents/changed paths to the smallest relevant Jarvis surface."""

    def __init__(self):
        self._components: dict[str, ComponentSpec] = {}

    def register(self, spec: ComponentSpec) -> None:
        self._components[spec.component_id] = spec

    def get(self, component_id: str) -> ComponentSpec | None:
        return self._components.get(str(component_id))

    def all(self) -> list[ComponentSpec]:
        return sorted(
            self._components.values(),
            key=lambda item: item.component_id,
        )

    @staticmethod
    def _normalized(path: str) -> str:
        return str(PurePosixPath(str(path).replace("\\", "/")))

    @classmethod
    def _matches(cls, path: str, watched: str) -> bool:
        value = cls._normalized(path)
        target = cls._normalized(watched)
        if target.endswith("/*"):
            return value.startswith(target[:-1])
        return value == target or value.startswith(target.rstrip("/") + "/")

    def for_paths(
        self,
        paths: Iterable[str],
    ) -> list[ComponentSpec]:
        normalized = [
            self._normalized(path)
            for path in paths
            if str(path).strip()
        ]
        result: list[ComponentSpec] = []
        for spec in self._components.values():
            if any(
                self._matches(path, watched)
                for path in normalized
                for watched in spec.watched_paths
            ):
                result.append(spec)
        return sorted(result, key=lambda item: item.component_id)

    def for_tags(
        self,
        tags: Iterable[str],
    ) -> list[ComponentSpec]:
        wanted = {str(tag) for tag in tags if str(tag)}
        return sorted(
            [
                spec
                for spec in self._components.values()
                if wanted & set(spec.tags)
            ],
            key=lambda item: item.component_id,
        )


def build_default_component_registry() -> ComponentRegistry:
    registry = ComponentRegistry()

    registry.register(
        ComponentSpec(
            component_id="windows_perception",
            description="Win32/UIA window and control perception/actions.",
            watched_paths=(
                "jarvis_agent/windows_perception.py",
                "jarvis_agent/native_tools.py",
            ),
            tags=("windows", "uia", "computer_use"),
            capabilities=(
                "computer.observe",
                "computer.interact",
            ),
            default_test_ids=(
                "TEST-WIN-BASELINE",
                "TEST-VISION-LAYER",
            ),
        )
    )
    registry.register(
        ComponentSpec(
            component_id="screen_vision",
            description="Local Ollama/Gemma visual perception and fallback actions.",
            watched_paths=("jarvis_agent/screen_vision.py",),
            tags=("vision", "computer_use"),
            capabilities=(
                "computer.observe",
                "computer.interact",
            ),
            default_test_ids=("TEST-VISION-LAYER",),
        )
    )
    registry.register(
        ComponentSpec(
            component_id="agent_runtime",
            description="LLM/tool loop, guards, confirmation and context behavior.",
            watched_paths=("jarvis_agent/agent_runtime.py",),
            tags=("runtime", "llm", "tools", "context"),
            default_test_ids=(
                "TEST-WIN-BASELINE",
                "TEST-MEMORY-KNOWLEDGE",
            ),
        )
    )
    registry.register(
        ComponentSpec(
            component_id="operational_knowledge",
            description="Skills, lessons, app profiles and proof runs.",
            watched_paths=(
                "jarvis_agent/agent_knowledge.py",
                "jarvis_agent/agent_knowledge_adapter.py",
                "jarvis_agent/knowledge_cli.py",
                "jarvis_agent/knowledge_policy.py",
                "jarvis_agent/knowledge_broker.py",
            ),
            tags=("memory", "knowledge", "learning"),
            default_test_ids=(
                "TEST-MEMORY-KNOWLEDGE",
                "TEST-KERNEL-FOUNDATIONS",
            ),
        )
    )
    registry.register(
        ComponentSpec(
            component_id="ms_football",
            description="MS Football bridge and protected mutations.",
            watched_paths=("jarvis_agent/ms_football_bridge.py",),
            tags=("ms_football", "domain", "approval"),
            capabilities=(
                "msf.read",
                "msf.prepare_mutation",
                "msf.commit_mutation",
            ),
        )
    )
    registry.register(
        ComponentSpec(
            component_id="voice_interaction",
            description="Jarvis launch, voice/UI worker and speech configuration.",
            watched_paths=(
                "run_jarvis.py",
                "jarvis_agent/assistant_v3.py",
                "jarvis_agent/config.py",
            ),
            tags=("voice", "stt", "tts", "wake", "ui"),
        )
    )
    registry.register(
        ComponentSpec(
            component_id="kernel_foundations",
            description="Passive AIOS-inspired Kernel/agent/scheduler contracts.",
            watched_paths=(
                "jarvis_agent/kernel_contracts.py",
                "jarvis_agent/kernel_service.py",
                "jarvis_agent/kernel_policy.py",
                "jarvis_agent/kernel_request_store.py",
                "jarvis_agent/kernel_dispatcher.py",
                "jarvis_agent/local_rpc_security.py",
                "jarvis_agent/mission_scheduler.py",
                "jarvis_agent/task_graph.py",
                "jarvis_agent/task_graph_store.py",
                "jarvis_agent/mission_orchestrator.py",
                "jarvis_agent/event_bus.py",
                "jarvis_agent/event_journal.py",
                "jarvis_agent/incident_bundle.py",
                "jarvis_agent/mission_context_store.py",
                "jarvis_agent/approval_manager.py",
                "jarvis_agent/context_broker.py",
                "jarvis_agent/context_injector.py",
                "jarvis_agent/model_router.py",
                "jarvis_agent/llm_manager.py",
                "jarvis_agent/agent_factory.py",
                "jarvis_agent/tool_gateway.py",
                "jarvis_agent/execution_managers.py",
            ),
            tags=("kernel", "scheduler", "multi_agent", "observability"),
            default_test_ids=("TEST-KERNEL-FOUNDATIONS",),
        )
    )

    return registry


DEFAULT_COMPONENT_REGISTRY = build_default_component_registry()
