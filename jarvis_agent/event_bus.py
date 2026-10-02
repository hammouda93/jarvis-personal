from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from .kernel_contracts import EventKind


@dataclass(frozen=True)
class BusEvent:
    kind: str
    mission_id: str
    payload: dict[str, Any]
    agent_id: str | None = None
    component: str | None = None


EventHandler = Callable[[BusEvent], None]


class MissionEventBus:
    """Small in-process pub/sub bus for future kernel observers.

    Handlers are isolated: one failing observer must not break the publisher.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._handlers: dict[str, list[EventHandler]] = {}
        self._wildcard: list[EventHandler] = []

    def subscribe(
        self,
        kind: EventKind | str | None,
        handler: EventHandler,
    ) -> None:
        with self._lock:
            if kind is None:
                if handler not in self._wildcard:
                    self._wildcard.append(handler)
                return
            key = kind.value if isinstance(kind, EventKind) else str(kind)
            handlers = self._handlers.setdefault(key, [])
            if handler not in handlers:
                handlers.append(handler)

    def unsubscribe(
        self,
        kind: EventKind | str | None,
        handler: EventHandler,
    ) -> None:
        with self._lock:
            if kind is None:
                self._wildcard = [
                    item for item in self._wildcard if item is not handler
                ]
                return
            key = kind.value if isinstance(kind, EventKind) else str(kind)
            self._handlers[key] = [
                item
                for item in self._handlers.get(key, [])
                if item is not handler
            ]

    def publish(self, event: BusEvent) -> list[str]:
        with self._lock:
            handlers = [
                *self._handlers.get(str(event.kind), []),
                *self._wildcard,
            ]

        errors: list[str] = []
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                errors.append(str(exc)[:500])
        return errors

    def subscriber_counts(self) -> dict[str, int]:
        with self._lock:
            result = {
                kind: len(handlers)
                for kind, handlers in self._handlers.items()
                if handlers
            }
            result["*"] = len(self._wildcard)
            return result
