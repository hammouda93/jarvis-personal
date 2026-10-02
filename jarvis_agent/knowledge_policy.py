from __future__ import annotations

from dataclasses import dataclass

from .kernel_contracts import (
    KnowledgeIdentity,
    KnowledgeScope,
    SharingPolicy,
)


@dataclass(frozen=True)
class KnowledgePrincipal:
    user_id: str | None
    agent_id: str | None
    organization_id: str | None = None


class KnowledgeAccessPolicy:
    """Fail-closed isolation policy for future cross-agent knowledge access."""

    @staticmethod
    def can_read(
        identity: KnowledgeIdentity,
        principal: KnowledgePrincipal,
    ) -> bool:
        if identity.scope == KnowledgeScope.CORE:
            return identity.sharing_policy in {
                SharingPolicy.PUBLIC,
                SharingPolicy.AGENT_SHARED,
                SharingPolicy.USER_SHARED,
            }

        if identity.organization_id is not None:
            if principal.organization_id != identity.organization_id:
                return False

        if identity.owner_user_id is not None:
            if principal.user_id != identity.owner_user_id:
                return False

        if identity.sharing_policy == SharingPolicy.PUBLIC:
            return True

        if identity.sharing_policy == SharingPolicy.ORGANIZATION_SHARED:
            return (
                identity.organization_id is not None
                and principal.organization_id == identity.organization_id
            )

        if identity.sharing_policy == SharingPolicy.USER_SHARED:
            return (
                identity.owner_user_id is not None
                and principal.user_id == identity.owner_user_id
            )

        if identity.sharing_policy == SharingPolicy.AGENT_SHARED:
            return (
                identity.owner_agent_id is not None
                and principal.agent_id == identity.owner_agent_id
            )

        # PRIVATE: require every declared owner dimension to match.
        if identity.owner_agent_id is not None:
            if principal.agent_id != identity.owner_agent_id:
                return False
        if identity.owner_user_id is not None:
            if principal.user_id != identity.owner_user_id:
                return False
        if identity.organization_id is not None:
            if principal.organization_id != identity.organization_id:
                return False

        # Unowned PRIVATE knowledge is unsafe to expose.
        return any(
            value is not None
            for value in (
                identity.owner_user_id,
                identity.owner_agent_id,
                identity.organization_id,
            )
        )

    @staticmethod
    def can_write(
        identity: KnowledgeIdentity,
        principal: KnowledgePrincipal,
    ) -> bool:
        # Core changes are intentionally never allowed through ordinary
        # knowledge writes. They need the Dev Supervisor + regression path.
        if identity.scope == KnowledgeScope.CORE:
            return False

        if identity.organization_id is not None:
            if principal.organization_id != identity.organization_id:
                return False

        if identity.owner_user_id is not None:
            if principal.user_id != identity.owner_user_id:
                return False

        if identity.owner_agent_id is not None:
            if principal.agent_id != identity.owner_agent_id:
                return False

        # Writing shared knowledge still requires ownership of its partition.
        if identity.sharing_policy == SharingPolicy.ORGANIZATION_SHARED:
            return (
                identity.organization_id is not None
                and principal.organization_id == identity.organization_id
            )
        if identity.sharing_policy == SharingPolicy.USER_SHARED:
            return (
                identity.owner_user_id is not None
                and principal.user_id == identity.owner_user_id
            )
        if identity.sharing_policy == SharingPolicy.AGENT_SHARED:
            return (
                identity.owner_agent_id is not None
                and principal.agent_id == identity.owner_agent_id
            )
        if identity.sharing_policy == SharingPolicy.PUBLIC:
            return False

        return any(
            value is not None
            for value in (
                identity.owner_user_id,
                identity.owner_agent_id,
                identity.organization_id,
            )
        )
