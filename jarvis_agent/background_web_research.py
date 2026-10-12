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
    queries_executed: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": bool(self.success),
            "provider": self.provider,
            "query": self.query,
            "answer": self.answer,
            "evidence": [dict(item) for item in self.evidence],
            "error": self.error,
            "queries_executed": list(self.queries_executed),
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

    def research(self, query: str, *, subqueries: object = None) -> ResearchResult:
        """One quick search or up to four bounded provider-side searches.

        Subquestions are authored by the existing model as data; no automatic
        browser window, desktop mutation or unsupported source claim is made.
        """
        query = str(query or "").strip()
        if not 2 <= len(query) <= 500:
            return ResearchResult(False, "none", query, error="invalid_query")
        if subqueries is None:
            subqueries = []
        if not isinstance(subqueries, (list, tuple)) or len(subqueries) > 3:
            return ResearchResult(False, "none", query, error="invalid_subqueries")
        questions = [query]
        seen = {query.casefold()}
        for raw in subqueries:
            if not isinstance(raw, str) or not 2 <= len(raw.strip()) <= 240:
                return ResearchResult(False, "none", query, error="invalid_subquery")
            item = raw.strip()
            if item.casefold() not in seen:
                questions.append(item)
                seen.add(item.casefold())
        if not settings.groq_browser_search or not settings.groq_api_key:
            return ResearchResult(False, "none", query,
                                  error="no_background_research_provider_configured")
        if len(questions) == 1:
            result = self._groq_browser_search(query)
            return ResearchResult(
                success=result.success, provider=result.provider, query=query,
                answer=result.answer, evidence=result.evidence, error=result.error,
                queries_executed=(query,),
            )

        answers: list[str] = []
        evidence: list[dict[str, Any]] = []
        urls: set[str] = set()
        executed: list[str] = []
        failures: list[str] = []
        for question in questions:
            result = self._groq_browser_search(question)
            executed.append(question)
            if result.success:
                answers.append("Question: " + question + "\n" + result.answer[:3500])
                for record in result.evidence:
                    link = str(record.get("url") or "")
                    if link and link in urls:
                        continue
                    if link:
                        urls.add(link)
                    evidence.append({**record, "research_query": question})
                    if len(evidence) >= 24:
                        break
            else:
                failures.append(result.error.split(":", 1)[0][:64])
                # Stop rather than spending more on a blocked account.
                if result.error.startswith(("http_402", "http_429", "http_401", "http_403")):
                    break
        if not answers:
            return ResearchResult(False, "groq_browser_search", query,
                                  error=failures[0] if failures else "research_failed",
                                  queries_executed=tuple(executed))
        return ResearchResult(
            success=True, provider="groq_browser_search", query=query,
            answer="\n\n---\n\n".join(answers)[:16000],
            evidence=tuple(evidence[:24]),
            error=("partial_research:" + ",".join(failures)) if failures else "",
            queries_executed=tuple(executed),
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
