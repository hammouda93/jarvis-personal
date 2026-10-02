from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .capability_registry import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
)
from .kernel_contracts import KernelRequest, RiskLevel


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str
    requires_approval: bool = False
    risk: RiskLevel = RiskLevel.READ


class KernelPolicy:
    """Central capability/tool permission checks for future kernel requests."""

    def __init__(
        self,
        registry: CapabilityRegistry | None = None,
    ):
        self.registry = registry or DEFAULT_CAPABILITY_REGISTRY

    def authorize(self, request: KernelRequest) -> AuthorizationDecision:
        manifest = self.registry.get_agent(request.agent_id)
        if manifest is None:
            return AuthorizationDecision(
                False,
                "unknown_agent",
            )

        registered = self.registry.get_capability(request.capability)
        if registered is None:
            return AuthorizationDecision(
                False,
                "unknown_capability",
            )
        if registered.provider_agent_id != request.agent_id:
            return AuthorizationDecision(
                False,
                "capability_not_owned_by_agent",
            )

        spec = registered.spec
        payload: dict[str, Any] = dict(request.payload or {})

        tool_name = str(payload.get("tool_name") or "").strip()
        if tool_name and tool_name not in set(manifest.allowed_tools):
            return AuthorizationDecision(
                False,
                "tool_not_allowed_for_agent",
                risk=spec.risk,
            )

        connector_id = str(payload.get("connector_id") or "").strip()
        if (
            connector_id
            and connector_id not in set(manifest.allowed_connectors)
        ):
            return AuthorizationDecision(
                False,
                "connector_not_allowed_for_agent",
                risk=spec.risk,
            )

        requires_approval = bool(
            request.requires_approval
            or spec.requires_confirmation
            or spec.risk
            in {RiskLevel.EXTERNAL_SIDE_EFFECT, RiskLevel.DESTRUCTIVE}
        )
        return AuthorizationDecision(
            True,
            "allowed",
            requires_approval=requires_approval,
            risk=spec.risk,
        )
