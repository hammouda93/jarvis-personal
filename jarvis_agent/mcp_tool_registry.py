"""Model-facing, user-approved MCP tools on the SINGLE Jarvis tool registry.

All MCP tools require explicit confirmation, even ones described as read only
by an untrusted external MCP server. No capability is exposed before the
user enables that server AND that particular discovered tool.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .mcp_server_registry import MCPRegistry
from .mcp_sdk_transport import MCPUnavailable, OfficialMCPTransport
from .mcp_activity import MCPBudgetExceeded
from .mcp_result import transport_outcome
from .native_tools import AgentActionResult


def _alias(server: str, tool: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(tool))
    return "mcp__" + str(server) + "__" + clean


class MCPToolRegistry:
    def __init__(self, delegate: Any, *,
                 registry: MCPRegistry | None = None,
                 transport: Any | None = None, runtime_gate=None):
        self.delegate = delegate
        self.registry = registry or MCPRegistry()
        self.transport = transport or OfficialMCPTransport()
        self.runtime_gate = runtime_gate

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def _available(self) -> dict[str, dict]:
        found: dict[str, dict] = {}
        collided = set()
        try:
            if self.runtime_gate is not None and not self.runtime_gate():
                return {}
            allowed = self.registry.exposed_tools()
        except (OSError, ValueError, TypeError, KeyError):
            # A damaged/unavailable optional MCP file must never take down
            # the original Windows/browser assistant.
            return {}
        for tool in allowed:
            alias = _alias(tool["server"], tool["tool"])
            if alias in found:
                collided.add(alias)
            else:
                found[alias] = tool
        for alias in collided:
            found.pop(alias, None)  # ambiguous alias never reaches the model
        return found

    def ollama_tools(self) -> list[dict]:
        original = list(self.delegate.ollama_tools())
        occupied = {
            item.get("function", {}).get("name")
            for item in original if isinstance(item, dict)
        }
        for alias, tool in self._available().items():
            if alias in occupied:
                continue
            schema = tool.get("input_schema") or {"type": "object"}
            if not isinstance(schema, dict) or schema.get("type", "object") != "object":
                continue
            original.append({
                "type": "function",
                "function": {
                    "name": alias,
                    "description": (
                        "MCP externe : " + str(tool["server"]) + " / "
                        + str(tool["tool"]) + ". "
                        + str(tool.get("description") or "")[:300]
                        + " Confirmation utilisateur explicite obligatoire "
                        "pour chaque exécution; vérifier indépendamment le résultat."
                    )[:650],
                    "parameters": schema,
                }
            })
        return original

    def openai_tools(self) -> list[dict]:
        return [
            {"type": "function", "name": item["function"]["name"],
             "description": item["function"]["description"],
             "parameters": item["function"]["parameters"],
             "strict": False}
            for item in self.ollama_tools()
        ]

    def requires_confirmation(self, name: str) -> bool:
        if str(name).startswith("mcp__"):
            return True
        return self.delegate.requires_confirmation(name)

    def execute(self, name: str, arguments: dict, *, approved: bool = False):
        if not str(name).startswith("mcp__"):
            return self.delegate.execute(name, arguments, approved=approved)
        if not approved:
            return AgentActionResult(
                name=name, success=False,
                message="Confirmation utilisateur explicite requise.",
                detail=json.dumps({"approval_required": True, "verified": False}),
            )
        tool = self._available().get(str(name))
        if tool is None:
            return AgentActionResult(
                name=name, success=False,
                message="Outil MCP inconnu, désactivé ou ambigu.",
                detail=json.dumps({"guard": "not_allowlisted", "verified": False}),
            )
        server_id, native_name = tool["server"], tool["tool"]
        entry = self.registry.get_server(server_id)
        if not entry or not self.registry.is_allowed(server_id, native_name):
            return AgentActionResult(
                name=name, success=False, message="MCP non autorisé.",
                detail='{"verified":false,"guard":"permission_changed"}',
            )
        # Never trust the model's ability to pick a tool name or remote
        # description to authorize a previously hidden capability.
        activity = self.registry.activity()
        try:
            attempt = activity.reserve(server_id, "call", entry, tool=native_name)
        except MCPBudgetExceeded as exc:
            if str(exc) == "mcp_unknown_requires_review":
                return AgentActionResult(name=name, success=False,
                    message="Appel MCP precedent incertain : revue explicite requise dans le centre MCP.",
                    detail='{"verified":false,"outcome_unknown":true,"guard":"mcp_unknown_requires_review"}')
            return AgentActionResult(name=name, success=False,
                message="Quota ou cooldown MCP atteint. Aucun nouvel appel effectue.",
                detail='{"verified":false,"outcome_unknown":false,"guard":"mcp_budget_unavailable"}')
        except Exception:
            return AgentActionResult(name=name, success=False,
                message="Quota, cooldown ou journal MCP indisponible. Aucun appel effectue.",
                detail='{"verified":false,"outcome_unknown":false,"guard":"mcp_budget_unavailable"}')
        try:
            result = self.transport.call_tool(
                {**entry, "id": server_id,
                 "_credential_root": str(self.registry.path.parent / "mcp_credentials")},
                native_name, dict(arguments or {})
            )
        except MCPUnavailable as exc:
            try:
                activity.finish(attempt, "unknown" if exc.outcome_unknown else "preflight_failed")
            except Exception:
                pass
            if exc.outcome_unknown:
                return AgentActionResult(name=name, success=False,
                    message="Resultat MCP incertain : verifier l'effet exterieur avant toute tentative.",
                    detail='{"verified":false,"outcome_unknown":true}')
            return AgentActionResult(name=name, success=False,
                message="Configuration ou credential MCP indisponible avant execution.",
                detail='{"verified":false,"outcome_unknown":false,"guard":"transport_preflight_failed"}')
        except Exception:
            try:
                activity.finish(attempt, "unknown")
            except Exception:
                pass
            return AgentActionResult(
                name=name, success=False,
                message="Résultat de l'appel MCP incertain : vérifier l'effet "
                        "extérieur avant toute nouvelle tentative.",
                detail='{"verified":false,"outcome_unknown":true}',
            )
        successful, unknown = transport_outcome(result)
        source = {"server_id": server_id, "tool_name": native_name,
                  "attempt_id": attempt, "trust": "external_unverified"}
        try:
            activity.finish(attempt, "unknown" if unknown else "success")
        except Exception:
            return AgentActionResult(name=name, success=False,
                message="Appel termine mais journalisation MCP indisponible. Verification manuelle requise.",
                detail='{"verified":false,"outcome_unknown":true}')
        if unknown:
            return AgentActionResult(name=name, success=False,
                message="Resultat MCP incomplet ou en erreur : verifier les effets avant toute reprise.",
                detail=json.dumps({"verified": False, "outcome_unknown": True,
                                   "source": source}, ensure_ascii=False))
        structured = result.get("data")
        if not isinstance(structured, dict):
            structured = None
        elif len(json.dumps(structured, ensure_ascii=False, default=str)) > 1500:
            structured = {"truncated": True}
        # Always keep valid JSON. Never mark remote content as verification.
        safe_detail = json.dumps({
            "verified": False,
            "outcome_unknown": False,
            "source": source,
            "mcp_output": str(result.get("message") or "")[:2200],
            "mcp_structured": structured,
        }, ensure_ascii=False, default=str)
        return AgentActionResult(
            name=name, success=successful,
            message="MCP exécuté : sortie du serveur non vérifiée."
                    if successful else "Le serveur MCP a signalé un échec.",
            detail=safe_detail,
        )
