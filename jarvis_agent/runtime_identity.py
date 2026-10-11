"""Trusted provider identity attached to one API request, never to stored history."""
from __future__ import annotations

import json
from typing import Any


def annotate_provider_request(
    messages: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
) -> list[dict[str, Any]]:
    """Give a model accurate runtime identity without changing the mission transcript.

    A new shallow copy of the system message is built on each provider attempt.
    Switching providers never rewrites historical messages or tool-call pairs.
    """
    if not messages or messages[0].get("role") != "system":
        return messages
    if not isinstance(messages[0].get("content"), str):
        return messages
    identity = json.dumps(
        {"provider": str(provider)[:60], "model": str(model)[:200]},
        ensure_ascii=False,
    )
    system = dict(messages[0])
    system["content"] += (
        "\n\nTRUSTED RUNTIME IDENTITY FOR THIS REQUEST: " + identity
        + "\nIf asked which model or brain you are running, use this identity; "
        "never invent an OpenAI model name or confuse Jarvis with the LLM supplier."
    )
    return [system, *messages[1:]]
