from __future__ import annotations

from typing import Any, Callable

from .agent_knowledge import AgentKnowledgeStore
from .kernel_contracts import (
    KnowledgeIdentity,
    KnowledgeScope,
    SharingPolicy,
)
from .knowledge_broker import ScopedKnowledgeRecord


class LegacyAgentKnowledgeBackend:
    """Adapter from Jarvis' existing operational SQLite knowledge to scoped knowledge.

    No migration is performed. Existing skills/lessons/app profiles remain the
    source of truth. The adapter only projects them into the Kernel knowledge
    contract and can optionally map compatible writes back to the existing store.
    """

    def __init__(
        self,
        store: AgentKnowledgeStore,
        *,
        owner_user_id: str,
        default_agent_id: str = "personal_assistant",
        read_allowed: Callable[[], bool] | None = None,
        write_allowed: Callable[[], bool] | None = None,
    ):
        user_id = str(owner_user_id or "").strip()
        if not user_id:
            raise ValueError("owner_user_id_required")
        self.store = store
        self.owner_user_id = user_id
        self.default_agent_id = str(default_agent_id or "personal_assistant")
        self.read_allowed = read_allowed
        self.write_allowed = write_allowed

    def _identity(
        self,
        scope: KnowledgeScope,
        *,
        app_id: str | None = None,
        skill_id: str | None = None,
        agent_id: str | None = None,
    ) -> KnowledgeIdentity:
        return KnowledgeIdentity(
            scope=scope,
            owner_user_id=self.owner_user_id,
            owner_agent_id=agent_id,
            app_id=app_id,
            skill_id=skill_id,
            sharing_policy=SharingPolicy.USER_SHARED,
        )

    def search(
        self,
        query: str,
        *,
        limit: int,
    ) -> list[ScopedKnowledgeRecord]:
        if self.read_allowed is not None and not self.read_allowed():
            return []
        context = self.store.relevant_context(
            str(query or ""),
            limit=max(1, min(int(limit), 12)),
        )
        records: list[ScopedKnowledgeRecord] = []

        for item in context.get("skills") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            app_scope = str(item.get("app_scope") or "").strip() or None
            procedure = [
                str(step)
                for step in item.get("procedure") or []
                if str(step)
            ]
            content = " | ".join(
                [
                    f"Skill: {name}",
                    str(item.get("goal") or ""),
                    "Procedure: " + " -> ".join(procedure),
                ]
            ).strip(" |")
            records.append(
                ScopedKnowledgeRecord(
                    knowledge_id=f"legacy_skill:{name}",
                    identity=self._identity(
                        KnowledgeScope.SKILL,
                        app_id=app_scope,
                        skill_id=name,
                    ),
                    content=content,
                    metadata={
                        "legacy_kind": "skill",
                        **dict(item),
                    },
                    relevance=float(item.get("confidence") or 0.0),
                )
            )

        for item in context.get("lessons") or []:
            lesson_id = str(item.get("id") or "").strip()
            scope_text = str(item.get("scope") or "global").strip()
            record_id = lesson_id or (
                str(item.get("pattern") or "")[:80].replace(" ", "_")
            )
            content = " | ".join(
                [
                    f"Lesson scope={scope_text}",
                    str(item.get("pattern") or ""),
                    str(item.get("rule") or ""),
                ]
            ).strip(" |")
            records.append(
                ScopedKnowledgeRecord(
                    knowledge_id=f"legacy_lesson:{record_id}",
                    identity=self._identity(KnowledgeScope.USER),
                    content=content,
                    metadata={
                        "legacy_kind": "lesson",
                        **dict(item),
                    },
                    relevance=float(item.get("confidence") or 0.0),
                )
            )

        for item in context.get("app_profiles") or []:
            app_key = str(
                item.get("app_key")
                or item.get("display_name")
                or ""
            ).strip()
            if not app_key:
                continue
            aliases = [
                str(value)
                for value in item.get("aliases") or []
                if str(value)
            ]
            caps = [
                str(value)
                for value in item.get("observed_capabilities") or []
                if str(value)
            ]
            content = " | ".join(
                [
                    f"Application: {item.get('display_name') or app_key}",
                    "Aliases: " + ", ".join(aliases),
                    "Capabilities: " + ", ".join(caps),
                ]
            ).strip(" |")
            records.append(
                ScopedKnowledgeRecord(
                    knowledge_id=f"legacy_app:{app_key}",
                    identity=self._identity(
                        KnowledgeScope.APP,
                        app_id=app_key,
                    ),
                    content=content,
                    metadata={
                        "legacy_kind": "app_profile",
                        **dict(item),
                    },
                    relevance=float(item.get("confidence") or 0.0),
                )
            )

        records.sort(
            key=lambda item: (
                -max(0.0, min(float(item.relevance), 1.0)),
                item.knowledge_id,
            )
        )
        return records[: max(1, int(limit))]

    def upsert(self, record: ScopedKnowledgeRecord) -> None:
        """Map compatible future scoped writes back to today's store.

        CORE knowledge is intentionally unsupported here; Core changes belong to
        Dev Supervisor + regression, never ordinary memory writes.
        """
        identity = record.identity
        if identity.owner_user_id != self.owner_user_id:
            raise PermissionError("legacy_knowledge_user_mismatch")
        if self.write_allowed is not None and not self.write_allowed():
            raise PermissionError("operational_learning_disabled")
        if identity.scope == KnowledgeScope.CORE:
            raise PermissionError("core_knowledge_requires_dev_supervisor")

        meta: dict[str, Any] = dict(record.metadata or {})
        legacy_kind = str(meta.get("legacy_kind") or "").strip()

        if identity.scope == KnowledgeScope.SKILL or legacy_kind == "skill":
            name = str(
                meta.get("name")
                or identity.skill_id
                or record.knowledge_id.replace("legacy_skill:", "")
            ).strip()
            procedure = [
                str(item)
                for item in meta.get("procedure") or []
                if str(item)
            ]
            if not procedure:
                raise ValueError("skill_procedure_required")
            self.store.upsert_skill(
                name=name,
                goal=str(meta.get("goal") or record.content)[:500],
                procedure=procedure,
                success_checks=list(meta.get("success_checks") or []),
                failure_patterns=list(meta.get("failure_patterns") or []),
                app_scope=str(
                    meta.get("app_scope")
                    or identity.app_id
                    or ""
                ),
                confidence=float(meta.get("confidence") or record.relevance or 0.65),
                source=str(meta.get("source") or "kernel_adapter"),
            )
            return

        if identity.scope == KnowledgeScope.APP or legacy_kind == "app_profile":
            display = str(
                meta.get("display_name")
                or identity.app_id
                or record.knowledge_id.replace("legacy_app:", "")
            ).strip()
            self.store.upsert_app_profile(
                display_name=display,
                aliases=list(meta.get("aliases") or []),
                launch_hint=str(meta.get("launch_hint") or ""),
                window_title_patterns=list(
                    meta.get("window_title_patterns") or []
                ),
                observed_capabilities=list(
                    meta.get("observed_capabilities") or []
                ),
                success=meta.get("success"),
                confidence=float(meta.get("confidence") or record.relevance or 0.65),
            )
            return

        if (
            identity.scope in {KnowledgeScope.USER, KnowledgeScope.AGENT}
            or legacy_kind == "lesson"
        ):
            pattern = str(meta.get("pattern") or "").strip()
            rule = str(meta.get("rule") or record.content).strip()
            if not pattern:
                pattern = rule[:160]
            self.store.record_lesson(
                scope=str(meta.get("scope") or identity.scope.value),
                pattern=pattern,
                rule=rule,
                confidence=float(meta.get("confidence") or record.relevance or 0.7),
                source=str(meta.get("source") or "kernel_adapter"),
            )
            return

        raise ValueError("unsupported_legacy_knowledge_scope")
