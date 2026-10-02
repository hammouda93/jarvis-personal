from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .replay_sandbox import ReplayAction


class ReplayTransport(Protocol):
    """Transport abstraction for a future local VM/MCP/LiteCUA backend."""

    transport_id: str

    def call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


@dataclass
class MCPReplaySandboxAdapter:
    """ReplaySandbox-compatible adapter over a generic MCP-like transport.

    No AIOS/LiteCUA code is imported. A future transport can speak to a
    localhost-only VM controller or another sandbox implementation.
    """

    transport: ReplayTransport
    sandbox_id: str = "mcp-replay"

    def reset(self, environment_id: str) -> None:
        result = self.transport.call(
            "reset",
            {"environment_id": str(environment_id)},
        )
        if result.get("success") is False:
            raise RuntimeError(
                str(result.get("error") or "sandbox_reset_failed")
            )

    def start_recording(self, replay_id: str) -> None:
        result = self.transport.call(
            "start_recording",
            {"replay_id": str(replay_id)},
        )
        if result.get("success") is False:
            raise RuntimeError(
                str(result.get("error") or "recording_start_failed")
            )

    def execute(self, action: ReplayAction) -> dict[str, Any]:
        return dict(
            self.transport.call(
                "execute",
                {
                    "action_type": action.action_type,
                    "arguments": dict(action.arguments),
                },
            )
            or {}
        )

    def observe(self) -> dict[str, Any]:
        return dict(self.transport.call("observe", {}) or {})

    def evaluate(
        self,
        *,
        expected_state: dict[str, Any],
        action_results: list[dict[str, Any]],
        final_observation: dict[str, Any],
    ) -> dict[str, Any]:
        return dict(
            self.transport.call(
                "evaluate",
                {
                    "expected_state": dict(expected_state),
                    "action_results": list(action_results),
                    "final_observation": dict(final_observation),
                },
            )
            or {}
        )

    def stop_recording(self, replay_id: str) -> list[str]:
        result = dict(
            self.transport.call(
                "stop_recording",
                {"replay_id": str(replay_id)},
            )
            or {}
        )
        return [
            str(item)
            for item in result.get("artifacts") or []
            if str(item)
        ]
