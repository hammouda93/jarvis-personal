from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .config import settings
from .connectors import CONNECTORS
from .native_tools import AgentActionResult, NATIVE_TOOLS, NativeToolRegistry


LogFn = Callable[[str], None]
PhaseFn = Callable[[str], None]


_SYSTEM_INSTRUCTIONS = """Tu es Jarvis, l'assistant personnel de l'utilisateur sur Windows.

/no_think


Le français est la langue principale actuelle. Réponds naturellement, brièvement
et comme un vrai assistant, pas comme une documentation technique. Comme la
réponse sera souvent lue à voix haute, vise 1 à 3 phrases utiles. Ne récite pas
de longues listes ou des données techniques brutes sauf si l'utilisateur les
demande explicitement.

Tu disposes de capacités réelles. Quand l'utilisateur demande une action:
- appelle réellement l'outil adapté;
- n'écris jamais la syntaxe d'un outil comme si c'était une réponse;
- après le résultat d'un outil, observe le résultat puis poursuis si d'autres
  étapes sont nécessaires;
- pour une demande multi-étapes, termine toutes les étapes raisonnables;
- si une information essentielle manque, pose une question courte;
- si un outil échoue, utilise le résultat pour corriger/replanifier si possible;
- n'invente jamais qu'une action a réussi;
- n'invente pas une capacité qui n'existe pas;
- n'enregistre rien dans la mémoire personnelle sauf si l'utilisateur demande
  explicitement de retenir/mémoriser une information;
- si l'utilisateur demande ce que Jarvis se rappelle d'une information passée,
  utilise recall_information au lieu d'inventer un souvenir;
- pour agir dans une application déjà ouverte, utilise d'abord list_windows ou
  inspect_active_window afin d'observer l'interface réelle;
- ne devine jamais le nom d'un bouton ou d'un menu si inspect_active_window peut
  te le montrer;
- utilise activate_window pour mettre une application au premier plan;
- inspect_active_window renvoie des refs courtes e1, e2...; utilise ces refs
  pour les contrôles sans libellé ou ambigus au lieu d'inventer un nom;
- si l'utilisateur parle d'un navigateur ou d'une application précise, passe
  son titre à inspect_active_window quand le nom est connu;
- utilise click_ui_element seulement sur un élément que tu as identifié dans
  l'interface, puis observe à nouveau si l'action doit continuer;
- utilise write_ui_element uniquement sur un champ réellement observé; utilise
  ref si le champ n'a pas de nom. Cet outil saisit le texte sans valider;
- press_key est réservé à la navigation simple, jamais à des raccourcis
  destructifs ou à l'exécution de commandes arbitraires;
- après click_ui_element, press_key ou close_window, n'affirme jamais que
  l'interface a changé comme prévu sans l'avoir vérifié avec
  inspect_active_window ou list_windows lorsque le résultat final compte;
- si l'utilisateur a demandé plusieurs étapes dans une seule phrase, exécute
  toutes les étapes explicitement demandées avant de répondre. Ne demande pas
  "voulez-vous que je..." pour une étape déjà demandée;
- si la cible demandée est un site ou service web, utilise open_url directement
  lorsque son URL est connue; ouvrir seulement Chrome n'accomplit pas la demande;
- n'annonce jamais "je vais chercher/ouvrir/faire" sans appeler l'outil dans le
  même tour.

Exemples:
- "Ouvre Chrome et cherche les agents IA" => ouvrir Chrome puis chercher.
- "Ouvre YouTube" => utiliser open_url vers YouTube; ne pas ouvrir Chrome seul.
- "Ouvre VLC Media Player" => utiliser l'outil générique d'ouverture d'application.
- "Ouvre media dans baristas" => ouvrir le dossier media avec baristas comme parent.
- "Non, je voulais dire tickets" => comprendre qu'il s'agit d'une correction
  de la cible précédente grâce au contexte de conversation.
- "YouTube" sans demande claire => demander ce que l'utilisateur veut faire.
- "Retiens que j'aime le golf" => utiliser remember_information.
- "Tu te rappelles quel sport j'aime ?" => utiliser recall_information.
- "Qu'est-ce qui est ouvert ?" => utiliser list_windows.
- "Dans cette fenêtre, clique sur Paramètres" => inspect_active_window puis
  click_ui_element si Paramètres est réellement visible.
- "Écris bonjour dans le champ message" => inspect_active_window puis
  write_ui_element sur le champ observé; n'appuie sur Entrée que si l'utilisateur
  a aussi demandé de valider/envoyer.

Tu peux converser normalement sans outil lorsqu'aucune action réelle n'est demandée.
Ne révèle jamais de raisonnement interne, de chaîne de pensée, de balises <think>
ou de notes techniques destinées au modèle. Seule la réponse finale utile doit
être visible ou prononcée.
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

    def warm_up(self, *, log: LogFn | None = None) -> None:
        ...


class AgentRuntimeUnavailable(RuntimeError):
    pass


_THINK_BLOCK_RE = re.compile(
    r"<think>.*?</think>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _looks_like_internal_reasoning(text: str) -> bool:
    lower = (text or "").lower()
    markers = (
        "the user said",
        "i need to",
        "let me",
        "the instructions",
        "no tool calls",
        "the user is",
        "i should respond",
        "the correct response",
        "so the answer",
        "first, check",
        "wait,",
    )
    hits = sum(1 for marker in markers if marker in lower)
    return hits >= 2


def _looks_like_action_promise(text: str) -> bool:
    lower = (text or "").lower()

    # Catch future-action language even when the model inserts adverbs such as
    # "maintenant", "ensuite" or "tout de suite" between the auxiliary and verb.
    patterns = (
        r"\bje vais\b.{0,40}\b(ouvrir|chercher|rechercher|faire|cliquer|écrire|ecrire|fermer)\b",
        r"\bi(?:'|’)ll\b.{0,40}\b(open|search|do|click|write|close)\b",
        r"\bi will\b.{0,40}\b(open|search|do|click|write|close)\b",
    )
    if any(re.search(pattern, lower, flags=re.DOTALL) for pattern in patterns):
        return True

    markers = (
        "patientez un instant",
        "patiente un instant",
        "let me do that",
        "right away",
    )
    return any(marker in lower for marker in markers)


def _looks_like_unnecessary_followup(text: str) -> bool:
    lower = (text or "").lower()
    markers = (
        "voulez-vous que je",
        "veux-tu que je",
        "souhaitez-vous que je",
        "would you like me to",
        "do you want me to",
        "shall i",
    )
    return any(marker in lower for marker in markers)


def _looks_mostly_english(text: str) -> bool:
    words = re.findall(r"[a-zA-ZÀ-ÿ']+", (text or "").lower())
    if len(words) < 4:
        return False
    english = {
        "i", "you", "the", "for", "to", "and", "would", "like", "search",
        "open", "have", "now", "me", "do", "that", "details", "with",
    }
    french = {
        "je", "vous", "tu", "le", "la", "les", "pour", "et", "recherche",
        "ouvrir", "ouvert", "avec", "que", "des", "une", "un", "dans",
    }
    english_hits = sum(1 for word in words if word in english)
    french_hits = sum(1 for word in words if word in french)
    return english_hits >= 3 and english_hits > french_hits + 1


def _visible_text(value: str) -> str:
    """Keep only user-facing model output, never internal reasoning text."""
    text = str(value or "").strip()
    if not text:
        return ""

    # Some Qwen templates may return reasoning in content instead of the
    # dedicated thinking field. If a closing tag exists, the useful answer is
    # normally what follows it.
    if "</think>" in text.lower():
        lower = text.lower()
        index = lower.rfind("</think>")
        text = text[index + len("</think>"):]

    text = _THINK_BLOCK_RE.sub("", text)

    # Defensive handling for an unterminated block.
    lower = text.lower()
    if "<think>" in lower:
        text = text[: lower.find("<think>")]

    text = text.strip()
    if _looks_like_internal_reasoning(text):
        return ""
    return text


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

    def warm_up(self, *, log: LogFn | None = None) -> None:
        # Prime the same system prompt + tool schema used by real turns. This
        # intentionally moves the expensive first prompt evaluation to boot,
        # before the user wakes Jarvis.
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": "Réponds seulement OK.\n/no_think"},
            ],
            "tools": self.tools.ollama_tools(),
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": settings.ollama_agent_num_ctx,
                "num_predict": 8,
            },
            "keep_alive": settings.ollama_agent_keep_alive,
        }
        started = time.perf_counter()
        try:
            self._post(payload)
            if log:
                log(
                    f"[PERF] ollama_warmup seconds="
                    f"{time.perf_counter() - started:.2f}"
                )
        except Exception as exc:
            if log:
                log(
                    f"[AGENT] warmup skipped: "
                    f"{type(exc).__name__}: {exc}"
                )

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
                timeout=settings.ollama_agent_timeout_s,
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
        except (TimeoutError, socket.timeout) as exc:
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
        self._messages.append(
            {
                "role": "user",
                "content": f"{user_text}\n/no_think",
            }
        )
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

            started = time.perf_counter()
            data = self._post(
                {
                    "model": self.model,
                    "messages": self._messages,
                    "tools": self.tools.ollama_tools(),
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": 0.10,
                        "num_ctx": settings.ollama_agent_num_ctx,
                        "num_predict": settings.ollama_agent_num_predict,
                    },
                    "keep_alive": settings.ollama_agent_keep_alive,
                }
            )
            elapsed = time.perf_counter() - started
            if log:
                log(f"[PERF] ollama_round={round_index} seconds={elapsed:.2f}")
            message = data.get("message") or {}
            content = _visible_text(message.get("content") or "")
            tool_calls = message.get("tool_calls") or []

            assistant_item: dict[str, Any] = {
                "role": "assistant",
                "content": content,
            }
            if tool_calls:
                assistant_item["tool_calls"] = tool_calls
            self._messages.append(assistant_item)

            if not tool_calls:
                if not content:
                    if log:
                        log(
                            "[AGENT] model output hidden because it looked like "
                            "internal reasoning"
                        )
                    content = (
                        "Je suis là. Reformulez votre demande si vous vouliez "
                        "que j'effectue une action."
                    )

                repair_reason = ""
                if _looks_mostly_english(content) and not _looks_mostly_english(user_text):
                    repair_reason = (
                        "Réponds uniquement en français. Ne change pas de langue."
                    )
                elif actions and _looks_like_unnecessary_followup(content):
                    repair_reason = (
                        "La demande initiale était déjà explicite. Ne demande pas "
                        "une nouvelle autorisation pour une étape déjà demandée. "
                        "S'il reste une étape demandée et qu'un outil existe, "
                        "appelle cet outil maintenant."
                    )
                elif _looks_like_action_promise(content):
                    repair_reason = (
                        "Tu annonces encore une action à faire. N'annonce jamais "
                        "une étape future: si cette étape fait partie de la demande "
                        "initiale et qu'un outil existe, appelle-le maintenant. "
                        "Sinon explique brièvement pourquoi elle ne peut pas être "
                        "réalisée avec les outils disponibles."
                    )

                if repair_reason and round_index < settings.agent_max_tool_rounds:
                    if log:
                        log(f"[AGENT] repair={repair_reason}")
                    self._messages.append(
                        {
                            "role": "user",
                            "content": repair_reason + "\n/no_think",
                        }
                    )
                    continue

                final_text = content
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

                tool_started = time.perf_counter()
                result = self.tools.execute(name, arguments)
                if log:
                    log(
                        f"[PERF] tool={name} "
                        f"seconds={time.perf_counter() - tool_started:.3f}"
                    )
                actions.append(result)
                end_session = end_session or result.end_session
                should_exit = should_exit or result.should_exit

                if log:
                    detail_for_log = result.detail
                    if len(detail_for_log) > 900:
                        detail_for_log = detail_for_log[:900] + "…"
                    log(
                        f"[AGENT_TOOL] result={name} "
                        f"success={result.success} detail={detail_for_log!r}"
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
        self.provider_name = "openai"
        self.reasoning_effort = settings.openai_reasoning_effort
        self.web_search_tool_type = "web_search"
        self._previous_response_id: str | None = None
        self._pending_mcp_approval: dict[str, Any] | None = None
        self._pending_mcp_response_id: str | None = None

    def reset(self) -> None:
        self._previous_response_id = None
        self._pending_mcp_approval = None
        self._pending_mcp_response_id = None

    def warm_up(self, *, log: LogFn | None = None) -> None:
        return

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AgentRuntimeUnavailable(
                f"Clé API manquante pour le provider {self.provider_name}."
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
                f"{self.provider_name} API error {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AgentRuntimeUnavailable(
                f"{self.provider_name} API n'est pas joignable."
            ) from exc
        except TimeoutError as exc:
            raise AgentRuntimeUnavailable(
                f"Le modèle {self.provider_name} a mis trop de temps à répondre."
            ) from exc

    def _tool_definitions(self) -> list[dict[str, Any]]:
        tools = self.tools.openai_tools()
        web_enabled = (
            settings.openai_web_search
            if self.provider_name == "openai"
            else settings.groq_browser_search
        )
        if web_enabled:
            tools.append({"type": self.web_search_tool_type})
        tools.extend(CONNECTORS.openai_tools())
        return tools

    def run(
        self,
        user_text: str,
        *,
        log: LogFn | None = None,
        phase: PhaseFn | None = None,
    ) -> AgentTurnResult:
        previous = self._previous_response_id

        if self._pending_mcp_approval is not None:
            normalized = user_text.strip().lower().strip(" .!?")
            yes = normalized in {
                "oui", "yes", "ok", "okay", "d'accord", "daccord",
                "vas-y", "vas y", "autorise", "autoriser", "approve",
            }
            no = normalized in {
                "non", "no", "annule", "annuler", "refuse", "refuser",
                "cancel", "deny",
            }
            if not yes and not no:
                pending = self._pending_mcp_approval
                tool_name = str(pending.get("name") or "cet outil")
                server = str(pending.get("server_label") or "ce connecteur")
                return AgentTurnResult(
                    text=(
                        f"J'attends votre autorisation pour utiliser "
                        f"{tool_name} via {server}. Dites oui ou non."
                    )
                )

            approval_id = str(
                self._pending_mcp_approval.get("id") or ""
            )
            previous = self._pending_mcp_response_id or previous
            next_input: Any = [
                {
                    "type": "mcp_approval_response",
                    "approve": yes,
                    "approval_request_id": approval_id,
                }
            ]
            self._pending_mcp_approval = None
            self._pending_mcp_response_id = None
        else:
            next_input = user_text
        actions: list[AgentActionResult] = []
        end_session = False
        should_exit = False

        for round_index in range(1, settings.agent_max_tool_rounds + 1):
            if phase:
                phase("thinking")
            if log:
                log(
                    f"[AGENT] provider={self.provider_name} model={self.model} "
                    f"round={round_index}"
                )

            payload: dict[str, Any] = {
                "model": self.model,
                "instructions": _SYSTEM_INSTRUCTIONS,
                "input": next_input,
                "tools": self._tool_definitions(),
                "reasoning": {
                    "effort": self.reasoning_effort,
                },
                "store": True,
            }
            if previous:
                payload["previous_response_id"] = previous

            started = time.perf_counter()
            data = self._post(payload)
            if log:
                log(
                    f"[PERF] openai_round={round_index} "
                    f"seconds={time.perf_counter() - started:.2f}"
                )
            response_id = str(data.get("id") or "")
            if not response_id:
                raise AgentRuntimeUnavailable(
                    "La réponse OpenAI ne contient pas d'identifiant."
                )
            previous = response_id
            self._previous_response_id = response_id

            output = data.get("output") or []

            approval_requests = [
                item
                for item in output
                if isinstance(item, dict)
                and item.get("type") == "mcp_approval_request"
            ]
            if approval_requests:
                pending = approval_requests[0]
                self._pending_mcp_approval = pending
                self._pending_mcp_response_id = response_id
                tool_name = str(pending.get("name") or "un outil")
                server = str(pending.get("server_label") or "un connecteur")
                if log:
                    log(
                        f"[MCP] approval_required server={server} "
                        f"tool={tool_name}"
                    )
                return AgentTurnResult(
                    text=(
                        f"J'ai besoin de votre autorisation pour utiliser "
                        f"{tool_name} via {server}. Dites oui pour autoriser "
                        f"ou non pour refuser."
                    ),
                    actions=tuple(actions),
                    end_session=end_session,
                    should_exit=should_exit,
                )

            mcp_calls = [
                item
                for item in output
                if isinstance(item, dict)
                and item.get("type") == "mcp_call"
            ]
            for mcp_call in mcp_calls:
                server = str(mcp_call.get("server_label") or "mcp")
                name = str(mcp_call.get("name") or "tool")
                error = mcp_call.get("error")
                output_value = str(mcp_call.get("output") or "")
                success = not bool(error)
                actions.append(
                    AgentActionResult(
                        name=f"mcp:{server}:{name}",
                        success=success,
                        message=(
                            "Action connectée exécutée."
                            if success
                            else "Le connecteur a signalé une erreur."
                        ),
                        detail=(output_value[:1200] if success else str(error)),
                    )
                )
                if log:
                    log(
                        f"[MCP] call server={server} tool={name} "
                        f"success={success}"
                    )

            calls = [
                item
                for item in output
                if isinstance(item, dict)
                and item.get("type") == "function_call"
            ]

            if not calls:
                text = self._response_text(output)
                if not text:
                    if mcp_calls:
                        failed = any(
                            bool(item.get("error"))
                            for item in mcp_calls
                        )
                        text = (
                            "Le connecteur a rencontré une erreur."
                            if failed
                            else "C'est fait."
                        )
                    else:
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


class GroqResponsesAgent(OpenAIResponsesAgent):
    """Groq Responses API agent using openai/gpt-oss-120b by default.

    Groq exposes an OpenAI-compatible Responses API, so Jarvis can reuse the
    same function-calling and MCP orchestration while switching base URL,
    credentials, model and built-in browser-search tool identifier.
    """

    def __init__(
        self,
        tools: NativeToolRegistry | None = None,
    ) -> None:
        super().__init__(tools)
        self.base_url = settings.groq_base_url.rstrip("/")
        self.model = settings.groq_agent_model
        self.api_key = settings.groq_api_key
        self.provider_name = "groq"
        self.reasoning_effort = settings.groq_reasoning_effort
        self.web_search_tool_type = "browser_search"


def build_agent_runtime() -> AgentRuntime:
    provider = settings.agent_provider.lower().strip()
    if provider == "ollama":
        return OllamaToolAgent()
    if provider == "openai":
        return OpenAIResponsesAgent()
    if provider == "groq":
        return GroqResponsesAgent()
    raise AgentRuntimeUnavailable(
        f"Agent provider non pris en charge: {settings.agent_provider}"
    )
