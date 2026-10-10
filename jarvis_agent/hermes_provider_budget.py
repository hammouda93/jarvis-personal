"""Opt-in logical Cerebras round budgets, without changing retry/fallback logic."""
from __future__ import annotations

import os

from .agent_runtime import AgentRuntimeUnavailable, CerebrasResponsesAgent, settings
from .hermes_reliability import classify_api_failure


class HermesBudgetedCerebrasAgent(CerebrasResponsesAgent):
    """Preserve Cerebras/Groq fallback. Bound *logical* _chat rounds per turn.

    Each _chat may invoke primary, secondary and Groq fallback: this class
    does not claim to measure every actual HTTP request. It does not sleep,
    retry side effects or change provider credentials.
    """

    def __init__(self, tools=None):
        super().__init__(tools)
        configured = os.getenv("JARVIS_HERMES_MODEL_ROUND_BUDGET", "").strip()
        maximum = max(1, int(getattr(settings, "agent_max_tool_rounds", 8)))
        if configured:
            try:
                maximum = max(1, min(100, int(configured)))
            except ValueError:
                pass
        self.reliability_round_budget = maximum
        self.reliability_rounds_used = 0
        self.reliability_last_failure_category = None
        self.reliability_last_usage = None
        self.reliability_last_effective_provider = ""

    def run(self, user_text, *, log=None, phase=None):
        self.reliability_rounds_used = 0
        self.reliability_last_failure_category = None
        self.reliability_last_usage = None
        self.reliability_last_effective_provider = ""
        return super().run(user_text, log=log, phase=phase)

    def _chat_via_groq_fallback(self, *, tool_choice, ms_football_only, msf_tool_names):
        # Only record Groq after its actual API response succeeds.
        response = super()._chat_via_groq_fallback(
            tool_choice=tool_choice,
            ms_football_only=ms_football_only,
            msf_tool_names=msf_tool_names,
        )
        self.reliability_last_effective_provider = "groq"
        return response

    def _chat(self, *, tool_choice="auto", ms_football_only=False, msf_tool_names=None):
        if self.reliability_rounds_used >= self.reliability_round_budget:
            self.reliability_last_failure_category = "logical_round_budget"
            raise AgentRuntimeUnavailable(
                "Budget des tours Cerebras atteint : arrêt contrôlé sans nouvelle requête."
            )
        self.reliability_rounds_used += 1
        self.reliability_last_effective_provider = ""
        try:
            response = super()._chat(
                tool_choice=tool_choice,
                ms_football_only=ms_football_only,
                msf_tool_names=msf_tool_names,
            )
            self.reliability_last_effective_provider = self.last_effective_provider
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.reliability_last_usage = {
                    "input_tokens": getattr(usage, "prompt_tokens", None),
                    "output_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                }
            return response
        except Exception as exc:
            self.reliability_last_failure_category = classify_api_failure(exc)
            raise
