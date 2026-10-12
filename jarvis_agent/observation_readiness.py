"""Bounded read-only observation readiness; independent of website/app names."""
from __future__ import annotations

import time
from typing import Any, Callable


def bounded_observation(read: Callable[[], dict], *, attempts: int = 7,
                        delay_seconds: float = 0.35,
                        sleep: Callable[[float], None] = time.sleep) -> dict:
    """Never repeats a mutation: only retries the same read-only sensor.

    A pending result is an *observed state* (not proof of mission success).
    The last pending result is returned honestly if the page stays loading.
    """
    if not 1 <= attempts <= 12 or delay_seconds < 0:
        raise ValueError("invalid_observation_budget")
    for index in range(attempts):
        result = read()
        if not isinstance(result, dict) or result.get("ready") is not False:
            return result
        if index + 1 < attempts:
            sleep(delay_seconds)
    return result
