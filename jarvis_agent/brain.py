from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol

from .config import settings
from .tools import ToolIntent


@dataclass(frozen=True)
class AgentDecision:
    kind: str
    message: str = ""
    tool: str = ""
    args: dict[str, Any] | None = None


class AIProvider(Protocol):
    def decide(self, user_text: str) -> AgentDecision:
        ...

    def remember_tool_result(
        self,
        user_text: str,
        intent: ToolIntent,
        spoken_result: str,
    ) -> None:
        ...


class AIProviderUnavailable(RuntimeError):
    pass


_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["answer", "tool"],
        },
        "message": {"type": "string"},
        "tool": {
            "type": "string",
            "enum": [
                "",
                "browser.search",
                "browser.open_url",
                "app.open",
                "folder.open",
                "system.time",
                "assistant.sleep",
            ],
        },
        "args": {
            "type": "object",
        },
    },
    "required": ["kind", "message", "tool", "args"],
}


_SYSTEM_PROMPT = """Tu es Jarvis, l'assistant personnel de l'utilisateur.
Tu tournes localement sur son ordinateur Windows et tu dois être naturel, utile,
concis et conversationnel.

Tu peux soit répondre directement, soit demander l'utilisation d'un outil parmi
ceux autorisés. N'invente jamais qu'une action a réussi: si une action est
nécessaire, retourne kind=tool.

Outils autorisés:
- browser.search: args {"query": "..."}
- browser.open_url: args {"url": "https://..."}
- app.open: args {"app": "chrome|spotify|cursor|vscode|snippingtool"}
- folder.open: args {"folder": "downloads"}
- system.time: args {}
- assistant.sleep: args {}

Pour une question générale, une discussion, une explication, "qui es-tu ?",
"pourquoi...", "comment...", etc., retourne kind=answer.
Réponds dans la langue de l'utilisateur quand c'est clair.
Ne choisis jamais un outil uniquement parce qu'un mot ressemble au nom d'une
application. Une action sur le PC doit être clairement demandée par l'utilisateur.
"""


class OllamaProvider:
    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model
        self._history: deque[dict[str, str]] = deque(
            maxlen=max(2, settings.ai_history_messages)
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=settings.ai_request_timeout_s,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise AIProviderUnavailable(
                "Ollama n'est pas joignable sur ce PC."
            ) from exc
        except TimeoutError as exc:
            raise AIProviderUnavailable(
                "Le modèle local a mis trop de temps à répondre."
            ) from exc

    def decide(self, user_text: str) -> AgentDecision:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            *list(self._history),
            {"role": "user", "content": user_text},
        ]
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": _TOOL_SCHEMA,
            "options": {
                "temperature": 0.15,
            },
        }
        data = self._post(payload)
        raw = (
            data.get("message", {}).get("content", "")
            if isinstance(data, dict)
            else ""
        )
        if not raw:
            raise AIProviderUnavailable(
                "Le modèle local n'a renvoyé aucune réponse."
            )

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            decision = AgentDecision(kind="answer", message=raw.strip())
            self._remember_answer(user_text, decision.message)
            return decision

        kind = str(parsed.get("kind", "answer")).strip().lower()
        message = str(parsed.get("message", "")).strip()
        tool = str(parsed.get("tool", "")).strip()
        args = parsed.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        if kind == "tool" and tool:
            return AgentDecision(
                kind="tool",
                message=message,
                tool=tool,
                args=args,
            )

        if not message:
            message = "Je suis là."
        decision = AgentDecision(kind="answer", message=message)
        self._remember_answer(user_text, message)
        return decision

    def _remember_answer(self, user_text: str, answer: str) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": answer})

    def remember_tool_result(
        self,
        user_text: str,
        intent: ToolIntent,
        spoken_result: str,
    ) -> None:
        self._history.append({"role": "user", "content": user_text})
        self._history.append(
            {
                "role": "assistant",
                "content": (
                    f"Action exécutée via {intent.name}. "
                    f"Résultat: {spoken_result}"
                ),
            }
        )


def decision_to_intent(decision: AgentDecision) -> ToolIntent | None:
    if decision.kind != "tool":
        return None

    args = dict(decision.args or {})

    if decision.tool == "browser.search":
        query = str(args.get("query", "")).strip()
        return ToolIntent("browser.search", {"query": query}) if query else None

    if decision.tool == "browser.open_url":
        url = str(args.get("url", "")).strip()
        if url.startswith(("https://", "http://")):
            return ToolIntent("browser.open_url", {"url": url})
        return None

    if decision.tool == "app.open":
        app = str(args.get("app", "")).strip().lower()
        allowed = {"chrome", "spotify", "cursor", "vscode", "snippingtool"}
        if app in allowed:
            return ToolIntent("app.open", {"app": app})
        return None

    if decision.tool == "folder.open":
        folder = str(args.get("folder", "")).strip().lower()
        if folder == "downloads":
            return ToolIntent("folder.open", {"folder": "downloads"})
        return None

    if decision.tool == "system.time":
        return ToolIntent("system.time")

    if decision.tool == "assistant.sleep":
        return ToolIntent("assistant.sleep")

    return None


def build_ai_provider() -> AIProvider:
    provider = settings.ai_provider.lower().strip()
    if provider == "ollama":
        return OllamaProvider()
    raise AIProviderUnavailable(
        f"Provider IA non pris en charge: {settings.ai_provider}"
    )
