from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .connector_registry import (
    ConnectorBackend,
    ConnectorRegistry,
    DEFAULT_CONNECTOR_REGISTRY,
)
from .kernel_contracts import RiskLevel


@dataclass(frozen=True)
class ConnectorResult:
    connector_id: str
    capability: str
    success: bool
    message: str
    data: dict[str, Any] | None = None
    error: str = ""


class ConnectorAdapter(Protocol):
    connector_id: str
    backend: ConnectorBackend

    def execute(
        self,
        capability: str,
        arguments: dict[str, Any],
    ) -> ConnectorResult:
        ...


class ConnectorGateway:
    """Permission boundary between logical capabilities and real backends."""

    def __init__(
        self,
        registry: ConnectorRegistry | None = None,
    ):
        self.registry = registry or DEFAULT_CONNECTOR_REGISTRY
        self._adapters: dict[
            tuple[str, ConnectorBackend],
            ConnectorAdapter,
        ] = {}

    def register_adapter(self, adapter: ConnectorAdapter) -> None:
        key = (str(adapter.connector_id), adapter.backend)
        self._adapters[key] = adapter

    def available_backends(
        self,
        connector_id: str,
    ) -> list[ConnectorBackend]:
        return sorted(
            [
                backend
                for (current_id, backend) in self._adapters
                if current_id == connector_id
            ],
            key=lambda item: item.value,
        )

    def execute(
        self,
        *,
        connector_id: str,
        capability: str,
        arguments: dict[str, Any] | None = None,
        approved: bool = False,
    ) -> ConnectorResult:
        spec = self.registry.get(connector_id)
        if spec is None:
            return ConnectorResult(
                connector_id=connector_id,
                capability=capability,
                success=False,
                message="Connecteur inconnu.",
                error="connector_not_registered",
            )

        cap = next(
            (
                item
                for item in spec.capabilities
                if item.name == capability
            ),
            None,
        )
        if cap is None:
            return ConnectorResult(
                connector_id=connector_id,
                capability=capability,
                success=False,
                message="Capacité non déclarée pour ce connecteur.",
                error="capability_not_allowed",
            )

        if (
            cap.requires_confirmation
            or cap.risk
            in {RiskLevel.EXTERNAL_SIDE_EFFECT, RiskLevel.DESTRUCTIVE}
        ) and not approved:
            return ConnectorResult(
                connector_id=connector_id,
                capability=capability,
                success=False,
                message="Confirmation utilisateur requise.",
                error="approval_required",
            )

        available = self.available_backends(connector_id)
        backend = self.registry.choose_backend(
            connector_id,
            available,
        )
        if backend is None:
            return ConnectorResult(
                connector_id=connector_id,
                capability=capability,
                success=False,
                message="Aucun backend connecté pour cette capacité.",
                error="backend_unavailable",
            )

        adapter = self._adapters[(connector_id, backend)]
        try:
            result = adapter.execute(
                capability,
                dict(arguments or {}),
            )
        except Exception as exc:
            return ConnectorResult(
                connector_id=connector_id,
                capability=capability,
                success=False,
                message="Le backend du connecteur a échoué.",
                error=str(exc)[:800],
            )
        return result
