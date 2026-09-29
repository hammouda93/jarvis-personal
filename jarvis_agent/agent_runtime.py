from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .config import settings
from .native_tools import AgentActionResult, NATIVE_TOOLS, NativeToolRegistry


LogFn = Callable[[str], None]
PhaseFn = Callable[[str], None]


_SYSTEM_INSTRUCTIONS = """Tu es Jarvis, l'assistant personnel de l'utilisateur sur Windows.

Le français est la langue principale actuelle. Réponds naturellement, brièvement
et comme un vrai assistant, pas comme une documentation technique.

Tu disposes de capacités réelles. Quand l'utilisateur demande une action:
- appelle réellement l'outil adapté;
- n'écris jamais la syntaxe d'un outil comme si c'était une réponse;
- après le résultat d'un outil, observe le résultat puis poursuis si d'autres
  étapes sont nécessaires;
- pour une demande multi-étapes, termine toutes les étapes raisonnables;
- si une information essentielle manque, pose une question courte;
- si un outil échoue, utilise le résultat pour corriger/replanifier si possible;
- n'invente jamais qu'une action a réussi;
- n'invente pas une capacité qui n'existe pas.

Exemples:
- "Ouvre Chrome et cherche les agents IA" => ouvrir Chrome puis chercher.
- "Ouvre VLC Media Player" => utiliser l'outil générique d'ouverture d'application.
- "Ouvre media dans baristas" => ouvrir le dossier media avec baristas comme parent.
- "Non, je voulais dire tickets" => comprendre qu'il s'agit d'une correction
  de la cible précédente grâce au contexte de conversation.
- "YouTube" sans demande claire => demander ce que l'utilisateur veut faire.

Tu peux converser normalement sans outil lorsqu'aucune action réelle n'est demandée.
"""


@dataclass(frozen=True)
class AgentTurnResult:
    text: str
    actions: tuple[AgentActionResult, ...] = ()
    end_session: bool = False
    should_exit: bool = False


class AgentRuntime(Protocol):
    def run(
        self,
        user_text: str,
        *,
        log: LogFn | None = None,
        phase: PhaseFn | None = None,
    ) -> AgentTurnResult:
        ...

    def reset(self) -> None:
        ...


class AgentRuntimeUnavailable(RuntimeError):
    pass


class OllamaToolAgent:
    """Native Ollama tool loop.

    Unlike the old JSON planner, this uses Ollama's actual tool_calls protocol.
    The model receives tool results and can decide its next action repeatedly.
    """

    def __init__(
        self,
        tools: NativeToolRegistry | None = None,
    ) -> None:
        self.tools = tools or NATIVE_TOOLS
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_agent_model
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SYSTEM_INSTRUCTIONS}
        ]

    def reset(self) -> None:
        self._messages = [
            {"role": "system", "content": _SYSTEM_INSTRUCTIONS}
        ]

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req,
                timeout=settings.ai_request_timeout_s,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            raise AgentRuntimeUnavailable(
                f"Ollama a refusé la requête pour {self.model}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AgentRuntimeUnavailable(
                "Ollama n'est pas joignable sur ce PC."
            ) from exc
        except TimeoutError as exc:
            raise AgentRuntimeUnavailable(
                "Le modèle local a mis trop de temps à répondre."
            ) from exc

    def run(
        self,
        user_text: str,
        *,
        log: LogFn | None = None,
        phase: PhaseFn | None = None,
    ) -> AgentTurnResult:
        self._messages.append({"role": "user", "content": user_text})
        actions: list[AgentActionResult] = []
        end_session = False
        should_exit = False

        for round_index in range(1, settings.agent_max_tool_rounds + 1):
            if phase:
                phase("thinking")
            if log:
                log(
                    f"[AGENT] provider=ollama model={self.model} "
                    f"round={round_index}"
                )

            data = self._post(
                {
                    "model": self.model,
                    "messages": self._messages,
                    "tools": self.tools.ollama_tools(),
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.15},
                    "keep_alive": "10m",
                }
            )
            message = data.get("message") or {}
            content = str(message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []

            assistant_item: dict[str, Any] = {
                "role": "assistant",
                "content": content,
            }
            if tool_calls:
                assistant_item["tool_calls"] = tool_calls
            self._messages.append(assistant_item)

            if not tool_calls:
                final_text = content or "Je suis là."
                self._trim_history()
                return AgentTurnResult(
                    text=final_text,
                    actions=tuple(actions),
                    end_session=end_session,
                    should_exit=should_exit,
                )

            for call in tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "").strip()
                raw_args = function.get("arguments") or {}
                if isinstance(raw_args, str):
                    try:
                        arguments = json.loads(raw_args)
                    except json.JSONDecodeError:
                        arguments = {}
                elif isinstance(raw_args, dict):
                    arguments = raw_args
                else:
                    arguments = {}

                if log:
                    log(f"[AGENT_TOOL] call={name} args={arguments}")
                if phase:
                    phase("acting")

                result = self.tools.execute(name, arguments)
                actions.append(result)
                end_session = end_session or result.end_session
                should_exit = should_exit or result.should_exit

                if log:
                    log(
                        f"[AGENT_TOOL] result={name} "
                        f"success={result.success} detail={result.detail!r}"
                    )

                self._messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": result.as_json(),
                    }
                )

        self._trim_history()
        return AgentTurnResult(
            text=(
                "Je n'ai pas terminé correctement cette demande. "
                "Je préfère m'arrêter plutôt que d'exécuter une action incertaine."
            ),
            actions=tuple(actions),
            end_session=end_session,
            should_exit=should_exit,
        )

    def _trim_history(self) -> None:
        maximum = max(8, settings.agent_history_items)
        if len(self._messages) <= maximum + 1:
            return
        recent = self._messages[-maximum:]
        self._messages = [
            {"role": "system", "content": _SYSTEM_INSTRUCTIONS},
            *recent,
        ]


class OpenAIResponsesAgent:
    """OpenAI Responses agent with native function calling and web search."""

    def __init__(
        self,
        tools: NativeToolRegistry | None = None,
    ) -> None:
        self.tools = tools or NATIVE_TOOLS
        self.base_url = settings.openai_base_url.rstrip("/")
        self.model = settings.openai_agent_model
        self.api_key = settings.openai_api_key
        self._previous_response_id: str | None = None

    def reset(self) -> None:
        self._previous_response_id = None

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AgentRuntimeUnavailable(
                "OPENAI_API_KEY n'est pas configurée."
            )

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/responses",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req,
                timeout=settings.ai_request_timeout_s,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AgentRuntimeUnavailable(
                f"OpenAI API error {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AgentRuntimeUnavailable(
                "OpenAI API n'est pas joignable."
            ) from exc
        except TimeoutError as exc:
            raise AgentRuntimeUnavailable(
                "Le modèle OpenAI a mis trop de temps à répondre."
            ) from exc

    def _tool_definitions(self) -> list[dict[str, Any]]:
        tools = self.tools.openai_tools()
        if settings.openai_web_search:
            tools.append({"type": "web_search"})
        return tools

    def run(
        self,
        user_text: str,
        *,
        log: LogFn | None = None,
        phase: PhaseFn | None = None,
    ) -> AgentTurnResult:
        next_input: Any = user_text
        previous = self._previous_response_id
        actions: list[AgentActionResult] = []
        end_session = False
        should_exit = False

        for round_index in range(1, settings.agent_max_tool_rounds + 1):
            if phase:
                phase("thinking")
            if log:
                log(
                    f"[AGENT] provider=openai model={self.model} "
                    f"round={round_index}"
                )

            payload: dict[str, Any] = {
                "model": self.model,
                "instructions": _SYSTEM_INSTRUCTIONS,
                "input": next_input,
                "tools": self._tool_definitions(),
                "reasoning": {
                    "effort": settings.openai_reasoning_effort,
                },
                "store": True,
            }
            if previous:
                payload["previous_response_id"] = previous

            data = self._post(payload)
            response_id = str(data.get("id") or "")
            if not response_id:
                raise AgentRuntimeUnavailable(
                    "La réponse OpenAI ne contient pas d'identifiant."
                )
            previous = response_id
            self._previous_response_id = response_id

            output = data.get("output") or []
            calls = [
                item
                for item in output
                if isinstance(item, dict)
                and item.get("type") == "function_call"
            ]

            if not calls:
                text = self._response_text(output)
                if not text:
                    text = "Je suis là."
                return AgentTurnResult(
                    text=text,
                    actions=tuple(actions),
                    end_session=end_session,
                    should_exit=should_exit,
                )

            next_input = []
            for call in calls:
                name = str(call.get("name") or "").strip()
                call_id = str(call.get("call_id") or "").strip()
                raw_arguments = call.get("arguments") or "{}"
                try:
                    arguments = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else dict(raw_arguments)
                    )
                except (json.JSONDecodeError, TypeError, ValueError):
                    arguments = {}

                if log:
                    log(f"[AGENT_TOOL] call={name} args={arguments}")
                if phase:
                    phase("acting")

                result = self.tools.execute(name, arguments)
                actions.append(result)
                end_session = end_session or result.end_session
                should_exit = should_exit or result.should_exit

                if log:
                    log(
                        f"[AGENT_TOOL] result={name} "
                        f"success={result.success} detail={result.detail!r}"
                    )

                next_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result.as_json(),
                    }
                )

        return AgentTurnResult(
            text=(
                "Je n'ai pas terminé correctement cette demande. "
                "Je préfère m'arrêter plutôt que d'aller plus loin sans certitude."
            ),
            actions=tuple(actions),
            end_session=end_session,
            should_exit=should_exit,
        )

    @staticmethod
    def _response_text(output: list[Any]) -> str:
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if (
                    isinstance(content, dict)
                    and content.get("type") == "output_text"
                ):
                    text = str(content.get("text") or "").strip()
                    if text:
                        parts.append(text)
        return "\n".join(parts).strip()


def build_agent_runtime() -> AgentRuntime:
    provider = settings.agent_provider.lower().strip()
    if provider == "ollama":
        return OllamaToolAgent()
    if provider == "openai":
        return OpenAIResponsesAgent()
    raise AgentRuntimeUnavailable(
        f"Agent provider non pris en charge: {settings.agent_provider}"
    )
