from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from .model_telemetry import ModelTelemetryStore


@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    model: str
    task_tags: tuple[str, ...] = ()
    local: bool = False
    estimated_input_cost_per_million: float | None = None
    estimated_output_cost_per_million: float | None = None
    max_context_tokens: int | None = None


@dataclass(frozen=True)
class RouteRequest:
    task_class: str
    required_tags: tuple[str, ...] = ()
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    latency_budget_ms: float | None = None
    cost_budget: float | None = None
    prefer_local: bool = False


@dataclass(frozen=True)
class RouteDecision:
    provider: str
    model: str
    score: float
    reasons: tuple[str, ...] = ()


@dataclass
class _CircuitState:
    blocked_until: float = 0.0
    recent_failures: int = 0


class LightweightModelRouter:
    """Telemetry-aware router inspired by AIOS SmartRouting.

    This class is deliberately NOT connected to the live Jarvis model path.
    """

    def __init__(
        self,
        candidates: Iterable[ModelCandidate],
        telemetry: ModelTelemetryStore | None = None,
    ):
        self.candidates = list(candidates)
        self.telemetry = telemetry
        self._circuits: dict[tuple[str, str], _CircuitState] = {}

    def note_failure(
        self,
        provider: str,
        model: str,
        *,
        cooldown_s: float = 30.0,
    ) -> None:
        key = (str(provider), str(model))
        state = self._circuits.setdefault(key, _CircuitState())
        state.recent_failures += 1
        state.blocked_until = max(
            state.blocked_until,
            time.monotonic() + max(0.0, float(cooldown_s)),
        )

    def note_success(self, provider: str, model: str) -> None:
        key = (str(provider), str(model))
        state = self._circuits.setdefault(key, _CircuitState())
        state.recent_failures = max(0, state.recent_failures - 1)
        if state.recent_failures == 0:
            state.blocked_until = 0.0

    def _estimated_cost(
        self,
        candidate: ModelCandidate,
        request: RouteRequest,
    ) -> float | None:
        if (
            candidate.estimated_input_cost_per_million is None
            or candidate.estimated_output_cost_per_million is None
        ):
            return None
        return (
            max(0, request.estimated_input_tokens)
            / 1_000_000.0
            * candidate.estimated_input_cost_per_million
            + max(0, request.estimated_output_tokens)
            / 1_000_000.0
            * candidate.estimated_output_cost_per_million
        )

    def route(self, request: RouteRequest) -> RouteDecision | None:
        summaries = {}
        if self.telemetry is not None:
            for item in self.telemetry.summary(
                task_class=request.task_class,
                since_hours=24.0,
            ):
                summaries[(item["provider"], item["model"])] = item

        now = time.monotonic()
        ranked: list[RouteDecision] = []
        wanted_tags = set(request.required_tags)

        for candidate in self.candidates:
            key = (candidate.provider, candidate.model)
            circuit = self._circuits.get(key)
            if circuit is not None and circuit.blocked_until > now:
                continue
            if wanted_tags and not wanted_tags.issubset(
                set(candidate.task_tags)
            ):
                continue
            if (
                candidate.max_context_tokens is not None
                and request.estimated_input_tokens
                > candidate.max_context_tokens
            ):
                continue

            estimated_cost = self._estimated_cost(candidate, request)
            if (
                request.cost_budget is not None
                and estimated_cost is not None
                and estimated_cost > request.cost_budget
            ):
                continue

            score = 1.0
            reasons: list[str] = ["eligible"]

            if request.prefer_local and candidate.local:
                score += 0.35
                reasons.append("local_preference")
            elif request.prefer_local and not candidate.local:
                score -= 0.15

            telemetry = summaries.get(key)
            if telemetry is not None:
                success_rate = float(telemetry.get("success_rate") or 0.0)
                score += 0.65 * success_rate
                reasons.append(
                    f"success_rate={success_rate:.2f}"
                )
                avg_latency = telemetry.get("avg_latency_ms")
                if avg_latency is not None:
                    avg_latency = float(avg_latency)
                    if (
                        request.latency_budget_ms is not None
                        and avg_latency <= request.latency_budget_ms
                    ):
                        score += 0.20
                        reasons.append("within_latency_budget")
                    elif (
                        request.latency_budget_ms is not None
                        and avg_latency > request.latency_budget_ms
                    ):
                        score -= 0.25
                        reasons.append("over_latency_budget")
                rate_limits = int(telemetry.get("rate_limits") or 0)
                if rate_limits:
                    score -= min(0.35, rate_limits * 0.08)
                    reasons.append(f"rate_limits={rate_limits}")

            if estimated_cost is not None:
                score -= min(0.25, estimated_cost * 10.0)
                reasons.append(f"estimated_cost={estimated_cost:.6f}")

            ranked.append(
                RouteDecision(
                    provider=candidate.provider,
                    model=candidate.model,
                    score=round(score, 6),
                    reasons=tuple(reasons),
                )
            )

        if not ranked:
            return None
        ranked.sort(
            key=lambda item: (-item.score, item.provider, item.model)
        )
        return ranked[0]
