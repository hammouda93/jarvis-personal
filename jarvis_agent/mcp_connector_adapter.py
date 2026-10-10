from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .connector_gateway import ConnectorResult
from .connector_registry import ConnectorBackend
from .mcp_result import transport_outcome


class MCPTransport(Protocol):
    transport_id: str

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        ...


@dataclass
class MCPConnectorAdapter:
    """Map logical connector capabilities to MCP tools.

    This adapter does not discover arbitrary tools automatically. The mapping
    must be explicitly supplied by trusted Jarvis configuration.
    """

    connector_id: str
    transport: MCPTransport
    capability_to_tool: dict[str, str]
    backend: ConnectorBackend = ConnectorBackend.MCP

    def execute(
        self,
        capability: str,
        arguments: dict[str, Any],
    ) -> ConnectorResult:
        tool_name = self.capability_to_tool.get(str(capability))
        if not tool_name:
            return ConnectorResult(
                connector_id=self.connector_id,
                capability=capability,
                success=False,
                message="Aucun outil MCP autorisé pour cette capacité.",
                error="mcp_tool_mapping_missing",
            )

        try:
            raw = self.transport.call_tool(
                tool_name,
                dict(arguments or {}),
            )
        except Exception:
            return ConnectorResult(
                connector_id=self.connector_id,
                capability=capability,
                success=False,
                message="Le transport MCP a échoué.",
                error="mcp_transport_outcome_unknown",
                outcome_unknown=True,
            )

        success, unknown = transport_outcome(raw)
        if unknown:
            return ConnectorResult(self.connector_id, capability, False,
                "Resultat MCP incertain ; verifier avant toute nouvelle tentative.",
                error="mcp_execution_outcome_unknown", outcome_unknown=True)
        return ConnectorResult(
            connector_id=self.connector_id,
            capability=capability,
            success=success,
            message=str(
                raw.get("message")
                or (
                    "Retour MCP recu, non verifie independamment."
                    if success
                    else "L'outil MCP a signalé un échec."
                )
            )[:1200],
            data=raw.get("data") if isinstance(raw.get("data"), dict) else None,
            error=str(raw.get("error") or "")[:1200],
        )
