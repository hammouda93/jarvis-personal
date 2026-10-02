from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable

from .kernel_contracts import RiskLevel


class ConnectorBackend(str, Enum):
    API = "api"
    MCP = "mcp"
    LOCAL_SDK = "local_sdk"
    DATABASE = "database"
    BROWSER = "browser"
    WINDOWS_UI = "windows_ui"


@dataclass(frozen=True)
class ConnectorCapability:
    name: str
    description: str
    risk: RiskLevel = RiskLevel.READ
    requires_confirmation: bool = False

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["risk"] = self.risk.value
        return data


@dataclass(frozen=True)
class ConnectorSpec:
    connector_id: str
    display_name: str
    capabilities: tuple[ConnectorCapability, ...]
    preferred_backends: tuple[ConnectorBackend, ...]
    auth_kind: str
    enabled: bool = False
    notes: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "connector_id": self.connector_id,
            "display_name": self.display_name,
            "capabilities": [
                capability.as_dict()
                for capability in self.capabilities
            ],
            "preferred_backends": [
                backend.value for backend in self.preferred_backends
            ],
            "auth_kind": self.auth_kind,
            "enabled": bool(self.enabled),
            "notes": self.notes,
        }


class ConnectorRegistry:
    """Metadata-only connector registry.

    No credentials are stored here and no network/UI action is performed.
    """

    def __init__(self):
        self._connectors: dict[str, ConnectorSpec] = {}

    def register(self, spec: ConnectorSpec) -> None:
        self._connectors[spec.connector_id] = spec

    def get(self, connector_id: str) -> ConnectorSpec | None:
        return self._connectors.get(str(connector_id))

    def all(self) -> list[ConnectorSpec]:
        return sorted(
            self._connectors.values(),
            key=lambda item: item.connector_id,
        )

    def supports(
        self,
        connector_id: str,
        capability_name: str,
    ) -> bool:
        spec = self.get(connector_id)
        if spec is None:
            return False
        return any(
            capability.name == capability_name
            for capability in spec.capabilities
        )

    def choose_backend(
        self,
        connector_id: str,
        available: Iterable[ConnectorBackend | str],
    ) -> ConnectorBackend | None:
        spec = self.get(connector_id)
        if spec is None:
            return None
        available_values = {
            item.value if isinstance(item, ConnectorBackend) else str(item)
            for item in available
        }
        for backend in spec.preferred_backends:
            if backend.value in available_values:
                return backend
        return None


def build_default_connector_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()

    registry.register(
        ConnectorSpec(
            connector_id="gmail",
            display_name="Gmail",
            capabilities=(
                ConnectorCapability(
                    "search_messages",
                    "Search authorized Gmail messages.",
                    RiskLevel.READ,
                ),
                ConnectorCapability(
                    "read_message",
                    "Read an authorized Gmail message or thread.",
                    RiskLevel.READ,
                ),
                ConnectorCapability(
                    "create_draft",
                    "Create a draft without sending it.",
                    RiskLevel.REVERSIBLE,
                ),
                ConnectorCapability(
                    "send_message",
                    "Send an external Gmail message.",
                    RiskLevel.EXTERNAL_SIDE_EFFECT,
                    requires_confirmation=True,
                ),
            ),
            preferred_backends=(
                ConnectorBackend.API,
                ConnectorBackend.MCP,
                ConnectorBackend.BROWSER,
            ),
            auth_kind="oauth2",
            enabled=False,
            notes=(
                "Prefer Gmail API/OAuth. Browser fallback is secondary."
            ),
        )
    )

    registry.register(
        ConnectorSpec(
            connector_id="whatsapp",
            display_name="WhatsApp",
            capabilities=(
                ConnectorCapability(
                    "find_contact",
                    "Find a conversation/contact.",
                    RiskLevel.READ,
                ),
                ConnectorCapability(
                    "read_conversation",
                    "Read visible/authorized recent messages.",
                    RiskLevel.READ,
                ),
                ConnectorCapability(
                    "compose_message",
                    "Prepare message text without sending.",
                    RiskLevel.REVERSIBLE,
                ),
                ConnectorCapability(
                    "send_message",
                    "Send an external WhatsApp message.",
                    RiskLevel.EXTERNAL_SIDE_EFFECT,
                    requires_confirmation=True,
                ),
            ),
            preferred_backends=(
                ConnectorBackend.API,
                ConnectorBackend.MCP,
                ConnectorBackend.WINDOWS_UI,
                ConnectorBackend.BROWSER,
            ),
            auth_kind="provider_or_user_session",
            enabled=False,
            notes=(
                "Business/API when available; Windows/Web UI fallback for "
                "capabilities not exposed through the authorized API."
            ),
        )
    )

    registry.register(
        ConnectorSpec(
            connector_id="instagram",
            display_name="Instagram",
            capabilities=(
                ConnectorCapability(
                    "open_profile",
                    "Open or resolve a profile.",
                    RiskLevel.READ,
                ),
                ConnectorCapability(
                    "read_notifications",
                    "Read authorized notifications/messages when supported.",
                    RiskLevel.READ,
                ),
                ConnectorCapability(
                    "create_draft",
                    "Prepare supported content without publishing.",
                    RiskLevel.REVERSIBLE,
                ),
                ConnectorCapability(
                    "publish",
                    "Publish supported content externally.",
                    RiskLevel.EXTERNAL_SIDE_EFFECT,
                    requires_confirmation=True,
                ),
            ),
            preferred_backends=(
                ConnectorBackend.API,
                ConnectorBackend.MCP,
                ConnectorBackend.BROWSER,
                ConnectorBackend.WINDOWS_UI,
            ),
            auth_kind="oauth_or_user_session",
            enabled=False,
            notes=(
                "Use official APIs where available; browser/UI only for "
                "capabilities not exposed by the authorized API."
            ),
        )
    )

    registry.register(
        ConnectorSpec(
            connector_id="github",
            display_name="GitHub",
            capabilities=(
                ConnectorCapability(
                    "read_repository",
                    "Read repository content and metadata.",
                    RiskLevel.READ,
                ),
                ConnectorCapability(
                    "create_change",
                    "Create controlled code changes.",
                    RiskLevel.REVERSIBLE,
                ),
            ),
            preferred_backends=(
                ConnectorBackend.API,
                ConnectorBackend.MCP,
            ),
            auth_kind="oauth_or_token",
            enabled=False,
            notes="Intended for Developer Agent / Dev Supervisor.",
        )
    )

    return registry


DEFAULT_CONNECTOR_REGISTRY = build_default_connector_registry()
