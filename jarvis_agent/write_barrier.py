from __future__ import annotations

import threading
import time
from collections import defaultdict


class ScopedWriteBarrier:
    """Per-scope read-after-write barrier inspired by AIOS memory barriers.

    Writers get monotonically increasing sequence numbers. A reader snapshots
    the current sequence and waits only for writes that existed at snapshot
    time, never for newer writes that arrive later.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._issued: dict[str, int] = defaultdict(int)
        self._finished: dict[str, set[int]] = defaultdict(set)

    def begin_write(self, scope_key: str) -> int:
        key = str(scope_key or "global")
        with self._condition:
            self._issued[key] += 1
            return self._issued[key]

    def finish_write(self, scope_key: str, sequence: int) -> None:
        key = str(scope_key or "global")
        seq = int(sequence)
        with self._condition:
            self._finished[key].add(seq)
            self._condition.notify_all()

    def snapshot(self, scope_key: str) -> int:
        key = str(scope_key or "global")
        with self._condition:
            return int(self._issued[key])

    def pending_through(self, scope_key: str, snapshot: int) -> list[int]:
        key = str(scope_key or "global")
        upper = max(0, int(snapshot))
        with self._condition:
            finished = self._finished[key]
            return [
                seq
                for seq in range(1, upper + 1)
                if seq not in finished
            ]

    def wait_for_snapshot(
        self,
        scope_key: str,
        snapshot: int,
        *,
        timeout_s: float = 2.0,
    ) -> bool:
        key = str(scope_key or "global")
        upper = max(0, int(snapshot))
        deadline = time.monotonic() + max(0.0, float(timeout_s))

        with self._condition:
            while True:
                finished = self._finished[key]
                if all(
                    seq in finished
                    for seq in range(1, upper + 1)
                ):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
