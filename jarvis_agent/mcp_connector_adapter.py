from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .connector_gateway import ConnectorResult
from .connector_registry import ConnectorBackend


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
            raw = dict(
                self.transport.call_tool(
                    tool_name,
                    dict(arguments or {}),
                )
                or {}
            )
        except Exception as exc:
            return ConnectorResult(
                connector_id=self.connector_id,
                capability=capability,
                success=False,
                message="Le transport MCP a échoué.",
                error=str(exc)[:1200],
            )

        success = bool(raw.get("success", True))
        return ConnectorResult(
            connector_id=self.connector_id,
            capability=capability,
            success=success,
            message=str(
                raw.get("message")
                or (
                    "Action MCP exécutée."
                    if success
                    else "L'outil MCP a signalé un échec."
                )
            )[:1200],
            data=dict(raw.get("data") or {}),
            error=str(raw.get("error") or "")[:1200],
        )
