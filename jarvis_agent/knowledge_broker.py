from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .kernel_contracts import KnowledgeIdentity
from .knowledge_policy import (
    KnowledgeAccessPolicy,
    KnowledgePrincipal,
)
from .write_barrier import ScopedWriteBarrier


@dataclass
class ScopedKnowledgeRecord:
    knowledge_id: str
    identity: KnowledgeIdentity
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    relevance: float = 0.0


class ScopedKnowledgeBackend(Protocol):
    def search(
        self,
        query: str,
        *,
        limit: int,
    ) -> list[ScopedKnowledgeRecord]:
        ...

    def upsert(self, record: ScopedKnowledgeRecord) -> None:
        ...


class KnowledgeBroker:
    """Isolation + read-after-write boundary for future agent knowledge."""

    def __init__(
        self,
        backend: ScopedKnowledgeBackend,
        *,
        policy: KnowledgeAccessPolicy | None = None,
        barrier: ScopedWriteBarrier | None = None,
        read_timeout_s: float = 2.0,
    ):
        self.backend = backend
        self.policy = policy or KnowledgeAccessPolicy()
        self.barrier = barrier or ScopedWriteBarrier()
        self.read_timeout_s = max(0.05, float(read_timeout_s))

    @staticmethod
    def scope_key(principal: KnowledgePrincipal) -> str:
        return "|".join(
            (
                f"user={principal.user_id or ''}",
                f"agent={principal.agent_id or ''}",
                f"org={principal.organization_id or ''}",
            )
        )

    def search(
        self,
        query: str,
        *,
        principal: KnowledgePrincipal,
        limit: int = 8,
    ) -> list[ScopedKnowledgeRecord]:
        key = self.scope_key(principal)
        snapshot = self.barrier.snapshot(key)
        self.barrier.wait_for_snapshot(
            key,
            snapshot,
            timeout_s=self.read_timeout_s,
        )

        candidates = self.backend.search(
            str(query or ""),
            limit=max(1, min(int(limit) * 4, 100)),
        )
        allowed = [
            record
            for record in candidates
            if self.policy.can_read(record.identity, principal)
        ]
        allowed.sort(
            key=lambda record: (
                -max(0.0, min(float(record.relevance), 1.0)),
                record.knowledge_id,
            )
        )
        return allowed[: max(1, min(int(limit), 50))]

    def write(
        self,
        record: ScopedKnowledgeRecord,
        *,
        principal: KnowledgePrincipal,
    ) -> bool:
        if not self.policy.can_write(record.identity, principal):
            return False

        key = self.scope_key(principal)
        sequence = self.barrier.begin_write(key)
        try:
            self.backend.upsert(record)
            return True
        finally:
            self.barrier.finish_write(key, sequence)


class InMemoryKnowledgeBackend:
    """Test/reference backend; production Jarvis keeps its current SQLite store."""

    def __init__(self):
        self.records: dict[str, ScopedKnowledgeRecord] = {}

    def search(
        self,
        query: str,
        *,
        limit: int,
    ) -> list[ScopedKnowledgeRecord]:
        terms = {
            token
            for token in str(query or "").lower().split()
            if token
        }
        ranked: list[ScopedKnowledgeRecord] = []
        for record in self.records.values():
            haystack = (
                record.content + " " + str(record.metadata)
            ).lower()
            if terms and not any(term in haystack for term in terms):
                continue
            ranked.append(record)
        ranked.sort(
            key=lambda record: (
                -max(0.0, min(float(record.relevance), 1.0)),
                record.knowledge_id,
            )
        )
        return ranked[: max(1, int(limit))]

    def upsert(self, record: ScopedKnowledgeRecord) -> None:
        self.records[record.knowledge_id] = record
