"""Read-only mission trace for the ordinary conversational agent.

An event projection, not a second planner, an executor, an authorization gate
or a durable mission checkpoint. No API calls. All observed action outcomes
come from runtime tool results; a successful tool is not goal completion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ast
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
    r"v[eé]rifie|v[eé]rifier|contr[oô]le|contr[oô]ler|inspecte|inspecter|"
    r"clique|cliquer|click|ferme|fermer|close|"
    r"ecris|ecrire|saisis|saisir|write|read|lis|lire|"
    r"retourne|reviens|back|rappelle|retrouve|"
    r"envoie|envoyer|send|installe|installer"
)
_SPLIT_INSTRUCTION = re.compile(
    r"\b(?:puis|ensuite|then)\b|"
    r"[,;]\s*(?=(?:" + _ACTION + r")\b)|"
    r"\bet\b\s+(?=(?:" + _ACTION + r")\b)",
    re.I,
)
_QUOTED = re.compile(r"""(?:"[^"\n]*"|«[^»\n]*»|“[^”\n]*”)""")
_NEGATIVE_START = re.compile(r"^\s*(?:ne\b|n['’]|never\b|do not\b|don't\b)", re.I)
_OPTIONAL_START = re.compile(r"^\s*(?:si\b|if\b|unless\b|facultativement\b)", re.I)

_RESUME = frozenset({"oui", "ok", "continue", "poursuis", "vas y", "vas-y", "go", "d'accord"})
_OBSERVERS = frozenset({
    "list_windows", "inspect_active_window", "inspect_interface", "observe_screen",
    "computer_observe", "computer_find", "computer_list_windows",
    "browser_list_tabs", "browser_get_active_tab", "browser_observe_dom",
    "browser_find", "semantic_memory_search", "semantic_memory_inspect",
    "read_observation_evidence",
})
_VERIFIERS = frozenset({"computer_verify", "browser_verify", "verify_file_artifact"})


def requested_steps(user_text: str) -> tuple[str, ...]:
    raw = str(user_text or "")
    # Explicit user-authored numbered instructions are more reliable than
    # splitting arbitrary prose. They remain only a provisional log outline,
    # never an instruction source or a source of permissions.
    numbered = [
        match.group(1).strip()
        for match in re.finditer(
            r"^\s*(?:[1-9]|1[0-2])[.)]\s+([^\r\n]+)\s*$",
            raw, flags=re.M,
        )
        if not _NEGATIVE_START.match(match.group(1))
    ]
    if len(numbered) >= 2:
        return tuple(numbered[:12])
    # Keep exact user-supplied entity names in quotes. Mask those spans during
    # splitting so a quoted instruction cannot become a new requested action.
    masked = _QUOTED.sub(lambda m: "Q" * len(m.group(0)), raw)
    matches = list(_SPLIT_INSTRUCTION.finditer(masked))
    spans = []
    start = 0
    for match in matches:
        spans.append(raw[start:match.start()])
        start = match.end()
    spans.append(raw[start:])
    result: list[str] = []
    for clause in spans:
        cleaned = clause.strip(" ,;.!?\n\t")
        if not cleaned or _NEGATIVE_START.match(cleaned) or _OPTIONAL_START.match(cleaned):
            continue
        # A prohibitive addendum is a constraint, not another positive step.
        cleaned = re.split(r"\b(?:mais\s+ne|but\s+do\s+not)\b",
                           cleaned, maxsplit=1, flags=re.I)[0].strip(" ,;")
        if cleaned:
            result.append(cleaned)
    # Provisional instructions only: never grant permission or mark success.
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
    # Opening a new destination belongs to the earliest requested opening
    # stage. An ordinal result is a separate later click, not open_url.
    if tool in {"open_url", "open_application", "open_file"} and matches:
        return matches[0]
    return matches[0] if len(matches) == 1 else None


def _target_evidence_hint(result, steps, earlier, *, clicked_ref=""):
    """Correlate a verified UI transition with an observed link label.

    A model-generated URL or a tool name alone is NEVER evidence. This uses
    the observed href/label of a unique DOM find and the verified destination
    in the later click's post-observation. It only enriches the trace; no
    step or overall goal is certified here.
    """
    if (getattr(result, "name", None) != "browser_click"
            or not getattr(result, "success", False)):
        return None
    try:
        detail = json.loads(getattr(result, "detail", "") or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(detail, dict) or detail.get("verified") is not True:
        return None
    if detail.get("postcondition") != "navigation_observed":
        return None
    current = detail.get("post_observation") or {}
    tab = current.get("tab") if isinstance(current, dict) else None
    url = str(tab.get("url") or "") if isinstance(tab, dict) else ""
    if not url.startswith(("https://", "http://")):
        return None
    labels = set()
    for prior in earlier:
        if getattr(prior, "name", None) != "browser_find" or not getattr(prior, "success", False):
            continue
        try:
            parsed = json.loads(getattr(prior, "detail", "") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        matches = parsed.get("matches")
        if not isinstance(matches, list) or len(matches) != 1:
            continue
        match = matches[0]
        if not isinstance(match, dict):
            continue
        observed_ref = str(match.get("ref") or "")
        if clicked_ref and observed_ref:
            # Directly linked tool call ref is stronger than an href: the
            # page may redirect after clicking a genuinely observed target.
            if observed_ref != clicked_ref:
                continue
        elif str(match.get("href") or "") != url:
            continue
        label = _normalized(match.get("name") or match.get("text") or "")
        if len(label) >= 4:
            labels.add(label)
    if len(labels) != 1:
        return None
    label = next(iter(labels))
    indices = [idx for idx, step in enumerate(steps, 1)
               if label in _normalized(step) and
               re.search(r"\b(?:clique|cliquer|click|ouvre|ouvrir|open)\b",
                         _normalized(step))]
    return indices[0] if len(indices) == 1 else None


def _anaphoric_ui_activity_hint(result, steps):
    """Associate an actual UI click with a conditional anaphoric step.

    Example shape: 'inspect the panel, then open it if needed', followed by
    a click on an *observed* control named 'Open panel'. This is a tentative
    tool-activity annotation, NOT a verified step or an authorization.
    """
    if (getattr(result, "name", None) not in {"browser_click", "click_ui_element"}
            or not getattr(result, "success", False)):
        return None
    try:
        detail = json.loads(getattr(result, "detail", "") or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(detail, dict):
        return None
    target = detail.get("target_after")
    if not isinstance(target, dict):
        return None
    label = _normalized(target.get("name") or target.get("aria_label") or "")
    if not re.search(r"\b(?:ouvrir|ouvre|open)\b", label):
        return None
    # Prefer significant tokens from the observed target label, not invented
    # application-specific menu identifiers or untrusted DOM instructions.
    words = set(re.findall(r"[a-z]{4,}", label)) - {
        "ouvrir", "ouvre", "open", "ouvrirai", "openit", "button", "bouton",
    }
    if not words:
        return None
    candidates = []
    for i, step in enumerate(steps):
        current = _normalized(step)
        if not re.search(r"\b(?:ouvre|ouvrir|open)[- ](?:le|la|les|it)\b", current):
            continue
        prior = " ".join(_normalized(value) for value in steps[:i])
        if prior and any(re.search(r"\b" + re.escape(word) + r"\b", prior) for word in words):
            candidates.append(i + 1)
    return candidates[0] if len(candidates) == 1 else None


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
    _tool_call_refs: list[str] = field(default_factory=list)
    _step_tools: dict[int, list[str]] = field(default_factory=dict)
    _finished: bool = False
    _goal_proof_logged: bool = False

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
            # Capture only the opaque observed target ref from the agent's
            # own tool dispatch log, not the full arguments or UI content.
            clicked_ref = ""
            raw = line.split(" args=", 1)
            if len(raw) == 2 and len(raw[1]) < 3000:
                try:
                    call_args = ast.literal_eval(raw[1])
                    if isinstance(call_args, dict):
                        candidate = call_args.get("ref")
                        if isinstance(candidate, str) and len(candidate) < 300:
                            clicked_ref = candidate
                except (SyntaxError, ValueError, TypeError, MemoryError):
                    pass
            self._tool_call_refs.append(clicked_ref)
            hint = _tool_step_hint(name, self.steps)
            phase = ("VERIFYING" if name in _VERIFIERS else
                     "OBSERVING" if name in _OBSERVERS else "ACTING")
            self._log(
                f"[MISSION_TRACE] id={self.trace_id} state={phase} "
                f"tool={name} step_hint={hint if hint is not None else 'unresolved'} "
                f"hint_is_not_proof=true"
            )
            if hint is not None:
                self._log(
                    f"[MISSION_STEP] id={self.trace_id} step={hint}/{len(self.steps)} "
                    f"state=ACTIVE_HINT tool={name} "
                    f"authoritative_assignment=false"
                )
            return
        match = re.search(r"^\[AGENT_TOOL\] result=([^\s]+) success=(True|False)", line)
        if match:
            self._log(
                f"[MISSION_TRACE] id={self.trace_id} state=TOOL_RESULT "
                f"tool={match.group(1)} success={match.group(2).lower()} "
                f"goal_completed=not_established"
            )
        elif line == "[MISSION] state=COMPLETED evidence=independent_browser_navigation":
            # Only the trusted convergence runtime announces this state.
            # Tool successes and conversational hints cannot certify goals.
            self._goal_proof_logged = True
            self._log(
                f"[MISSION_TRACE] id={self.trace_id} "
                "state=GOAL_CERTIFIED proof_source=independent_browser_navigation"
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
        earlier = []
        call_sequence_aligned = (
            len(actions) == len(self.tool_calls)
            and all(str(getattr(action, "name", "")) == call
                    for action, call in zip(actions, self.tool_calls))
        )
        for action_index, result in enumerate(actions):
            name = str(getattr(result, "name", ""))
            hint = _tool_step_hint(name, self.steps)
            clicked_ref = (self._tool_call_refs[action_index]
                           if call_sequence_aligned else "")
            provenance_hint = _target_evidence_hint(
                result, self.steps, earlier, clicked_ref=clicked_ref
            )
            activity_hint = _anaphoric_ui_activity_hint(result, self.steps)
            evidence = _evidence_level(result)
            if hint is not None:
                self._step_tools.setdefault(hint, []).append(evidence)
            if activity_hint is not None:
                self._step_tools.setdefault(activity_hint, []).append(evidence)
                self._log(
                    f"[MISSION_STEP] id={self.trace_id} "
                    f"step={activity_hint}/{len(self.steps)} "
                    "state=ACTIVITY_CORRELATED source=observed_ui_target "
                    "authoritative_assignment=false goal_verified=not_established"
                )
            if provenance_hint is not None:
                self._step_tools.setdefault(provenance_hint, []).append(evidence)
                self._log(
                    f"[MISSION_STEP] id={self.trace_id} "
                    f"step={provenance_hint}/{len(self.steps)} "
                    "state=EVIDENCE_CORRELATED source=observed_href_and_verified_navigation "
                    "authoritative_assignment=false goal_verified=not_established"
                )
            self._log(
                f"[MISSION_EVIDENCE] id={self.trace_id} tool={name} "
                f"effect={evidence} goal_proof=not_inferred"
            )
            earlier.append(result)
        certified = self._goal_proof_logged and not error
        for idx, step in enumerate(self.steps, 1):
            evidences = self._step_tools.get(idx, [])
            if certified:
                # This outline is a non-authoritative split of the original
                # user request; its steps have no independent evidence gate.
                # Once the trusted runtime has certified the ENTIRE original
                # goal, reporting NO_ASSIGNED_TOOL_EVIDENCE for an outlined
                # clause contradicts the goal outcome. Distinguish the goal
                # certificate from an independently verified step, without
                # inventing a per-step proof or retroactively assigning tools.
                self._log(
                    f"[MISSION_STEP] id={self.trace_id} step={idx}/{len(self.steps)} "
                    "state=COVERED_BY_GOAL_CERTIFICATE "
                    "goal_verified=certified step_verified=not_independently_assessed "
                    f"tool_evidence={'correlated' if evidences else 'not_assigned'} "
                    "source=trusted_runtime"
                )
            else:
                state = ("TOOL_ACTIVITY_RECORDED"
                         if evidences else "NO_ASSIGNED_TOOL_EVIDENCE")
                self._log(
                    f"[MISSION_STEP] id={self.trace_id} step={idx}/{len(self.steps)} "
                    f"state={state} goal_verified=not_established"
                )
        result = ("INTERRUPTED" if error else "COMPLETED" if certified else
                  "NO_TOOLS" if not actions else "RESULTS_RECORDED_GOAL_UNVERIFIED")
        self._log(
            f"[MISSION_TRACE] id={self.trace_id} state={result} "
            f"tool_calls={len(self.tool_calls)} results={len(actions)} "
            f"goal_verification={'certified' if certified else 'not_certified'} "
            f"error_type={error or 'none'}"
        )
