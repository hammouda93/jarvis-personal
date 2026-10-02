from __future__ import annotations

import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LocalRPCPolicy:
    host: str = "127.0.0.1"
    require_token: bool = True

    def validate_host(self) -> bool:
        value = str(self.host or "").strip()
        if value.lower() == "localhost":
            return True
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False
        return bool(address.is_loopback)


class CapabilityTokenAuthority:
    """Small local-RPC token authority.

    Tokens are process-local by design; persistence belongs to an OS secret
    store later. Nothing in this module starts a network server.
    """

    def __init__(self):
        self._tokens: dict[str, set[str]] = {}

    def issue(self, capabilities: set[str]) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = {
            str(item)
            for item in capabilities
            if str(item)
        }
        return token

    def revoke(self, token: str) -> None:
        self._tokens.pop(str(token), None)

    def authorize(self, token: str, capability: str) -> bool:
        supplied = str(token or "")
        matched: str | None = None
        for known in self._tokens:
            if hmac.compare_digest(known, supplied):
                matched = known
                break
        if matched is None:
            return False
        return str(capability) in self._tokens[matched]

    def describe(self, token: str) -> dict[str, Any] | None:
        for known, capabilities in self._tokens.items():
            if hmac.compare_digest(known, str(token or "")):
                return {
                    "capabilities": sorted(capabilities),
                    "persistent": False,
                }
        return None
