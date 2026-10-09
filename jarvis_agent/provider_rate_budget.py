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
    """In-process circuit breaker for temporary 429 and terminal billing 402.

    A 402 requires a configuration or billing change rather than waiting 60s.
    Disable that credential for the lifetime of this agent process. Other
    providers/credentials remain available. No user data or keys are stored.
    """

    def __init__(self, *, cooldown_seconds: float = 60.0,
                 clock: Callable[[], float] | None = None):
        self.cooldown_seconds = max(1.0, min(float(cooldown_seconds), 3600.0))
        self.clock = clock if clock is not None else time.monotonic
        self._until: dict[str, float] = {}
        self._billing_blocked: set[str] = set()

    def note_failure(self, provider: str, error: Exception) -> bool:
        detail = str(error).lower()
        limited = ("429" in detail or "request_quota_exceeded" in detail
                   or "token_quota_exceeded" in detail)
        # Do not retry an account that cannot currently be billed. A local
        # restart with updated credentials/settings creates a fresh gate.
        billing = ("402" in detail or "payment_required" in detail
                   or "billing_required" in detail)
        if billing:
            self._billing_blocked.add(str(provider))
        elif limited:
            self._until[str(provider)] = self.clock() + self.cooldown_seconds
        return limited or billing

    def remaining(self, provider: str) -> float:
        return max(0.0, self._until.get(str(provider), 0.0) - self.clock())

    def billing_blocked(self, provider: str) -> bool:
        return str(provider) in self._billing_blocked

    def available(self, provider: str) -> bool:
        return not self.billing_blocked(provider) and self.remaining(provider) <= 0.0


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
