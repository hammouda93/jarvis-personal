"""Local, deterministic provider admission checks.

Do not turn a provider rate limit into a storm of duplicate requests.
No API calls, sleeping, credentials or token-accounting claims here.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable


class ProviderRateGate:
    """In-process circuit breaker for provider accounts returning HTTP 429."""

    def __init__(self, *, cooldown_seconds: float = 60.0,
                 clock: Callable[[], float] | None = None):
        self.cooldown_seconds = max(1.0, min(float(cooldown_seconds), 3600.0))
        self.clock = clock if clock is not None else time.monotonic
        self._until: dict[str, float] = {}

    def note_failure(self, provider: str, error: Exception) -> bool:
        detail = str(error).lower()
        limited = ("429" in detail or "request_quota_exceeded" in detail
                   or "token_quota_exceeded" in detail)
        if limited:
            self._until[str(provider)] = self.clock() + self.cooldown_seconds
        return limited

    def remaining(self, provider: str) -> float:
        return max(0.0, self._until.get(str(provider), 0.0) - self.clock())

    def available(self, provider: str) -> bool:
        return self.remaining(provider) <= 0.0


def groq_fallback_preflight(messages: list[dict[str, Any]],
                            tool_definitions: list[dict[str, Any]],
                            *, limit_tokens: int | None = None,
                            completion_tokens: int = 256) -> tuple[bool, int, int]:
    """Conservative estimate to skip obviously oversize Groq fallback calls.

    This is NOT an exact tokenizer nor a report of actual billable tokens.
    Provider-specific limits vary. The env override is opt-in and bounded.
    """
    if limit_tokens is None:
        raw = os.getenv("JARVIS_GROQ_FALLBACK_ESTIMATED_TPM_BUDGET", "7000")
        try:
            limit_tokens = int(raw)
        except (ValueError, TypeError):
            limit_tokens = 7000
    limit_tokens = max(1000, min(int(limit_tokens), 1000000))
    payload = json.dumps({"messages": messages, "tools": tool_definitions},
                         ensure_ascii=True, separators=(",", ":"), default=str)
    # Conservative: approx 3 serialized bytes / token. Must fail closed
    # rather than replay an oversized prompt with an identical 413 response.
    estimated = (len(payload.encode("utf-8")) + 2) // 3 + completion_tokens
    return estimated <= limit_tokens, estimated, limit_tokens
