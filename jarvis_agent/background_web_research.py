from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import settings


@dataclass(frozen=True)
class ResearchResult:
    success: bool
    provider: str
    query: str
    answer: str = ""
    evidence: tuple[dict[str, Any], ...] = ()
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": bool(self.success),
            "provider": self.provider,
            "query": self.query,
            "answer": self.answer,
            "evidence": [dict(item) for item in self.evidence],
            "error": self.error,
            "visible_browser_opened": False,
        }


class BackgroundWebResearch:
    """Server-side / HTTP research that never opens the user's browser.

    Provider order is intentionally explicit and testable. Groq browser_search
    is used when configured because the project already has a Groq credential
    and GPT-OSS supports provider-side search. A future provider can be added
    behind this normalized boundary without changing the agent contract.
    """

    def __init__(self) -> None:
        self.timeout_s = max(3.0, min(float(settings.ai_request_timeout_s), 90.0))

    def research(self, query: str) -> ResearchResult:
        query = str(query or "").strip()
        if len(query) < 2:
            return ResearchResult(
                success=False,
                provider="none",
                query=query,
                error="empty_query",
            )

        if settings.groq_browser_search and settings.groq_api_key:
            # Prefer Groq's current Responses API contract for built-in browser
            # search. Keep Chat Completions as a compatibility fallback.
            result = self._groq_responses_browser_search(query)
            if result.success:
                return result
            fallback = self._groq_browser_search(query)
            if fallback.success:
                return fallback
            return result

        return ResearchResult(
            success=False,
            provider="none",
            query=query,
            error="no_background_research_provider_configured",
        )

    def _groq_responses_browser_search(self, query: str) -> ResearchResult:
        base = str(settings.groq_base_url or "").rstrip("/")
        url = base + "/responses"
        payload = {
            "model": settings.groq_agent_model or "openai/gpt-oss-120b",
            "input": query,
            "tool_choice": "required",
            "tools": [{"type": "browser_search"}],
            "reasoning": {"effort": "low"},
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_s,
            ) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            return ResearchResult(
                success=False,
                provider="groq_responses_browser_search",
                query=query,
                error=f"http_{exc.code}:{detail[:800]}",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ResearchResult(
                success=False,
                provider="groq_responses_browser_search",
                query=query,
                error=f"{type(exc).__name__}:{str(exc)[:500]}",
            )

        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ResearchResult(
                success=False,
                provider="groq_responses_browser_search",
                query=query,
                error="invalid_json_response",
            )

        texts: list[str] = []
        evidence: list[dict[str, Any]] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in {"output_text", "text"}:
                    value = str(part.get("text") or "").strip()
                    if value:
                        texts.append(value)
                for annotation in part.get("annotations") or []:
                    if not isinstance(annotation, dict):
                        continue
                    url_value = (
                        annotation.get("url")
                        or (annotation.get("url_citation") or {}).get("url")
                    )
                    title = (
                        annotation.get("title")
                        or (annotation.get("url_citation") or {}).get("title")
                    )
                    if url_value:
                        evidence.append(
                            {
                                "title": str(title or "")[:300],
                                "url": str(url_value)[:1200],
                            }
                        )
            for result in item.get("search_results") or []:
                if isinstance(result, dict) and result.get("url"):
                    evidence.append(
                        {
                            key: result.get(key)
                            for key in ("title", "url", "snippet", "content", "published")
                            if result.get(key) is not None
                        }
                    )

        content = "\n".join(texts).strip()
        if not content:
            content = str(data.get("output_text") or "").strip()
        if not content:
            return ResearchResult(
                success=False,
                provider="groq_responses_browser_search",
                query=query,
                evidence=tuple(evidence[:12]),
                error="empty_research_answer",
            )

        return ResearchResult(
            success=True,
            provider="groq_responses_browser_search",
            query=query,
            answer=content[:16000],
            evidence=tuple(evidence[:12]),
        )

    def _groq_browser_search(self, query: str) -> ResearchResult:
        base = str(settings.groq_base_url or "").rstrip("/")
        url = base + "/chat/completions"
        payload = {
            "model": settings.groq_agent_model or "openai/gpt-oss-120b",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Research the web to answer the query. Prefer primary "
                        "documentation and authoritative sources. Distinguish "
                        "verified facts from uncertainty. Keep source citations "
                        "or source identifiers present in the provider output."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "temperature": 1,
            "max_completion_tokens": 4096,
            "tool_choice": "required",
            "tools": [{"type": "browser_search"}],
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_s,
            ) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            return ResearchResult(
                success=False,
                provider="groq_browser_search",
                query=query,
                error=f"http_{exc.code}:{detail[:800]}",
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ResearchResult(
                success=False,
                provider="groq_browser_search",
                query=query,
                error=f"{type(exc).__name__}:{str(exc)[:500]}",
            )

        try:
            data = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return ResearchResult(
                success=False,
                provider="groq_browser_search",
                query=query,
                error="invalid_json_response",
            )

        choices = data.get("choices") or []
        if not choices:
            return ResearchResult(
                success=False,
                provider="groq_browser_search",
                query=query,
                error="no_choices",
            )

        message = dict((choices[0] or {}).get("message") or {})
        content = str(message.get("content") or "").strip()

        evidence: list[dict[str, Any]] = []
        executed = message.get("executed_tools") or []
        for item in executed:
            if not isinstance(item, dict):
                continue
            search_results = item.get("search_results") or []
            if isinstance(search_results, dict):
                search_results = [search_results]
            for result in search_results:
                if not isinstance(result, dict):
                    continue
                normalized = {
                    key: result.get(key)
                    for key in (
                        "title",
                        "url",
                        "snippet",
                        "content",
                        "published",
                    )
                    if result.get(key) is not None
                }
                if normalized:
                    evidence.append(normalized)
                if len(evidence) >= 12:
                    break
            if len(evidence) >= 12:
                break

        if not content:
            return ResearchResult(
                success=False,
                provider="groq_browser_search",
                query=query,
                evidence=tuple(evidence),
                error="empty_research_answer",
            )

        return ResearchResult(
            success=True,
            provider="groq_browser_search",
            query=query,
            answer=content[:16000],
            evidence=tuple(evidence),
        )


BACKGROUND_WEB_RESEARCH = BackgroundWebResearch()
