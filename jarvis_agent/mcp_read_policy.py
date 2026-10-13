"""Conservative, local MCP *read* classification for an operator-scoped grant.

MCP tool descriptions and annotations are UNTRUSTED and cannot confer
permissions. This is deliberately restrictive: a tool whose effect is not
obviously observational MUST use the existing per-tool/per-call approval path.
The connected service's independently scoped token remains authoritative.
"""
from __future__ import annotations

import re

_READ_STARTS = frozenset({
    "get", "list", "search", "read", "fetch", "find", "lookup", "inspect",
    "describe", "view",
})
_DENIED_WORDS = frozenset({
    "write", "edit", "update", "create", "delete", "remove", "merge", "push",
    "post", "send", "set", "save", "publish", "grant", "revoke", "invite",
    "approve", "close", "archive", "dispatch", "execute", "run", "rerun",
    "retry", "trigger", "cancel", "reset", "stop", "lock", "unlock", "deploy",
    "fork", "mark", "reopen", "assign", "label", "comment",
    "workflowdispatch", "workflow_dispatch", "credential", "credentials",
    "secret", "secrets", "token", "tokens", "password", "passwords",
    "key", "keys", "privatekey", "private", "billing", "payment",
    "access", "permission", "permissions", "ssh", "certificate",
})


def is_conservative_read_tool(name: str, input_schema: dict | None = None) -> bool:
    """Admit only descriptive/query tools with no obviously mutating controls.

    This classification only narrows an operator-approved server-scoped grant.
    Never turn server login/discovery alone into permission. It is not a proof
    of remote tool purity; users should authorize minimal read-only credentials.
    """
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", name):
        return False
    words = [v.lower() for v in re.split(r"[._-]+", name) if v]
    if len(words) < 2 or any(word in _DENIED_WORDS for word in words):
        return False
    if words[0] not in _READ_STARTS and words[-1] not in _READ_STARTS:
        return False
    if input_schema is not None:
        if not isinstance(input_schema, dict) or input_schema.get("type", "object") != "object":
            return False
        props = input_schema.get("properties") or {}
        if not isinstance(props, dict):
            return False
        # Names that take force/execute/patch/delete toggles are ambiguous,
        # regardless of how innocuous the tool's public title sounds.
        if any(re.search(r"(?i)(?:^|[_-])(force|write|update|delete|remove|"
                         r"create|submit|merge|push|execute|dispatch|approve|"
                         r"mutate|dry_run_override)(?:$|[_-])", str(k))
               for k in props):
            return False
    return True
