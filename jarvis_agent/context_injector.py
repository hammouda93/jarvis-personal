from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .capability_registry import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
)
from .context_broker import ContextBroker, ContextItem, ContextSelection
from .kernel_contracts import KnowledgeScope
from .knowledge_broker import KnowledgeBroker
from .knowledge_policy import KnowledgePrincipal
from .mission_context_store import MissionContextStore


@dataclass(frozen=True)
class AgentContextRequest:
    mission_id: str
    agent_id: str
    query: str
    user_id: str | None = None
    organization_id: str | None = None
    token_budget: int = 1200
    knowledge_limit: int = 8


@dataclass(frozen=True)
class AgentContextResult:
    mission_id: str
    agent_id: str
    selection: ContextSelection
    denied_scope_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "agent_id": self.agent_id,
            "estimated_tokens": self.selection.estimated_tokens,
            "items": [
                {
                    "item_id": item.item_id,
                    "source": item.source,
                    "scope": item.scope.value,
                    "content": item.content,
                    "relevance": item.relevance,
                    "priority": item.priority,
                    "required": item.required,
                }
                for item in self.selection.items
            ],
            "omitted_item_ids": list(
                self.selection.omitted_item_ids
            ),
            "denied_scope_ids": list(self.denied_scope_ids),
        }


class AgentContextInjector:
    """Cross-agent context injection with manifest and ownership isolation."""

    def __init__(
        self,
        *,
        mission_store: MissionContextStore,
        knowledge: KnowledgeBroker,
        registry: CapabilityRegistry | None = None,
        broker: ContextBroker | None = None,
    ):
        self.mission_store = mission_store
        self.knowledge = knowledge
        self.registry = registry or DEFAULT_CAPABILITY_REGISTRY
        self.broker = broker or ContextBroker()

    def build(
        self,
        request: AgentContextRequest,
    ) -> AgentContextResult:
        manifest = self.registry.get_agent(request.agent_id)
        if manifest is None:
            raise ValueError("unknown_agent")

        loaded = self.mission_store.load(request.mission_id)
        if loaded is None:
            raise KeyError("mission_context_not_found")
        mission, _version = loaded

        if request.user_id is not None:
            if (
                mission.user_id is not None
                and mission.user_id != request.user_id
            ):
                raise PermissionError("mission_user_mismatch")
        principal = KnowledgePrincipal(
            user_id=request.user_id or mission.user_id,
            agent_id=request.agent_id,
            organization_id=request.organization_id,
        )
        records = self.knowledge.search(
            request.query,
            principal=principal,
            limit=max(1, int(request.knowledge_limit)),
        )

        allowed_scopes = set(manifest.memory_scopes)
        items: list[ContextItem] = [
            self.broker.mission_item(mission)
        ]
        denied: list[str] = []

        for record in records:
            if record.identity.scope not in allowed_scopes:
                denied.append(record.knowledge_id)
                continue
            items.append(
                ContextItem(
                    item_id=f"knowledge:{record.knowledge_id}",
                    source="scoped_knowledge",
                    content=record.content,
                    scope=record.identity.scope,
                    relevance=record.relevance,
                    priority=50,
                    required=False,
                )
            )

        selection = self.broker.select(
            items,
            token_budget=max(64, int(request.token_budget)),
        )
        return AgentContextResult(
            mission_id=request.mission_id,
            agent_id=request.agent_id,
            selection=selection,
            denied_scope_ids=tuple(sorted(denied)),
        )
