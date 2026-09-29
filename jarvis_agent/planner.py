from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .config import settings
from .registry import ToolRegistry


@dataclass(frozen=True)
class PlannedStep:
    tool: str
    args: dict[str, Any]
    reason: str = ""


@dataclass(frozen=True)
class PlanDecision:
    kind: str
    message: str = ""
    question: str = ""
    steps: tuple[PlannedStep, ...] = ()


class Planner(Protocol):
    def plan(
        self,
        user_text: str,
        *,
        context: str = "",
    ) -> PlanDecision:
        ...


class PlannerUnavailable(RuntimeError):
    pass


_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["answer", "clarify", "mission"],
        },
        "message": {"type": "string"},
        "question": {"type": "string"},
        "steps": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string"},
                    "args": {"type": "object"},
                    "reason": {"type": "string"},
                },
                "required": ["tool", "args", "reason"],
            },
        },
    },
    "required": ["kind", "message", "question", "steps"],
}


class OllamaPlanner:
    """Local planner. It reasons about objectives but cannot execute anything."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.base_url = settings.ollama_base_url.rstrip("/")
        self.model = settings.ollama_model

    def _system_prompt(self) -> str:
        catalog = self.registry.catalog_for_prompt()
        return f"""Tu es le Planner de Jarvis, un assistant personnel Windows.

Ton rôle est de comprendre l'objectif réel de l'utilisateur et de décider entre:
- answer: répondre directement, sans outil;
- clarify: poser UNE question courte si une information essentielle manque;
- mission: produire un plan de 1 à 6 étapes avec uniquement les capacités disponibles.

CAPACITÉS DISPONIBLES
{catalog}

RÈGLES
1. Le français est la langue principale actuelle.
2. N'invente jamais une capacité, une application, un chemin ou un résultat.
3. Une demande composée doit devenir plusieurs étapes si nécessaire.
4. Pour une application non listée comme raccourci rapide, utilise app.open_named.
5. Pour un dossier nommé, utilise folder.open_named.
6. Pour "X dans Y", préfère folder.open_named avec query="X" et within="Y".
7. Si le nom de la cible est absent ou réellement ambigu, utilise clarify.
8. Si l'utilisateur corrige un nom après un échec, utilise le contexte pour
   reprendre l'objectif précédent au lieu de repartir de zéro.
9. Une simple mention comme "YouTube" sans verbe d'action doit utiliser clarify.
10. Ne dis jamais qu'une action est terminée: seul le Mission Engine peut le savoir.

EXEMPLES
Utilisateur: "Ouvre Chrome et recherche les agents IA"
=> mission:
   1 app.open {{"app":"chrome"}}
   2 browser.search {{"query":"agents IA"}}

Utilisateur: "Ouvre VLC Media Player"
=> mission:
   1 app.open_named {{"query":"VLC Media Player"}}

Utilisateur: "Ouvre le dossier media dans baristas"
=> mission:
   1 folder.open_named {{"query":"media","within":"baristas"}}

Utilisateur: "Je veux ouvrir un dossier spécifique"
=> clarify: "Quel dossier voulez-vous ouvrir ?"

Utilisateur: "YouTube"
=> clarify: "Voulez-vous que j'ouvre YouTube ?"

Utilisateur: "Explique-moi ce qu'est un agent IA"
=> answer.
"""

    def plan(
        self,
        user_text: str,
        *,
        context: str = "",
    ) -> PlanDecision:
        context_block = context.strip() or "Aucun contexte précédent utile."
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {
                    "role": "user",
                    "content": (
                        "CONTEXTE RÉCENT\n"
                        f"{context_block}\n\n"
                        "NOUVELLE DEMANDE\n"
                        f"{user_text}"
                    ),
                },
            ],
            "stream": False,
            "format": _PLAN_SCHEMA,
            "options": {"temperature": 0.10},
        }

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
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PlannerUnavailable(
                "Le planner local n'est pas joignable."
            ) from exc

        raw = (
            data.get("message", {}).get("content", "")
            if isinstance(data, dict)
            else ""
        )
        if not raw:
            raise PlannerUnavailable("Le planner local n'a rien renvoyé.")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return PlanDecision(kind="answer", message=raw.strip())

        kind = str(parsed.get("kind", "answer")).strip().lower()
        message = str(parsed.get("message", "")).strip()
        question = str(parsed.get("question", "")).strip()

        steps: list[PlannedStep] = []
        raw_steps = parsed.get("steps") or []
        if isinstance(raw_steps, list):
            for item in raw_steps[:6]:
                if not isinstance(item, dict):
                    continue
                tool = str(item.get("tool", "")).strip()
                args = item.get("args") or {}
                reason = str(item.get("reason", "")).strip()
                if tool and isinstance(args, dict):
                    steps.append(
                        PlannedStep(tool=tool, args=args, reason=reason)
                    )

        if kind == "mission" and not steps:
            return PlanDecision(
                kind="clarify",
                question=question or "Pouvez-vous préciser ce que vous voulez que je fasse ?",
            )

        if kind == "clarify":
            return PlanDecision(
                kind="clarify",
                question=question or message or "Pouvez-vous préciser votre demande ?",
            )

        if kind == "mission":
            return PlanDecision(
                kind="mission",
                message=message,
                steps=tuple(steps),
            )

        return PlanDecision(
            kind="answer",
            message=message or "Je suis là.",
        )
