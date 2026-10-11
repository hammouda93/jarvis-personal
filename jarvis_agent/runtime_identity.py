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
    return [system, *_compatible_wire_history(messages[1:], provider=provider)]


def google_tool_call_extra(call: Any) -> dict[str, Any] | None:
    """Preserve the opaque Google function-call signature, without logging it.

    OpenAI-compatible SDKs may put nonstandard fields in model_extra.
    Only the signature is forwarded; unrelated model-generated fields are not.
    """
    extra = getattr(call, "extra_content", None)
    if not isinstance(extra, dict):
        model_extra = getattr(call, "model_extra", None)
        if isinstance(model_extra, dict):
            extra = model_extra.get("extra_content")
    if not isinstance(extra, dict):
        return None
    google = extra.get("google")
    if not isinstance(google, dict):
        return None
    signature = google.get("thought_signature")
    if not isinstance(signature, str) or not signature or len(signature) > 100000:
        return None
    return {"google": {"thought_signature": signature}}


def _compatible_wire_history(
    messages: list[dict[str, Any]], *, provider: str,
) -> list[dict[str, Any]]:
    """Provider-specific wire projection; never edit retained tool evidence."""
    target = str(provider).lower()
    output = []
    for message in messages:
        role = message.get("role")
        if role != "assistant" or not message.get("tool_calls"):
            output.append(message)
            continue
        if target == "gemini" and message.get("content") == "":
            # Some Google OpenAI-compatible endpoints reject empty assistant
            # content alongside a tool call; omitted is protocol-equivalent.
            copy = dict(message)
            copy.pop("content", None)
            output.append(copy)
            continue
        if target in {"cerebras", "cerebras-secondary", "groq", "groq-fallback", "openai", "grok"}:
            calls = list(message["tool_calls"])
            if any(isinstance(call, dict) and "extra_content" in call for call in calls):
                copy = dict(message)
                copy["tool_calls"] = [
                    {key: value for key, value in call.items() if key != "extra_content"}
                    if isinstance(call, dict) else call
                    for call in calls
                ]
                output.append(copy)
                continue
        output.append(message)
    return output
