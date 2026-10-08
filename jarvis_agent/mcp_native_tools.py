"""Provider-agnostic MCP tool bridge for the original Jarvis agent loop.

Disabled unless JARVIS_MCP_NATIVE_ENABLED=1 AND a server has an explicit
tool allowlist and independent auth. All remote tool executions ask approval.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .mcp_hub import MCP_HUB, MCPHub, MCPHubError
from .native_tools import AgentActionResult


def _enabled() -> bool:
    return os.getenv("JARVIS_MCP_NATIVE_ENABLED", "0").strip().lower() in (
        "1", "true", "on", "yes",
    )


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name, "description": description,
            "parameters": {
                "type": "object", "properties": properties,
                "required": required, "additionalProperties": False,
            },
        },
    }


_NATIVE_MCP_TOOLS = [
    _schema("mcp_list_servers",
            "Liste les serveurs MCP activés et autorisés par l'utilisateur. "
            "Ne connecte aucun serveur et ne dévoile aucun secret.",
            {}, []),
    _schema("mcp_list_tools",
            "Découvre les outils MCP explicitement autorisés pour un serveur "
            "activé. Observation seulement, pas d'appel à une action externe.",
            {"server_id": {"type": "string"}}, ["server_id"]),
    _schema("mcp_call_tool",
            "Appelle un outil d'un serveur MCP activé et de sa liste d'outils "
            "autorisés, avec confirmation explicite préalable de l'utilisateur. "
            "Jamais de replay si le résultat distant est incertain. "
            "La réponse d'outil ne prouve pas la mission.",
            {
                "server_id": {"type": "string"},
                "tool_name": {"type": "string"},
                "arguments": {"type": "object", "additionalProperties": True},
            },
            ["server_id", "tool_name", "arguments"]),
]


class MCPNativeToolBridge:
    """Delegates all previous tools untouched; adds only opt-in MCP verbs."""

    def __init__(self, delegate: Any, hub: MCPHub | None = None):
        self.delegate = delegate
        self.hub = hub or MCP_HUB

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def ollama_tools(self) -> list[dict]:
        base = list(self.delegate.ollama_tools())
        if not _enabled():
            return base
        if not any(x["enabled"] and x["auth_ready"] and x["approved_tool_count"]
                   for x in self.hub.status()):
            return base
        return base + list(_NATIVE_MCP_TOOLS)

    def requires_confirmation(self, name: str) -> bool:
        if name == "mcp_call_tool":
            return True
        return bool(self.delegate.requires_confirmation(name))

    def execute(
        self, name: str, arguments: dict[str, Any],
        *, approved: bool = False,
    ) -> AgentActionResult:
        if name not in {"mcp_list_servers", "mcp_list_tools", "mcp_call_tool"}:
            return self.delegate.execute(name, arguments, approved=approved)
        if not _enabled():
            return AgentActionResult(name, False, "MCP natif désactivé.",
                                     "mcp_not_enabled")
        if not isinstance(arguments, dict):
            return AgentActionResult(name, False, "Arguments MCP invalides.",
                                     "invalid_arguments")
        try:
            if name == "mcp_list_servers":
                response = {
                    "servers": [
                        {"id": x["id"], "allowed_tools": x["approved_tools"]}
                        for x in self.hub.status()
                        if x["enabled"] and x["auth_ready"] and x["approved_tool_count"]
                    ]
                }
            elif name == "mcp_list_tools":
                response = self.hub.list_tools(str(arguments.get("server_id") or ""))
            else:
                response = self.hub.call_tool(
                    str(arguments.get("server_id") or ""),
                    str(arguments.get("tool_name") or ""),
                    arguments.get("arguments") or {},
                    approved=bool(approved),
                )
            successful = response.get("success") is not False
            return AgentActionResult(
                name, successful,
                "Opération MCP terminée; objectif global non vérifié."
                if successful else "Le serveur MCP a signalé un échec.",
                json.dumps(response, ensure_ascii=False)[:4100],
            )
        except MCPHubError as exc:
            return AgentActionResult(name, False,
                                     "MCP non exécuté ou résultat à vérifier.",
                                     str(exc)[:80])
        except Exception:
            return AgentActionResult(name, False,
                                     "Erreur MCP; résultat distant incertain.",
                                     "mcp_outcome_unknown")
