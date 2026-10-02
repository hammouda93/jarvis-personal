from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .execution_managers import ExecutionManagerRegistry
from .kernel_contracts import KernelResponse
from .kernel_service import JarvisKernel


@dataclass(frozen=True)
class DispatchResult:
    request_id: str
    agent_id: str
    capability: str
    response: KernelResponse

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "capability": self.capability,
            "response": self.response.as_dict(),
        }


class KernelDispatcher:
    """Execute queued Kernel requests through registered managers.

    This closes the AIOS-like request path without integrating it into the
    current voice/runtime loop. A manager failure is converted into a failed
    KernelResponse so scheduler capacity is always released.
    """

    def __init__(
        self,
        *,
        kernel: JarvisKernel,
        managers: ExecutionManagerRegistry,
    ):
        self.kernel = kernel
        self.managers = managers

    def run_once(
        self,
        *,
        timeout_s: float | None = None,
        allowed_agents: set[str] | None = None,
    ) -> DispatchResult | None:
        scheduled = self.kernel.next_request(
            timeout_s=timeout_s,
            allowed_agents=allowed_agents,
        )
        if scheduled is None:
            return None

        request = scheduled.request
        try:
            result = self.managers.execute(request)
            success = bool(result.success)
            payload = dict(result.result or {})
            error = str(result.error or "")
        except Exception as exc:
            success = False
            payload = {}
            error = str(exc)[:1200]

        response = self.kernel.complete(
            request.request_id,
            success=success,
            result=payload,
            error=error,
        )
        return DispatchResult(
            request_id=request.request_id,
            agent_id=request.agent_id,
            capability=request.capability,
            response=response,
        )

    def drain(
        self,
        *,
        max_items: int = 100,
        allowed_agents: set[str] | None = None,
    ) -> list[DispatchResult]:
        results: list[DispatchResult] = []
        for _ in range(max(0, int(max_items))):
            item = self.run_once(
                timeout_s=0.0,
                allowed_agents=allowed_agents,
            )
            if item is None:
                break
            results.append(item)
        return results
