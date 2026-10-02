from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .capability_registry import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
)


@dataclass(frozen=True)
class ToolGatewayResult:
    tool_name: str
    success: bool
    message: str
    detail: dict[str, Any] | None = None
    error: str = ""


class ScopedToolGateway:
    """Hard permission boundary for agent -> tool execution.

    The model cannot bypass this gateway by merely mentioning a tool in a
    prompt. The agent manifest must explicitly allow the tool.
    """

    def __init__(
        self,
        *,
        registry: CapabilityRegistry | None = None,
    ):
        self.registry = registry or DEFAULT_CAPABILITY_REGISTRY
        self._executors: dict[
            str,
            Callable[[dict[str, Any]], ToolGatewayResult],
        ] = {}

    def register_tool(
        self,
        tool_name: str,
        executor: Callable[[dict[str, Any]], ToolGatewayResult],
    ) -> None:
        if not callable(executor):
            raise TypeError("tool_executor_not_callable")
        self._executors[str(tool_name)] = executor

    def execute(
        self,
        *,
        agent_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolGatewayResult:
        if not self.registry.tool_allowed(agent_id, tool_name):
            return ToolGatewayResult(
                tool_name=tool_name,
                success=False,
                message="Outil non autorisé pour cet agent.",
                error="tool_not_allowed_for_agent",
            )

        executor = self._executors.get(str(tool_name))
        if executor is None:
            return ToolGatewayResult(
                tool_name=tool_name,
                success=False,
                message="Aucun exécuteur n'est enregistré pour cet outil.",
                error="tool_executor_unavailable",
            )

        try:
            return executor(dict(arguments or {}))
        except Exception as exc:
            return ToolGatewayResult(
                tool_name=tool_name,
                success=False,
                message="L'exécution de l'outil a échoué.",
                error=str(exc)[:1200],
            )
