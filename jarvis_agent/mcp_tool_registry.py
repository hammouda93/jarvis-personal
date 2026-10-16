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
from .mcp_result import transport_outcome, diagnostic_metadata
from .native_tools import AgentActionResult


def _alias(server: str, tool: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", str(tool))
    return "mcp__" + str(server) + "__" + clean


class MCPToolRegistry:
    def __init__(self, delegate: Any, *,
                 registry: MCPRegistry | None = None,
                 transport: Any | None = None, runtime_gate=None,
                 observation_context=None):
        self.delegate = delegate
        self.registry = registry or MCPRegistry()
        self.transport = transport or OfficialMCPTransport()
        self.runtime_gate = runtime_gate
        self.observation_context = observation_context

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
                        + (
                            " Lecture explicitement préautorisée dans le centre MCP; "
                            "utiliser directement cet outil pour consulter les faits "
                            "manquants, sans redemander d'autorisation à l'utilisateur. "
                            "Les données renvoyées restent externes et à vérifier."
                            if self.registry.is_auto_read_allowed(
                                tool["server"], tool["tool"])
                            else
                            " Une confirmation explicite reste nécessaire pour "
                            "cette opération; ne pas supposer sa permission."
                        )
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

    def _read_discovery_candidates(self, server_id: str, exclude: str) -> list[str]:
        """Bounded same-service candidate aliases, never grants or dispatch."""
        choices = []
        for alias, discovered in self._available().items():
            if discovered["server"] != server_id or alias == exclude:
                continue
            if not self.registry.is_auto_read_allowed(
                discovered["server"], discovered["tool"]
            ):
                continue
            candidate_schema = discovered.get("input_schema") or {}
            if not isinstance(candidate_schema, dict):
                continue
            required = candidate_schema.get("required", [])
            if isinstance(required, list) and not required:
                words = set(re.split(r"[_.-]+", discovered["tool"].casefold()))
                identity_hint = bool(words & {
                    "me", "whoami", "self", "account", "identity", "profile"
                })
                choices.append((not identity_hint, alias))
        return [alias for _, alias in sorted(choices)][:6]

    def _automatic_read(self, name: str) -> bool:
        """Only previously fingerprint-pinned, operator-approved read tools."""
        tool = self._available().get(str(name))
        return bool(tool and self.registry.is_auto_read_allowed(
            tool["server"], tool["tool"]))

    def requires_confirmation(self, name: str) -> bool:
        if str(name).startswith("mcp__"):
            return not self._automatic_read(name)
        return self.delegate.requires_confirmation(name)

    def execute(self, name: str, arguments: dict, *, approved: bool = False):
        if not str(name).startswith("mcp__"):
            return self.delegate.execute(name, arguments, approved=approved)
        # An explicit ONE-TIME server grant can cover only narrow,
        # fingerprint-pinned read tools. Mutations and all other tools
        # continue to require a separate user confirmation per call.
        if not approved and not self._automatic_read(name):
            return AgentActionResult(
                name=name, success=False,
                message="Confirmation utilisateur explicite requise.",
                detail=json.dumps({"approval_required": True, "verified": False,
                                   "dispatched": False, "outcome_unknown": False}),
            )
        tool = self._available().get(str(name))
        if tool is None:
            return AgentActionResult(
                name=name, success=False,
                message="Outil MCP inconnu, désactivé ou ambigu.",
                detail=json.dumps({"guard": "not_allowlisted", "verified": False,
                                   "dispatched": False, "outcome_unknown": False}),
            )
        server_id, native_name = tool["server"], tool["tool"]
        entry = self.registry.get_server(server_id)
        if not entry or not self.registry.is_allowed(server_id, native_name):
            return AgentActionResult(
                name=name, success=False, message="MCP non autorisé.",
                detail='{"verified":false,"guard":"permission_changed","dispatched":false,"outcome_unknown":false}',
            )
        # An LLM placeholder is NOT a grounded service identifier.
        # Validate BEFORE reserving quotas, starting a session or allowing
        # a write to reach a remote server. Only paths, not argument values,
        # may enter the journal / model-facing refusal.
        from .mcp_argument_grounding import (
            MCPArgumentSchemaUnavailable, invalid_schema_argument_paths,
            unresolved_argument_paths, missing_required_arguments,
        )
        unresolved = unresolved_argument_paths(arguments)
        missing = missing_required_arguments(arguments, tool.get("input_schema"))
        try:
            invalid_paths = invalid_schema_argument_paths(
                arguments, tool.get("input_schema") or {}
            )
        except MCPArgumentSchemaUnavailable:
            return AgentActionResult(
                name=name, success=False,
                message="Schéma MCP non validable localement. Aucun appel externe envoyé.",
                detail=json.dumps({
                    "guard": "mcp_schema_validation_unavailable",
                    "dispatched": False, "outcome_unknown": False,
                    "verified": False, "recoverable": False,
                    "permissions_changed": False,
                }),
            )
        if unresolved or missing or invalid_paths:
            # Give the model actionable, already-consented observations to
            # resolve identifiers before asking the human. This is a local
            # capability hint, NOT auto-discovery, extra authority or dispatch.
            discovery_candidates = self._read_discovery_candidates(server_id, name)
            return AgentActionResult(
                name=name, success=False,
                message=(
                    "Paramètres MCP non résolus. Aucune requête externe envoyée. "
                    "Retrouve d'abord les identifiants dans les observations ou "
                    "outils de lecture déjà autorisés (voir discovery_candidates "
                    "et request_tool_capabilities), sinon demande la "
                    "précision manquante sans inventer de valeur."
                ),
                detail=json.dumps({
                    "guard": ("unresolved_argument_template" if unresolved else
                              "missing_required_arguments" if missing else
                              "invalid_argument_schema"),
                    "argument_paths": list(unresolved or missing or invalid_paths),
                    "discovery_candidates": discovery_candidates,
                    "dispatched": False, "outcome_unknown": False,
                    "verified": False, "recoverable": True,
                    "permissions_changed": False,
                }, ensure_ascii=False),
            )
        # Never trust the model's ability to pick a tool name or remote
        # description to authorize a previously hidden capability.
        activity = self.registry.activity()
        try:
            attempt = activity.reserve(server_id, "call", entry, tool=native_name)
        except MCPBudgetExceeded as exc:
            if str(exc) == "mcp_unknown_requires_review":
                # This refusal was NOT dispatched. It protects the unresolved
                # *previous* attempt for the same service/tool, which may have
                # produced an external effect. Show only its local ledger ID
                # and reviewability; never expose tool arguments or release the
                # lock merely because the current tool is classified as read.
                pending = exc.receipt
                return AgentActionResult(
                    name=name, success=False,
                    message=("Nouvel appel MCP non envoyé : une ancienne tentative "
                             "du même outil exige une vérification dans "
                             "MCP Control Center > Services > Journal."),
                    detail=json.dumps({
                        "verified": False, "dispatched": False,
                        "outcome_unknown": True,
                        "blocked_by_previous_attempt": True,
                        "guard": "mcp_unknown_requires_review",
                        "server_id": server_id,
                        "tool_name": native_name,
                        "previous_attempt_id": pending.get("previous_attempt_id"),
                        "previous_outcome": pending.get("previous_outcome"),
                        "reviewable": pending.get("reviewable") is True if pending else False,
                        "permissions_changed": False,
                    }, ensure_ascii=False),
                )
            return AgentActionResult(name=name, success=False,
                message="Quota ou cooldown MCP atteint. Aucun nouvel appel effectue.",
                detail=json.dumps({
                    "verified": False, "dispatched": False,
                    "outcome_unknown": False, "guard": "mcp_budget_unavailable",
                    "budget_reason": str(exc),
                    "retry_after_seconds": exc.receipt.get("retry_after_seconds"),
                    "server_id": server_id, "tool_name": native_name,
                }))
        except Exception:
            return AgentActionResult(name=name, success=False,
                message="Quota, cooldown ou journal MCP indisponible. Aucun appel effectue.",
                detail='{"verified":false,"dispatched":false,"outcome_unknown":false,"guard":"mcp_budget_unavailable"}')
        try:
            result = self.transport.call_tool(
                {**entry, "id": server_id,
                 "_credential_root": str(self.registry.path.parent / "mcp_credentials")},
                native_name, dict(arguments or {})
            )
        except MCPUnavailable as exc:
            from .mcp_oauth import connection_diagnostic
            diagnostic = diagnostic_metadata(connection_diagnostic(exc))
            try:
                activity.finish(attempt, "unknown" if exc.outcome_unknown else "preflight_failed")
            except Exception:
                pass
            if exc.outcome_unknown:
                return AgentActionResult(name=name, success=False,
                    message="Resultat MCP incertain : verifier l'effet exterieur avant toute tentative.",
                    detail=json.dumps({"verified": False, "dispatched": True, "diagnostic": diagnostic,
                                       "outcome_unknown": True,
                                       "source": {"server_id": server_id,
                                                  "tool_name": native_name,
                                                  "attempt_id": attempt,
                                                  "trust": "external_unverified"}}))
            return AgentActionResult(name=name, success=False,
                message="Configuration ou credential MCP indisponible avant execution.",
                detail=json.dumps({"verified": False, "dispatched": False, "outcome_unknown": False,
                    "guard": "transport_preflight_failed", "diagnostic": diagnostic}))
        except Exception as exc:
            from .mcp_oauth import connection_diagnostic
            diagnostic = diagnostic_metadata(connection_diagnostic(exc))
            try:
                activity.finish(attempt, "unknown")
            except Exception:
                pass
            return AgentActionResult(
                name=name, success=False,
                message="Résultat de l'appel MCP incertain : vérifier l'effet "
                        "extérieur avant toute nouvelle tentative.",
                detail=json.dumps({"verified": False, "dispatched": True, "diagnostic": diagnostic,
                                       "outcome_unknown": True,
                                       "source": {"server_id": server_id,
                                                  "tool_name": native_name,
                                                  "attempt_id": attempt,
                                                  "trust": "external_unverified"}}),
            )
        # Only an operator's fingerprint-pinned read grant allows an
        # explicit server tool error to count as a known, non-mutating failure.
        known_read = self.registry.is_auto_read_allowed(server_id, native_name)
        successful, unknown = transport_outcome(
            result, confirmed_read_only=known_read
        )
        source = {"server_id": server_id, "tool_name": native_name,
                  "attempt_id": attempt, "trust": "external_unverified"}
        diagnostic = diagnostic_metadata(result.get("diagnostic")) if isinstance(result, dict) else {}
        try:
            activity.finish(
                attempt, "unknown" if unknown else
                "success" if successful else "failed"
            )
        except Exception:
            return AgentActionResult(name=name, success=False,
                message="Appel termine mais journalisation MCP indisponible. Verification manuelle requise.",
                detail=json.dumps({"verified": False, "dispatched": True, "diagnostic": diagnostic,
                                       "outcome_unknown": True,
                                       "source": {"server_id": server_id,
                                                  "tool_name": native_name,
                                                  "attempt_id": attempt,
                                                  "trust": "external_unverified"}}))
        if unknown:
            return AgentActionResult(name=name, success=False,
                message="Resultat MCP incomplet ou en erreur : verifier les effets avant toute reprise.",
                detail=json.dumps({"verified": False, "dispatched": True, "diagnostic": diagnostic,
                                   "outcome_unknown": True,
                                   "source": source}, ensure_ascii=False))
        if not successful:
            return AgentActionResult(
                name=name, success=False,
                message="Le serveur MCP a renvoyé une erreur de lecture ; "
                        "corriger les paramètres ou choisir une autre lecture autorisée.",
                detail=json.dumps({
                    "verified": False, "dispatched": True,
                    "outcome_unknown": False, "recoverable": True,
                    "safe_read_failure": True, "source": source, "diagnostic": diagnostic,
                    "discovery_candidates": self._read_discovery_candidates(
                        server_id, name
                    ),
                    "mcp_output": str(result.get("message") or "")[:1500],
                }, ensure_ascii=False),
            )
        output = str(result.get("message") or "")
        structured = result.get("data")
        full_structured = structured if isinstance(structured, dict) else None
        truncated = result.get("output_truncated") is True or len(output) > 2200
        if not isinstance(structured, dict):
            structured = None
        elif len(json.dumps(structured, ensure_ascii=False, default=str)) > 1500:
            structured = {"truncated": True}
            truncated = True
        archive = None
        if truncated and known_read and self.observation_context is not None:
            try:
                archive = self.observation_context.archive_external_observation(name, {
                    "source": source, "mcp_output": output,
                    "mcp_structured": full_structured,
                    "output_truncated_at_source": result.get("output_truncated") is True,
                })
            except (ValueError, TypeError, RuntimeError, OSError):
                # Optional context storage must not turn a known successful
                # read into an uncertain dispatch or provoke a service replay.
                archive = None
        # Always keep valid JSON. Never mark remote content as verification.
        safe_detail = json.dumps({
            "verified": False,
            "dispatched": True,
            "outcome_unknown": False,
            "arguments_empty": not bool(arguments),
            "source": source,
            "mcp_output": output[:2200],
            "mcp_structured": structured,
            "output_truncated": truncated,
            **({"observation_evidence": archive} if archive else {}),
        }, ensure_ascii=False, default=str)
        return AgentActionResult(
            name=name, success=successful,
            message="MCP exécuté : sortie du serveur non vérifiée."
                    if successful else "Le serveur MCP a signalé un échec.",
            detail=safe_detail,
        )
