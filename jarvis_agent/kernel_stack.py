from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_factory import AgentFactory
from .agent_knowledge import AgentKnowledgeStore
from .agent_knowledge_adapter import LegacyAgentKnowledgeBackend
from .agent_router import CapabilityAgentRouter
from .approval_manager import HumanApprovalManager
from .capability_registry import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
)
from .connector_gateway import ConnectorGateway
from .context_broker import ContextBroker
from .context_injector import AgentContextInjector
from .correction_store import CorrectionCandidateStore
from .event_bus import MissionEventBus
from .event_journal import StructuredEventJournal
from .execution_managers import (
    ConnectorExecutionManager,
    ExecutionManagerRegistry,
    MemoryExecutionManager,
    ToolExecutionManager,
)
from .kernel_dispatcher import KernelDispatcher
from .kernel_request_store import KernelRequestStore
from .kernel_service import JarvisKernel
from .knowledge_broker import KnowledgeBroker
from .mission_context_store import MissionContextStore
from .mission_orchestrator import MissionOrchestrator
from .model_catalog import CurrentModelCatalog
from .model_router import LightweightModelRouter
from .model_telemetry import ModelTelemetryStore
from .task_graph_store import TaskGraphStore
from .tool_gateway import ScopedToolGateway
from .config import Settings


@dataclass
class PassiveKernelStack:
    """Composition root for the future Kernel, never auto-installed live."""

    registry: CapabilityRegistry
    event_bus: MissionEventBus
    journal: StructuredEventJournal
    approvals: HumanApprovalManager
    request_store: KernelRequestStore
    mission_store: MissionContextStore
    graph_store: TaskGraphStore
    correction_store: CorrectionCandidateStore
    operational_knowledge: AgentKnowledgeStore
    knowledge: KnowledgeBroker
    context_broker: ContextBroker
    context_injector: AgentContextInjector
    tool_gateway: ScopedToolGateway
    connector_gateway: ConnectorGateway
    managers: ExecutionManagerRegistry
    kernel: JarvisKernel
    dispatcher: KernelDispatcher
    orchestrator: MissionOrchestrator
    agent_router: CapabilityAgentRouter
    agent_factory: AgentFactory
    telemetry: ModelTelemetryStore
    model_router: LightweightModelRouter


def build_passive_kernel_stack(
    *,
    base_dir: str | Path,
    owner_user_id: str,
    settings: Settings,
    registry: CapabilityRegistry | None = None,
    operational_knowledge: AgentKnowledgeStore | None = None,
) -> PassiveKernelStack:
    """Build an isolated, non-live Kernel stack under one local directory.

    The caller must explicitly pass this stack to future code. Merely importing
    this module cannot change the current Jarvis runtime.
    """
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    active_registry = registry or DEFAULT_CAPABILITY_REGISTRY

    journal = StructuredEventJournal(root / "mission_events.sqlite3")
    approvals = HumanApprovalManager(root / "approvals.sqlite3")
    request_store = KernelRequestStore(root / "kernel_requests.sqlite3")
    mission_store = MissionContextStore(root / "mission_context.sqlite3")
    graph_store = TaskGraphStore(root / "task_graphs.sqlite3")
    correction_store = CorrectionCandidateStore(
        root / "correction_candidates.sqlite3"
    )
    telemetry = ModelTelemetryStore(root / "model_telemetry.sqlite3")

    knowledge_store = operational_knowledge or AgentKnowledgeStore(
        root / "agent_knowledge.sqlite3"
    )
    knowledge_backend = LegacyAgentKnowledgeBackend(
        knowledge_store,
        owner_user_id=owner_user_id,
    )
    knowledge = KnowledgeBroker(knowledge_backend)
    context_broker = ContextBroker()
    context_injector = AgentContextInjector(
        mission_store=mission_store,
        knowledge=knowledge,
        registry=active_registry,
        broker=context_broker,
    )

    tool_gateway = ScopedToolGateway(registry=active_registry)
    connector_gateway = ConnectorGateway()

    managers = ExecutionManagerRegistry()
    managers.register(ToolExecutionManager(tool_gateway))
    managers.register(ConnectorExecutionManager(connector_gateway))
    managers.register(MemoryExecutionManager(knowledge))

    event_bus = MissionEventBus()
    kernel = JarvisKernel(
        approvals=approvals,
        event_bus=event_bus,
        journal=journal,
        request_store=request_store,
    )
    dispatcher = KernelDispatcher(
        kernel=kernel,
        managers=managers,
    )
    orchestrator = MissionOrchestrator(
        kernel=kernel,
        context_store=mission_store,
        graph_store=graph_store,
    )

    catalog = CurrentModelCatalog(settings)
    model_router = LightweightModelRouter(
        catalog.configured_candidates(),
        telemetry=telemetry,
    )

    return PassiveKernelStack(
        registry=active_registry,
        event_bus=event_bus,
        journal=journal,
        approvals=approvals,
        request_store=request_store,
        mission_store=mission_store,
        graph_store=graph_store,
        correction_store=correction_store,
        operational_knowledge=knowledge_store,
        knowledge=knowledge,
        context_broker=context_broker,
        context_injector=context_injector,
        tool_gateway=tool_gateway,
        connector_gateway=connector_gateway,
        managers=managers,
        kernel=kernel,
        dispatcher=dispatcher,
        orchestrator=orchestrator,
        agent_router=CapabilityAgentRouter(active_registry),
        agent_factory=AgentFactory(active_registry),
        telemetry=telemetry,
        model_router=model_router,
    )
