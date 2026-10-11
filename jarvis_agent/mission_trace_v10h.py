"""Read-only mission trace for the ordinary conversational agent.

An event projection, not a second planner, an executor, an authorization gate
or a durable mission checkpoint. No API calls. All observed action outcomes
come from runtime tool results; a successful tool is not goal completion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import unicodedata
import uuid
from typing import Callable, Iterable

from .instruction_clauses import positive_instruction_clauses


def _normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def _safe(value: str, *, limit: int = 500) -> str:
    value = str(value or "").strip()
    if len(value) > limit:
        value = value[:limit] + "…"
    return json.dumps(value, ensure_ascii=False)


# A provisional outline of the user's *positive instructions*. Do not run
# these as commands. An actual semantic mission plan needs its own evidence
# and approval; quoted data or negative/conditional clauses are never plans.
_ACTION = (
    r"ouvre|ouvres|ouvrez|ouvrir|open|lance|lancer|"
    r"recherche|chercher|cherche|search|trouve|trouver|"
    r"verifie|verifier|controle|controler|inspecte|inspecter|"
    r"clique|cliquer|click|ferme|fermer|close|"
    r"ecris|ecrire|saisis|saisir|write|read|lis|lire|"
    r"retourne|reviens|back|rappelle|retrouve|"
    r"envoie|envoyer|send|installe|installer"
)
_SPLIT_INSTRUCTION = re.compile(
    r"\s*(?:,|\bet\b)\s*(?=(?:" + _ACTION + r")\b)",
    re.I,
)
_RESUME = frozenset({"oui", "ok", "continue", "poursuis", "vas y", "vas-y", "go", "d'accord"})


def requested_steps(user_text: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in positive_instruction_clauses(user_text):
        for clause in _SPLIT_INSTRUCTION.split(item):
            cleaned = clause.strip(" ,")
            if cleaned:
                result.append(cleaned)
    # No claim that a local keyword split equals the semantic plan.
    return tuple(result[:12])


def _tool_step_hint(tool: str, steps: tuple[str, ...]) -> int | None:
    """Only direct broad-capability alignment, never inferred completion."""
    terms = {
        "open_url": r"\b(?:ouvre|ouvrir|open|lance)\b",
        "browser_navigate": r"\b(?:ouvre|ouvrir|open|navigue|va)\b",
        "open_application": r"\b(?:ouvre|ouvrir|open|lance)\b",
        "open_file": r"\b(?:ouvre|ouvrir|open|lance)\b",
        "browser_write": r"\b(?:recherche|cherche|search|ecris|saisis|write)\b",
        "browser_find": r"\b(?:recherche|cherche|search|trouve|identifie|inspecte)\b",
        "semantic_memory_search": r"\b(?:memoire|souvenir|retiens|memorise|retrouve|rappelle)\b",
        "semantic_memory_inspect": r"\b(?:memoire|souvenir|memorise)\b",
        "close_window": r"\b(?:ferme|fermer|close)\b",
        "browser_close_tab": r"\b(?:ferme|fermer|close)\b",
        "browser_back": r"\b(?:retour|retourne|reviens|back)\b",
        "list_windows": r"\b(?:verifie|verifier|liste|fenetre|window|controle)\b",
        "inspect_active_window": r"\b(?:verifie|verifier|inspecte|fenetre|window|controle)\b",
    }
    pattern = terms.get(tool)
    if not pattern:
        return None
    matches = [i + 1 for i, step in enumerate(steps)
               if re.search(pattern, _normalized(step))]
    return matches[0] if len(matches) == 1 else None


def _evidence_level(result) -> str:
    if not bool(getattr(result, "success", False)):
        return "FAILED"
    detail = getattr(result, "detail", "")
    try:
        data = json.loads(detail) if isinstance(detail, str) else detail
    except (ValueError, TypeError):
        data = {}
    if isinstance(data, dict):
        if data.get("outcome_unknown"):
            return "OUTCOME_UNKNOWN"
        if data.get("verified") is True:
            return "TOOL_EFFECT_VERIFIED"
        if data.get("dispatched") is True:
            return "DISPATCHED_UNVERIFIED"
    return "TOOL_RETURNED_SUCCESS"


@dataclass
class ConversationMissionTrace:
    user_request: str
    emit: Callable[[str], None]
    mission_id: str = ""
    previous: "ConversationMissionTrace | None" = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    steps: tuple[str, ...] = field(init=False)
    tool_calls: list[str] = field(default_factory=list)
    _step_tools: dict[int, list[str]] = field(default_factory=dict)
    _finished: bool = False

    def __post_init__(self):
        self.steps = requested_steps(self.user_request) or (self.user_request.strip(),)

    def _log(self, line: str):
        self.emit(line)

    def start(self):
        previous = self.previous
        if (previous and _normalized(self.user_request).strip(" .!?") in _RESUME
                and previous._finished and previous.mission_id == self.mission_id):
            self._log(
                f"[MISSION_TRACE] id={self.trace_id} state=FOLLOWUP "
                f"previous_trace={previous.trace_id} "
                f"previous_objective={_safe(previous.user_request)} "
                f"source=conversation_not_durable"
            )
        self._log(
            f"[MISSION_TRACE] id={self.trace_id} state=RECEIVED "
            f"mission_id={self.mission_id or 'none'} objective={_safe(self.user_request)} "
            f"outline_source=user_clauses_provisional steps={len(self.steps)}"
        )
        for idx, step in enumerate(self.steps, 1):
            self._log(
                f"[MISSION_STEP] id={self.trace_id} step={idx}/{len(self.steps)} "
                f"state=REQUESTED title={_safe(step, limit=300)}"
            )

    def ingest(self, line: str):
        if self._finished:
            return
        line = str(line)
        match = re.search(r"^\[AGENT_TOOL\] call=([^\s]+)", line)
        if match:
            name = match.group(1)
            self.tool_calls.append(name)
            hint = _tool_step_hint(name, self.steps)
            self._log(
                f"[MISSION_TRACE] id={self.trace_id} state=ACTING "
                f"tool={name} step_hint={hint if hint is not None else 'unresolved'} "
                f"hint_is_not_proof=true"
            )
            return
        match = re.search(r"^\[AGENT_TOOL\] result=([^\s]+) success=(True|False)", line)
        if match:
            self._log(
                f"[MISSION_TRACE] id={self.trace_id} state=TOOL_RESULT "
                f"tool={match.group(1)} success={match.group(2).lower()} "
                f"goal_completed=not_established"
            )
        elif line.startswith("[MISSION] proof_repair="):
            self._log(
                f"[MISSION_TRACE] id={self.trace_id} state=RECOVERING "
                "reason=missing_evidence"
            )

    def finish(self, actions: Iterable, *, error: str = ""):
        if self._finished:
            return
        self._finished = True
        actions = tuple(actions)
        for result in actions:
            name = str(getattr(result, "name", ""))
            hint = _tool_step_hint(name, self.steps)
            evidence = _evidence_level(result)
            if hint is not None:
                self._step_tools.setdefault(hint, []).append(evidence)
            self._log(
                f"[MISSION_EVIDENCE] id={self.trace_id} tool={name} "
                f"effect={evidence} goal_proof=not_inferred"
            )
        for idx, step in enumerate(self.steps, 1):
            evidences = self._step_tools.get(idx, [])
            state = "TOOL_ACTIVITY_RECORDED" if evidences else "NO_ASSIGNED_TOOL_EVIDENCE"
            self._log(
                f"[MISSION_STEP] id={self.trace_id} step={idx}/{len(self.steps)} "
                f"state={state} goal_verified=not_established"
            )
        result = ("INTERRUPTED" if error else
                  "NO_TOOLS" if not actions else "RESULTS_RECORDED_GOAL_UNVERIFIED")
        self._log(
            f"[MISSION_TRACE] id={self.trace_id} state={result} "
            f"tool_calls={len(self.tool_calls)} results={len(actions)} "
            f"goal_verification=not_certified "
            f"error_type={error or 'none'}"
        )
