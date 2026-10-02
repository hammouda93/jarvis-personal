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


@dataclass(frozen=True)
class AgentRoutingContext:
    """Passive context used to predict the best specialized agent.

    The live runtime remains authoritative. This contract is intentionally
    data-only so Shadow routing can be inspected, persisted and tested without
    executing a tool or submitting a KernelRequest.
    """

    user_goal: str
    current_tool: str
    previous_tool: str = ""
    previous_agent: str = ""
    current_application: str = ""
    observed_window: str = ""
    mission_history: tuple[str, ...] = ()
    domain: str = ""
    available_agents: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "user_goal": self.user_goal,
            "current_tool": self.current_tool,
            "previous_tool": self.previous_tool,
            "previous_agent": self.previous_agent,
            "current_application": self.current_application,
            "observed_window": self.observed_window,
            "mission_history": list(self.mission_history),
            "domain": self.domain,
            "available_agents": list(self.available_agents),
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
        }


@dataclass(frozen=True)
class ContextualAgentRouteDecision:
    tool_name: str
    agent_id: str
    candidate_agents: tuple[str, ...]
    reason: str
    needs_review: bool = False


class CapabilityAgentRouter:
    """Deterministic capability routing plus passive contextual tool routing."""

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

    def route_contextual(
        self,
        context: AgentRoutingContext,
        *,
        candidate_agents: tuple[str, ...] | None = None,
    ) -> ContextualAgentRouteDecision:
        """Predict an agent for one already-observed live tool call.

        Resolution order is intentionally conservative:
        explicit domain -> application/window context -> mission continuity ->
        exclusive tool ownership -> unresolved ambiguity.

        This method is pure routing logic. It never dispatches or executes.
        """
        tool_name = str(context.current_tool or "").strip()
        supplied = tuple(
            dict.fromkeys(
                str(agent_id).strip()
                for agent_id in tuple(candidate_agents or ())
                if str(agent_id).strip()
            )
        )
        if supplied:
            candidates = supplied
        else:
            candidates = tuple(
                manifest.agent_id
                for manifest in self.registry.agents()
                if tool_name in set(manifest.allowed_tools)
            )

        if context.available_agents:
            available = {
                str(agent_id).strip()
                for agent_id in context.available_agents
                if str(agent_id).strip()
            }
            candidates = tuple(
                agent_id
                for agent_id in candidates
                if agent_id in available or agent_id == "interaction"
            )

        domain_text = " ".join(
            (
                str(context.domain or ""),
                str(context.user_goal or ""),
            )
        ).lower()
        if (
            tool_name.startswith("msf_")
            or "ms_football" in domain_text
            or "ms football" in domain_text
        ):
            if "ms_football" in candidates:
                return ContextualAgentRouteDecision(
                    tool_name=tool_name,
                    agent_id="ms_football",
                    candidate_agents=candidates,
                    reason="explicit_domain",
                )

        application_text = " ".join(
            (
                str(context.current_application or ""),
                str(context.observed_window or ""),
                str(context.user_goal or ""),
            )
        ).lower()
        browser_terms = (
            "chrome",
            "youtube",
            "firefox",
            "edge",
            "browser",
            "navigateur",
            "onglet",
            " tab ",
            "site web",
            "page web",
            "http://",
            "https://",
            " url ",
        )
        windows_terms = (
            "notepad",
            "bloc-notes",
            "bloc note",
            "bloc-notes",
            "cursor",
            "vscode",
            "vs code",
            "explorer",
            "explorateur",
            "installer",
            "installation",
            "fenêtre windows",
            "fenetre windows",
        )
        if (
            "browser" in candidates
            and any(term in application_text for term in browser_terms)
        ):
            return ContextualAgentRouteDecision(
                tool_name=tool_name,
                agent_id="browser",
                candidate_agents=candidates,
                reason="application_context",
            )
        if (
            "windows" in candidates
            and any(term in application_text for term in windows_terms)
        ):
            return ContextualAgentRouteDecision(
                tool_name=tool_name,
                agent_id="windows",
                candidate_agents=candidates,
                reason="application_context",
            )

        previous_agent = str(context.previous_agent or "").strip()
        if previous_agent and previous_agent in candidates:
            return ContextualAgentRouteDecision(
                tool_name=tool_name,
                agent_id=previous_agent,
                candidate_agents=candidates,
                reason="mission_continuity",
            )

        if len(candidates) == 1:
            return ContextualAgentRouteDecision(
                tool_name=tool_name,
                agent_id=candidates[0],
                candidate_agents=candidates,
                reason="exclusive_tool",
            )

        if not candidates:
            return ContextualAgentRouteDecision(
                tool_name=tool_name,
                agent_id="interaction",
                candidate_agents=(),
                reason="unmapped_tool",
                needs_review=True,
            )

        return ContextualAgentRouteDecision(
            tool_name=tool_name,
            agent_id="interaction",
            candidate_agents=candidates,
            reason="unresolved_ambiguity",
            needs_review=True,
        )
