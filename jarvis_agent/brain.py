from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from .config import settings
from .tools import ToolIntent, normalize


@dataclass(frozen=True)
class AgentDecision:
    kind: str
    message: str = ""
    tool: str = ""
    args: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolDecisionValidation:
    intent: ToolIntent | None
    reason: str


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
                "app.open_named",
                "folder.open",
                "folder.open_named",
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

Ton identité côté utilisateur est Jarvis. Ne te présentes pas comme Gemma.
Si l'utilisateur demande quel moteur local tu utilises, tu peux expliquer que
Gemma 3 est actuellement l'un de tes moteurs IA.

Tu peux soit répondre directement, soit demander l'utilisation d'un outil parmi
ceux autorisés. N'invente jamais qu'une action a réussi: si une action est
nécessaire, retourne kind=tool.

Outils autorisés:
- browser.search: args {"query": "..."}
- browser.open_url: args {"url": "https://..."}
- app.open: args {"app": "chrome|spotify|cursor|vscode|snippingtool"}
- app.open_named: args {"query": "nom d'une application installée"}
- folder.open: args {"folder": "downloads"}
- folder.open_named: args {"query": "nom d'un dossier"}
- system.time: args {}
- assistant.sleep: args {}

Pour une question générale, une discussion, une explication, "qui es-tu ?",
"pourquoi...", "comment...", etc., retourne kind=answer.
Pour l'heure actuelle, n'invente jamais l'heure: utilise toujours system.time.
Pour la phase actuelle, le français est la langue principale de Jarvis.
Réponds en français par défaut. Si l'utilisateur demande explicitement de parler
anglais ou arabe, tu peux changer de langue. L'architecture reste prévue pour
français, anglais et arabe, mais n'impose pas de changement automatique de langue.
Ne choisis jamais un outil uniquement parce qu'un mot ressemble au nom d'une
application. Une action sur le PC doit être clairement demandée par l'utilisateur.
Si l'utilisateur demande d'ouvrir une application ou un dossier qui n'est pas
dans les raccourcis connus, utilise app.open_named ou folder.open_named au lieu
d'inventer une autre application. Si le nom exact manque, pose une question de
clarification au lieu de supposer une cible.
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


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


def _explicit_action_requested(user_text: str, tool: str) -> bool:
    text = normalize(user_text)

    if tool == "browser.search":
        return _contains_any(
            text,
            (
                "cherche",
                "recherche",
                "trouve",
                "sur internet",
                "sur google",
                "sur le web",
                "search",
                "find",
                "look up",
                "on the web",
                "online",
                "ابحث",
                "دور",
                "فتش",
            ),
        )

    if tool in {
        "browser.open_url",
        "app.open",
        "app.open_named",
        "folder.open",
        "folder.open_named",
    }:
        return _contains_any(
            text,
            (
                "ouvre",
                "ouvrir",
                "lance",
                "lancer",
                "affiche",
                "va sur",
                "accede",
                "accède",
                "open",
                "launch",
                "show",
                "go to",
                "افتح",
                "شغل",
                "حل",
            ),
        )

    if tool == "assistant.sleep":
        return _contains_any(
            text,
            (
                "dors",
                "veille",
                "c est tout",
                "c'est tout",
                "a plus",
                "au revoir",
                "sleep",
                "standby",
                "go to sleep",
                "نم",
                "استنى",
            ),
        )

    if tool == "system.time":
        return _contains_any(
            text,
            ("heure", "quelleur", "quelheur", "horaire", "time", "الوقت", "الساعة"),
        )

    return False


def _tool_arguments_are_grounded(
    decision: AgentDecision,
    user_text: str,
) -> bool:
    text = normalize(user_text)
    args = dict(decision.args or {})

    if decision.tool == "app.open_named":
        query = normalize(str(args.get("query", "")).strip())
        return bool(query) and query in text

    if decision.tool == "app.open":
        app = str(args.get("app", "")).strip().lower()
        aliases: dict[str, tuple[str, ...]] = {
            "chrome": ("chrome", "google chrome", "creme", "crhome", "كروم"),
            "spotify": ("spotify",),
            "cursor": ("cursor",),
            "vscode": ("vs code", "vscode", "visual studio code"),
            "snippingtool": (
                "capture ecran",
                "capture d ecran",
                "capture d'ecran",
                "snipping",
            ),
        }
        return app in aliases and _contains_any(text, aliases[app])

    if decision.tool == "folder.open_named":
        query = normalize(str(args.get("query", "")).strip())
        return bool(query) and query in text

    if decision.tool == "folder.open":
        folder = str(args.get("folder", "")).strip().lower()
        if folder != "downloads":
            return False
        return _contains_any(
            text,
            (
                "telechargement",
                "telechargements",
                "downloads",
                "download",
                "chargement",
                "chargements",
                "التنزيلات",
                "تنزيلات",
                "التحميلات",
                "تحميلات",
            ),
        )

    if decision.tool == "browser.open_url":
        url = str(args.get("url", "")).strip()
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return False
        if not host:
            return False
        labels = [part for part in host.split(".") if part not in {"www", "com", "net", "org"}]
        return any(normalize(label) in text for label in labels if label)

    if decision.tool == "system.time":
        return _contains_any(
            text,
            ("heure", "quelleur", "quelheur", "horaire", "time", "الوقت", "الساعة"),
        )

    return True


def _candidate_intent(decision: AgentDecision) -> ToolIntent | None:
    args = dict(decision.args or {})

    if decision.tool == "browser.search":
        query = str(args.get("query", "")).strip()
        return ToolIntent("browser.search", {"query": query}) if query else None

    if decision.tool == "browser.open_url":
        url = str(args.get("url", "")).strip()
        if url.startswith(("https://", "http://")):
            return ToolIntent("browser.open_url", {"url": url})
        return None

    if decision.tool == "app.open_named":
        query = str(args.get("query", "")).strip()
        normalized_query = normalize(query)
        blocked = {
            "app",
            "application",
            "programme",
            "program",
            "browser",
            "navigateur",
            "com",
            "exe",
        }
        if (
            not normalized_query
            or len(normalized_query.replace(" ", "")) < 4
            or normalized_query in blocked
        ):
            return None
        return ToolIntent("app.open_named", {"query": query})

    if decision.tool == "app.open":
        app = str(args.get("app", "")).strip().lower()
        allowed = {"chrome", "spotify", "cursor", "vscode", "snippingtool"}
        if app in allowed:
            return ToolIntent("app.open", {"app": app})
        return None

    if decision.tool == "folder.open_named":
        query = str(args.get("query", "")).strip()
        return ToolIntent("folder.open_named", {"query": query}) if query else None

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


def validate_tool_decision(
    decision: AgentDecision,
    *,
    user_text: str = "",
) -> ToolDecisionValidation:
    if decision.kind != "tool":
        return ToolDecisionValidation(None, "not_a_tool")

    candidate = _candidate_intent(decision)
    if candidate is None:
        return ToolDecisionValidation(None, "unsupported_tool")

    grounded = _tool_arguments_are_grounded(decision, user_text)
    explicit = _explicit_action_requested(user_text, decision.tool)

    if not grounded:
        return ToolDecisionValidation(None, "ungrounded_arguments")

    if not explicit:
        return ToolDecisionValidation(candidate, "confirmation_required")

    return ToolDecisionValidation(candidate, "ok")


def decision_to_intent(
    decision: AgentDecision,
    *,
    user_text: str = "",
) -> ToolIntent | None:
    validation = validate_tool_decision(decision, user_text=user_text)
    return validation.intent if validation.reason == "ok" else None


def build_ai_provider() -> AIProvider:
    provider = settings.ai_provider.lower().strip()
    if provider == "ollama":
        return OllamaProvider()
    raise AIProviderUnavailable(
        f"Provider IA non pris en charge: {settings.ai_provider}"
    )
