from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .kernel_contracts import KnowledgeScope, MissionContext


@dataclass(frozen=True)
class ContextItem:
    item_id: str
    source: str
    content: str
    scope: KnowledgeScope
    relevance: float = 0.0
    priority: int = 100
    required: bool = False
    estimated_tokens: int | None = None

    def token_estimate(self) -> int:
        if self.estimated_tokens is not None:
            return max(0, int(self.estimated_tokens))
        # Cheap deterministic approximation: avoids introducing tokenizer deps.
        return max(1, (len(self.content) + 3) // 4)


@dataclass(frozen=True)
class ContextSelection:
    items: tuple[ContextItem, ...]
    estimated_tokens: int
    omitted_item_ids: tuple[str, ...]


class ContextBroker:
    """Budgeted context selection for future specialized agents.

    It does not alter the current conversation/history implementation.
    """

    def __init__(self, *, max_item_chars: int = 6000):
        self.max_item_chars = max(200, int(max_item_chars))

    @staticmethod
    def mission_item(context: MissionContext) -> ContextItem:
        summary = {
            "mission_id": context.mission_id,
            "goal": context.user_goal,
            "status": context.status.value,
            "current_step": context.current_step,
            "current_step_id": context.current_step_id,
            "owner_agent_id": context.owner_agent_id,
            "pending_confirmation": context.pending_confirmation,
            "expected_state": context.expected_state,
            "observed_state": context.observed_state,
            "artifacts": context.artifacts[-12:],
            "proof_refs": context.proof_refs[-12:],
            "knowledge_refs": context.knowledge_refs[-12:],
        }
        return ContextItem(
            item_id=f"mission:{context.mission_id}",
            source="mission_context",
            content=str(summary),
            scope=KnowledgeScope.SESSION,
            relevance=1.0,
            priority=0,
            required=True,
        )

    def select(
        self,
        items: Iterable[ContextItem],
        *,
        token_budget: int,
    ) -> ContextSelection:
        budget = max(1, int(token_budget))
        prepared: list[ContextItem] = []
        for item in items:
            content = str(item.content or "")
            if len(content) > self.max_item_chars:
                content = content[: self.max_item_chars] + "…"
                item = ContextItem(
                    item_id=item.item_id,
                    source=item.source,
                    content=content,
                    scope=item.scope,
                    relevance=item.relevance,
                    priority=item.priority,
                    required=item.required,
                    estimated_tokens=None,
                )
            prepared.append(item)

        required = sorted(
            [item for item in prepared if item.required],
            key=lambda item: (item.priority, item.item_id),
        )
        optional = sorted(
            [item for item in prepared if not item.required],
            key=lambda item: (
                item.priority,
                -max(0.0, min(float(item.relevance), 1.0)),
                item.item_id,
            ),
        )

        selected: list[ContextItem] = []
        used = 0
        omitted: list[str] = []

        for item in required:
            cost = item.token_estimate()
            # Required mission/system context wins even if it exceeds the
            # nominal budget; callers can detect the resulting estimate.
            selected.append(item)
            used += cost

        for item in optional:
            cost = item.token_estimate()
            if used + cost <= budget:
                selected.append(item)
                used += cost
            else:
                omitted.append(item.item_id)

        return ContextSelection(
            items=tuple(selected),
            estimated_tokens=used,
            omitted_item_ids=tuple(omitted),
        )
