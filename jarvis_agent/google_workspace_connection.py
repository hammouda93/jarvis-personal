"""Explicit, worker-only Google account setup. No background account probing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .google_workspace_catalog import CLIENT_SLOT, GOOGLE_TARGET, SERVICES, admitted_operations
from .google_workspace_oauth import GoogleConnectionError, GoogleDesktopOAuth, parse_client
from .mcp_credentials import CredentialVault
from .personal_workspace import PersonalWorkspaceStore

OPERATIONS = frozenset({"google_import_client", "google_connect", "google_disconnect", "google_test"})
REASONS = frozenset({"google_desktop_client_required", "google_token_invalid", "google_identity_unverified",
    "google_response_too_large", "google_response_invalid", "google_browser_open_failed", "google_client_required",
    "google_client_changed", "google_bindings_changed", "google_no_services_authorized", "google_server_conflict",
    "google_client_import_failed", "google_connection_failed", "google_consent_denied", "google_not_connected", "google_vault_unavailable", "google_dependency_missing"})


def options(operation, value):
    packet = json.loads(value)
    if operation == "google_import_client":
        if (not isinstance(packet, dict) or set(packet) != {"path"} or not isinstance(packet["path"], str)
                or not 1 <= len(packet["path"]) <= 2000 or any(c in packet["path"] for c in "\r\n\x00")):
            raise ValueError("invalid_google_options")
    elif operation == "google_connect":
        if (not isinstance(packet, dict) or set(packet) != {"binding_revisions", "client_fingerprint"}
                or not isinstance(packet["client_fingerprint"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", packet["client_fingerprint"])
                or not isinstance(packet["binding_revisions"], dict)
                or set(packet["binding_revisions"]) != set(SERVICES)
                or any(type(n) is not int or n < 0 for n in packet["binding_revisions"].values())):
            raise ValueError("invalid_google_options")
    else:
        raise ValueError("invalid_google_operation")
    return packet


def error_reason(exc):
    from .mcp_credentials import CredentialUnavailable
    if isinstance(exc, CredentialUnavailable):
        return "google_vault_unavailable"
    if isinstance(exc, ImportError):
        return "google_dependency_missing"
    reason = str(exc)
    return reason if reason in REASONS | {"oauth_cancelled", "oauth_timeout"} else "google_connection_failed"


def snapshot(registry, workspace=None):
    workspace = workspace or PersonalWorkspaceStore()
    metadata = registry.google_client_metadata()
    sid = registry.active_google_server()
    entry = registry.get_server(sid) if sid else None
    if entry and entry.get("kind") != "google":
        entry = None
    bindings = workspace.bindings()
    # A display snapshot, not a permission grant. Read the account once rather
    # than reopening/parsing its registry for every tool at every Qt tick.
    allowed = {name for name, tool in (entry or {}).get("tools", {}).items()
        if entry.get("enabled") is True and tool.get("blocked") is not True
        and (tool.get("allowed") is True or registry._is_auto_read(entry, tool))}
    from .google_workspace_catalog import OPERATIONS as catalog
    return {"server_id": sid, "client_configured": metadata.get("configured") is True,
        "client_fingerprint": metadata.get("fingerprint", ""), "project_id": metadata.get("project_id", ""),
        "enabled": bool(entry and entry.get("enabled")), "runtime_enabled": registry.runtime_enabled(),
        "email": ((entry or {}).get("google_account") or {}).get("email", ""),
        "binding_revisions": {service: (bindings.get(service) or {}).get("revision", 0) for service in SERVICES},
        "services": [{"id": service, "label": label,
            "tools": [op.name for op in catalog if op.service == service and op.name in allowed],
            "binding_active": bool(sid and (bindings.get(service) or {}).get("server") == sid)} for service, label in SERVICES.items()]}


def _check_bindings(workspace, revisions):
    bindings = workspace.bindings()
    if any((bindings.get(service) or {}).get("revision", 0) != revisions[service] for service in SERVICES):
        raise GoogleConnectionError("google_bindings_changed")
    return bindings


def perform_google_command(registry, operation, server_id, value="", *, vault=None, workspace=None,
                           oauth=None, transport=None, stop_event=None, progress=None):
    if operation not in OPERATIONS or operation in {"google_import_client", "google_connect"} and server_id != CLIENT_SLOT:
        raise ValueError("invalid_google_operation")
    workspace = workspace or PersonalWorkspaceStore()
    vault = vault or CredentialVault(registry.path.parent / "mcp_credentials")
    if operation == "google_import_client":
        path = Path(options(operation, value)["path"])
        try:
            with path.open("r", encoding="utf-8-sig") as stream:
                raw = stream.read(32769)
            if len(raw) > 32768:
                raise GoogleConnectionError("google_desktop_client_required")
            client = parse_client(raw)
        except OSError:
            raise GoogleConnectionError("google_client_import_failed") from None
        fingerprint = hashlib.sha256(client["client_id"].encode()).hexdigest()
        vault.write(CLIENT_SLOT, GOOGLE_TARGET, json.dumps(client), "oauth_client")
        registry.note_google_client(project_id=client["project_id"], fingerprint=fingerprint)
        return {"success": True, "operation": operation, "server_id": CLIENT_SLOT,
            "message": "Configuration Google importée dans le coffre sécurisé. Clique sur Connecter Google."}
    if operation == "google_connect":
        packet = options(operation, value)
        _check_bindings(workspace, packet["binding_revisions"])
        metadata = registry.google_client_metadata()
        if metadata.get("fingerprint") != packet["client_fingerprint"]:
            raise GoogleConnectionError("google_client_changed")
        raw = vault.read(CLIENT_SLOT, GOOGLE_TARGET, "oauth_client")
        if not raw:
            raise GoogleConnectionError("google_client_required")
        client = json.loads(raw)
        if hashlib.sha256(client["client_id"].encode()).hexdigest() != packet["client_fingerprint"]:
            raise GoogleConnectionError("google_client_changed")
        bundle = (oauth or GoogleDesktopOAuth()).authorize(client, stop_event=stop_event, progress=progress)
        if stop_event is not None and stop_event.is_set():
            raise GoogleConnectionError("oauth_cancelled")
        if registry.google_client_metadata().get("fingerprint") != packet["client_fingerprint"]:
            raise GoogleConnectionError("google_client_changed")
        bindings = _check_bindings(workspace, packet["binding_revisions"])
        ops = admitted_operations(bundle["token"]["scopes"])
        services = [service for service in SERVICES if any(op.service == service for op in ops)]
        if not services:
            raise GoogleConnectionError("google_no_services_authorized")
        sid = "google_" + bundle["account"]["subject_hash"][:16]
        old = registry.get_server(sid)
        old_tokens = vault.read(sid, GOOGLE_TARGET, "oauth_tokens")
        if old and old.get("kind") != "google":
            raise GoogleConnectionError("google_server_conflict")
        mapping = {service: sid for service in services}
        mapping.update({service: "" for service in SERVICES if service not in services
            and (bindings.get(service) or {}).get("server") == sid})
        previous = {service: (bindings.get(service) or {}).get("server", "") for service in mapping}
        # A legacy connector is paused only if no unrelated workspace binding
        # uses it. Credentials, tools, history and quotas are retained.
        pause = []
        for prior in set(previous.values()) - {"", sid}:
            uses = {service for service, binding in bindings.items() if binding["server"] == prior}
            if uses <= set(mapping):
                pause.append(prior)
        revisions = None
        try:
            registry.install_google_account(sid, account=bundle["account"], services=services,
                tools=[op.definition() for op in ops], activate=False)
            if progress:
                progress({"stage": "credentials_storage"})
            vault.write(sid, GOOGLE_TARGET, json.dumps(bundle), "oauth_tokens")
            revisions = workspace.bind_service_map(mapping, expected_revisions=packet["binding_revisions"])
            registry.activate_google_account(sid, pause_servers=pause)
        except Exception:
            # No automatic external retry. Handled commit failures restore
            # bindings and keep the new provider disabled until rollback is
            # complete. A process crash at any intermediate stage fails shut.
            if registry.get_server(sid):
                registry.set_enabled(sid, False)
            if revisions:
                workspace.bind_service_map(previous, expected_revisions=revisions)
            if old_tokens is not None:
                vault.write(sid, GOOGLE_TARGET, old_tokens, "oauth_tokens")
            else:
                vault.delete(sid, "oauth_tokens")
            if old:
                with registry._lock:
                    data = registry._load()
                    data["servers"][sid] = old
                    registry._write(data)
            raise
        if progress:
            progress({"stage": "authorized"})
        return {"success": True, "operation": operation, "server_id": sid,
            "message": "Compte Google autorisé et fonctions activées. Vérifie Gmail, puis utilise les outils dans la conversation. Aucun mail ni document lu pendant la connexion.",
            "google": snapshot(registry, workspace), "paused_servers": pause}
    entry = registry.get_server(server_id)
    if not entry or entry.get("kind") != "google":
        raise GoogleConnectionError("google_not_connected")
    if operation == "google_disconnect":
        registry.set_enabled(server_id, False)
        registry.revoke_tools(server_id)
        vault.delete(server_id, "oauth_tokens")
        return {"success": True, "operation": operation, "server_id": server_id,
            "message": "Google déconnecté de Jarvis. Configuration de l’application conservée ; aucun fichier ou mail supprimé."}
    if operation == "google_test":
        from .mcp_tool_registry import MCPToolRegistry
        class NoNative:
            def ollama_tools(self):
                return []
        gateway = MCPToolRegistry(NoNative(), registry=registry, transport=transport,
            runtime_gate=registry.runtime_enabled)
        results = []
        for name in ("gmail_get_profile", "gmail_get_mailbox_counts"):
            action = gateway.execute("mcp__" + server_id + "__" + name, {})
            data = json.loads(action.detail)
            if not action.success:
                return {"success": False, "operation": operation, "server_id": server_id,
                    "reason": data.get("guard") or "google_read_failed", "diagnostic": data.get("diagnostic", {}),
                    "message": "La lecture Gmail n’a pas abouti. Vérifie l’activation de l’API Gmail et l’autorisation Google. Aucun changement envoyé."}
            results.append(data.get("mcp_structured") or {})
        profile, counts = results
        unread = (counts.get("unread") or {}).get("messagesTotal")
        inbox = (counts.get("inbox") or {}).get("messagesUnread")
        if type(unread) is not int or type(inbox) is not int or unread < 0 or inbox < 0:
            raise GoogleConnectionError("google_response_invalid")
        return {"success": True, "operation": operation, "server_id": server_id,
            "message": "Gmail répond : " + str(profile.get("emailAddress", "")) + "\n"
                + str(unread) + " messages non lus au total ; " + str(inbox) + " dans la boîte de réception. Aucune modification.",
            "unread_messages": unread, "inbox_unread_messages": inbox}
    raise ValueError("invalid_google_operation")
