"""Gmail onboarding over existing MCP/OAuth. No mail API or model dispatcher.

Provider login, metadata discovery, local binding and operator-reviewed tool
access are distinct. None of these operations reads or sends an email.
"""
from __future__ import annotations

import hashlib
import json
import re

from .mcp_read_policy import is_conservative_read_tool
from .mcp_server_registry import connection_fingerprint
from .personal_workspace import PersonalWorkspaceStore


PIPEDREAM_ENDPOINT = "https://mcp.pipedream.net/v2"
PIPEDREAM_DOC = "https://pipedream.com/docs/connect/mcp/users"
PIPEDREAM_SCOPE = "openid profile email offline_access mcp"
GOOGLE_GMAIL_ENDPOINT = "https://gmailmcp.googleapis.com/mcp/v1"
GMAIL_OPERATIONS = frozenset({"gmail_connect", "gmail_grant"})
GROUPS = {"read": "Lire et rechercher", "draft": "Brouillons", "send": "Envoyer et répondre",
          "organize": "Classer et modifier", "delete": "Supprimer", "other": "Autres outils Gmail"}
_VERBS = frozenset({"get", "list", "search", "read", "fetch", "find", "lookup", "inspect", "view", "download",
    "send", "reply", "forward", "create", "draft", "update", "modify", "archive", "trash", "untrash",
    "delete", "remove", "add", "mark", "label", "move", "batch", "insert"})
_ERRORS = frozenset({"gmail_invalid_request", "gmail_binding_changed", "gmail_server_conflict", "gmail_server_not_found",
    "gmail_connection_changed", "gmail_inventory_changed", "gmail_no_tools", "gmail_test_connection_first",
    "gmail_oauth_cancelled", "gmail_invalid_selection", "mcp_tool_review_changed", "mcp_read_autopilot_needs_tested_connection",
    "mcp_failure_cooldown", "mcp_hourly_quota_exhausted", "mcp_unknown_requires_review"})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def managed_server_id(registry, workspace=None):
    workspace = workspace or PersonalWorkspaceStore()
    bindings = workspace.bindings()
    bound = (bindings.get("gmail") or {}).get("server")
    entry = registry.get_server(bound) if bound else None
    if entry and entry.get("kind") == "http" and entry.get("url") == PIPEDREAM_ENDPOINT:
        return bound
    for name in ["gmail", "gmail_pd", *["gmail_pd" + str(i) for i in range(2, 31)]]:
        entry = registry.get_server(name)
        used_elsewhere = any(key != "gmail" and row["server"] == name for key, row in bindings.items())
        reusable = (entry and entry.get("kind") == "http" and entry.get("url") == PIPEDREAM_ENDPOINT and
                    not entry.get("enabled") and not any(t.get("allowed") for t in (entry.get("tools") or {}).values()) and not used_elsewhere)
        if entry is None or reusable:
            return name
    raise ValueError("gmail_server_conflict")


def tool_group(name, entry):
    """Select named Gmail tools; generic cross-service runners stay unselected."""
    words = re.split(r"[._-]+", str(name).lower())
    exclusive = entry.get("kind") == "http" and entry.get("url") == GOOGLE_GMAIL_ENDPOINT
    if not words or (not exclusive and "gmail" not in words):
        return None
    operation = words[1] if words[0] == "gmail" and len(words) > 1 else words[0]
    if operation not in _VERBS:
        return None
    if any(w in {"execute", "run", "bash", "python", "workbench", "credentials", "token", "secret", "password"} for w in words):
        return None
    if operation in {"send", "reply", "forward"}:
        return "send"
    if "draft" in words or "drafts" in words:
        return "read" if operation in {"get", "list", "search", "read", "fetch", "find"} else "draft"
    if operation in {"delete", "remove", "trash"}:
        return "delete"
    if operation in {"get", "list", "search", "read", "fetch", "find", "lookup", "inspect", "view", "download"}:
        return "read"
    return "organize"


def snapshot(registry, workspace=None, *, server_id=""):
    workspace = workspace or PersonalWorkspaceStore()
    binding = workspace.bindings().get("gmail") or {"server": "", "revision": 0}
    server_id = server_id or binding["server"] or "gmail"
    entry = registry.get_server(server_id)
    tools = []
    if entry:
        for name, record in (entry.get("tools") or {}).items():
            group = tool_group(name, entry)
            if group is None:
                continue
            tools.append({"name": name, "group": group, "fingerprint": record["fingerprint"],
                "allowed": registry.is_allowed(server_id, name), "blocked": record.get("blocked") is True,
                "automatic_read": registry.is_auto_read_allowed(server_id, name),
                "read_candidate": is_conservative_read_tool(name, record.get("input_schema")),
                "description": str(record.get("description") or "")[:400]})
    tools.sort(key=lambda t: (list(GROUPS).index(t["group"]), t["name"]))
    state = "not_configured" if not entry else "disabled" if not entry.get("enabled") else (
        "discovery_required" if not entry.get("last_discovery_success_utc") else "no_gmail_tools" if not tools else
        "tools_available" if any(t["allowed"] for t in tools) else "tools_to_allow")
    return {"server": server_id, "bound_server": binding["server"], "binding_revision": binding["revision"],
        "state": state, "connection_state": entry.get("connection_state") if entry else "disconnected",
        "runtime_enabled": registry.runtime_enabled(), "connection": connection_fingerprint(entry) if entry else "",
        "inventory": digest([{k: t[k] for k in ("name", "fingerprint", "allowed", "blocked", "automatic_read")} for t in tools]), "tools": tools,
        "groups": {g: {"discovered": sum(t["group"] == g for t in tools),
                        "allowed": sum(t["group"] == g and t["allowed"] for t in tools)} for g in GROUPS},
        "provider": "pipedream" if entry and entry.get("url") == PIPEDREAM_ENDPOINT else "existing",
        "mailbox_identity_verified": False, "mail_action_dispatched": False,
        "inventory_partial": bool(entry and entry.get("inventory_truncated"))}


def options(operation, value):
    packet = json.loads(value)
    expected = {"provider", "binding_revision"} if operation == "gmail_connect" else {
        "binding_revision", "connection", "inventory", "selected", "automatic_reads"}
    if operation == "gmail_connect" and isinstance(packet, dict) and "access" in packet:
        expected.add("access")
    if not isinstance(packet, dict) or set(packet) != expected or type(packet["binding_revision"]) is not int or packet["binding_revision"] < 0:
        raise ValueError("gmail_invalid_request")
    if operation == "gmail_connect":
        if packet["provider"] not in {"pipedream", "existing"}:
            raise ValueError("gmail_invalid_request")
        if packet.get("access", "manual") not in {"plugin", "manual"}:
            raise ValueError("gmail_invalid_request")
    else:
        if any(not isinstance(packet[k], str) or not re.fullmatch(r"[a-f0-9]{64}", packet[k]) for k in ("connection", "inventory")):
            raise ValueError("gmail_invalid_request")
        selected = packet["selected"]
        if (not isinstance(selected, list) or len(selected) > 150 or any(not isinstance(n, str) or
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", n) for n in selected) or len(set(selected)) != len(selected)
                or type(packet["automatic_reads"]) is not bool):
            raise ValueError("gmail_invalid_selection")
    return packet


def _binding(workspace, revision):
    row = workspace.bindings().get("gmail") or {"server": "", "revision": 0}
    if row["revision"] != revision:
        raise ValueError("gmail_binding_changed")
    return row


def connect(registry, server_id, packet, *, command_runner, workspace=None, stop_event=None):
    """Connect with the access policy explicitly chosen by the UI operation.

    The normal plugin button includes access to the discovered Gmail functions.
    Legacy/advanced manual connections retain their separate review step.
    """
    workspace = workspace or PersonalWorkspaceStore()
    binding = _binding(workspace, packet["binding_revision"])
    entry = registry.get_server(server_id)
    if packet["provider"] == "pipedream":
        if entry and (entry.get("kind") != "http" or entry.get("url") != PIPEDREAM_ENDPOINT):
            raise ValueError("gmail_server_conflict")
        if entry is None:
            registry.add_http(server_id, PIPEDREAM_ENDPOINT)
            entry = registry.get_server(server_id)
        if stop_event is not None and stop_event.is_set():
            raise ValueError("gmail_oauth_cancelled")
        command_runner("oauth_authorize", server_id, json.dumps({"scope": PIPEDREAM_SCOPE}))
        current = registry.get_server(server_id)
        if not current or current.get("url") != PIPEDREAM_ENDPOINT or current.get("credential_source") != "oauth":
            raise ValueError("gmail_connection_changed")
        # The user requested connecting this service as well as signing in.
        # Reload inventory after login; do not execute any account tool.
    elif entry is None:
        raise ValueError("gmail_server_not_found")
    if stop_event is not None and stop_event.is_set():
        raise ValueError("gmail_oauth_cancelled")
    command_runner("connect", server_id, "")
    _binding(workspace, packet["binding_revision"])
    if binding["server"] != server_id:
        workspace.bind_service("gmail", server_id, expected_revision=binding["revision"])
    status = snapshot(registry, workspace, server_id=server_id)
    plugin_access = packet.get("access") == "plugin"
    if plugin_access and status["tools"]:
        if stop_event is not None and stop_event.is_set():
            raise ValueError("gmail_oauth_cancelled")
        review = {k: status[k] for k in ("binding_revision", "connection", "inventory")}
        review.update(selected=[t["name"] for t in status["tools"]], automatic_reads=True)
        grant(registry, server_id, review, workspace=workspace, activate_runtime=True)
        status = snapshot(registry, workspace, server_id=server_id)
    message = ("Gmail est connecté à Jarvis. Tu peux maintenant lui demander de rechercher, résumer ou utiliser les fonctions disponibles."
        if plugin_access and status["tools"] else
        "Connexion MCP testée. Vérifie les capacités Gmail puis autorise les outils choisis." if status["tools"] else
        "Connexion MCP testée, aucun outil Gmail reconnu. Sélectionne Gmail chez le fournisseur puis actualise.")
    return {"success": True, "operation": "gmail_connect", "server_id": server_id,
        "message": message, "access_mode": "plugin" if plugin_access else "manual",
        "gmail": status, "mail_action_dispatched": False}


def grant(registry, server_id, packet, *, workspace=None, activate_runtime=False):
    workspace = workspace or PersonalWorkspaceStore()
    binding = _binding(workspace, packet["binding_revision"])
    if binding["server"] != server_id:
        raise ValueError("gmail_binding_changed")
    current = snapshot(registry, workspace, server_id=server_id)
    if packet["connection"] != current["connection"]:
        raise ValueError("gmail_connection_changed")
    if packet["inventory"] != current["inventory"]:
        raise ValueError("gmail_inventory_changed")
    if not current["tools"]:
        raise ValueError("gmail_no_tools")
    reviewed = {t["name"]: t["fingerprint"] for t in current["tools"]}
    result = registry.apply_tool_review(server_id, expected_connection=packet["connection"],
        reviewed=reviewed, selected=packet["selected"], automatic_reads=packet["automatic_reads"],
        activate_runtime=activate_runtime)
    return {"success": True, "operation": "gmail_grant", "server_id": server_id,
        "message": "Capacités Gmail enregistrées. Les envois et modifications conservent leurs confirmations.",
        "access": result, "mail_action_dispatched": False}


def error_reason(exc):
    from .mcp_oauth import connection_diagnostic, OAUTH_REASONS
    known = connection_diagnostic(exc).get("reason")
    if known in OAUTH_REASONS:
        return known
    reason = str(exc)
    if reason in _ERRORS:
        return reason
    if reason in {"oauth_cancelled", "oauth_timeout"}:
        return reason
    from .mcp_control import connection_failure_state
    return connection_failure_state(exc)
