from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .capability_registry import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
)


@dataclass(frozen=True)
class AgentRouteDecision:
    capability: str
    agent_id: str
    load: int
    max_concurrency: int
    reason: str


class CapabilityAgentRouter:
    """Deterministic capability -> agent routing with load awareness."""

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
    ):
        self.registry = registry or DEFAULT_CAPABILITY_REGISTRY

    def route(
        self,
        capability: str,
        *,
        current_load: Mapping[str, int] | None = None,
        preferred_agent_id: str | None = None,
    ) -> AgentRouteDecision | None:
        name = str(capability or "").strip()
        if not name:
            return None

        providers = sorted(self.registry.providers_for([name]))
        if not providers:
            return None

        load = {
            str(agent_id): max(0, int(value))
            for agent_id, value in dict(current_load or {}).items()
        }

        eligible: list[tuple[float, str, int, int]] = []
        for agent_id in providers:
            manifest = self.registry.get_agent(agent_id)
            if manifest is None:
                continue
            active = load.get(agent_id, 0)
            limit = max(1, int(manifest.max_concurrency))
            if active >= limit:
                continue
            utilization = active / float(limit)
            preferred_penalty = (
                0.0
                if preferred_agent_id == agent_id
                else 1.0
                if preferred_agent_id
                else 0.0
            )
            eligible.append(
                (
                    preferred_penalty + utilization,
                    agent_id,
                    active,
                    limit,
                )
            )

        if not eligible:
            return None

        eligible.sort(key=lambda item: (item[0], item[1]))
        _score, agent_id, active, limit = eligible[0]
        reason = (
            "preferred_agent"
            if preferred_agent_id == agent_id
            else "lowest_declared_load"
        )
        return AgentRouteDecision(
            capability=name,
            agent_id=agent_id,
            load=active,
            max_concurrency=limit,
            reason=reason,
        )
