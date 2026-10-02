from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from .execution_managers import ManagerExecutionResult
from .kernel_contracts import KernelRequest, SyscallKind
from .model_router import LightweightModelRouter, RouteRequest
from .model_telemetry import ModelTelemetryStore


class LLMAdapter(Protocol):
    provider: str
    model: str

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class LLMExecutionAttempt:
    provider: str
    model: str
    success: bool
    latency_ms: float
    error: str = ""


class RoutedLLMExecutionManager:
    """Passive future Kernel LLM manager.

    It is intentionally separate from the current live Cerebras/Groq runtime.
    """

    syscall_kind = SyscallKind.LLM

    def __init__(
        self,
        *,
        router: LightweightModelRouter,
        telemetry: ModelTelemetryStore | None = None,
    ):
        self.router = router
        self.telemetry = telemetry
        self._adapters: dict[tuple[str, str], LLMAdapter] = {}

    def register_adapter(self, adapter: LLMAdapter) -> None:
        self._adapters[(str(adapter.provider), str(adapter.model))] = adapter

    def execute(self, request: KernelRequest) -> ManagerExecutionResult:
        payload = dict(request.payload or {})
        task_class = str(payload.get("task_class") or "general")
        max_attempts = max(1, min(int(payload.get("max_attempts") or 2), 5))
        attempts: list[LLMExecutionAttempt] = []

        for _attempt in range(max_attempts):
            decision = self.router.route(
                RouteRequest(
                    task_class=task_class,
                    required_tags=tuple(
                        str(item)
                        for item in payload.get("required_tags") or ()
                        if str(item)
                    ),
                    estimated_input_tokens=int(
                        payload.get("estimated_input_tokens") or 0
                    ),
                    estimated_output_tokens=int(
                        payload.get("estimated_output_tokens") or 0
                    ),
                    latency_budget_ms=(
                        None
                        if payload.get("latency_budget_ms") is None
                        else float(payload.get("latency_budget_ms"))
                    ),
                    cost_budget=(
                        None
                        if payload.get("cost_budget") is None
                        else float(payload.get("cost_budget"))
                    ),
                    prefer_local=bool(payload.get("prefer_local")),
                )
            )
            if decision is None:
                break

            key = (decision.provider, decision.model)
            adapter = self._adapters.get(key)
            if adapter is None:
                self.router.note_failure(
                    decision.provider,
                    decision.model,
                    cooldown_s=5.0,
                )
                attempts.append(
                    LLMExecutionAttempt(
                        provider=decision.provider,
                        model=decision.model,
                        success=False,
                        latency_ms=0.0,
                        error="adapter_unavailable",
                    )
                )
                continue

            started = time.perf_counter()
            try:
                result = dict(adapter.generate(payload) or {})
            except Exception as exc:
                elapsed = (time.perf_counter() - started) * 1000.0
                error = str(exc)[:1200]
                attempts.append(
                    LLMExecutionAttempt(
                        provider=decision.provider,
                        model=decision.model,
                        success=False,
                        latency_ms=elapsed,
                        error=error,
                    )
                )
                self.router.note_failure(
                    decision.provider,
                    decision.model,
                    cooldown_s=float(
                        payload.get("failure_cooldown_s") or 30.0
                    ),
                )
                if self.telemetry is not None:
                    error_lower = error.lower()
                    kind = (
                        "rate_limit"
                        if "429" in error_lower
                        or "rate limit" in error_lower
                        else "error"
                    )
                    self.telemetry.record(
                        provider=decision.provider,
                        model=decision.model,
                        success=False,
                        latency_s=elapsed / 1000.0,
                        task_class=task_class,
                        error_kind=kind,
                    )
                continue

            elapsed = (time.perf_counter() - started) * 1000.0
            attempts.append(
                LLMExecutionAttempt(
                    provider=decision.provider,
                    model=decision.model,
                    success=True,
                    latency_ms=elapsed,
                )
            )
            self.router.note_success(
                decision.provider,
                decision.model,
            )
            if self.telemetry is not None:
                self.telemetry.record(
                    provider=decision.provider,
                    model=decision.model,
                    success=True,
                    latency_s=elapsed / 1000.0,
                    task_class=task_class,
                    input_tokens=(
                        int(result["input_tokens"])
                        if result.get("input_tokens") is not None
                        else None
                    ),
                    output_tokens=(
                        int(result["output_tokens"])
                        if result.get("output_tokens") is not None
                        else None
                    ),
                    estimated_cost=(
                        float(result["estimated_cost"])
                        if result.get("estimated_cost") is not None
                        else None
                    ),
                )
            return ManagerExecutionResult(
                success=True,
                result={
                    "provider": decision.provider,
                    "model": decision.model,
                    "route_reasons": list(decision.reasons),
                    "output": result,
                    "attempts": [
                        {
                            "provider": item.provider,
                            "model": item.model,
                            "success": item.success,
                            "latency_ms": round(item.latency_ms, 3),
                            "error": item.error,
                        }
                        for item in attempts
                    ],
                },
            )

        return ManagerExecutionResult(
            success=False,
            result={
                "attempts": [
                    {
                        "provider": item.provider,
                        "model": item.model,
                        "success": item.success,
                        "latency_ms": round(item.latency_ms, 3),
                        "error": item.error,
                    }
                    for item in attempts
                ]
            },
            error="no_healthy_model_available",
        )
