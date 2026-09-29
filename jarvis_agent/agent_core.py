from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .mission import Mission, MissionEngine, MissionOutcome, MissionStep
from .planner import OllamaPlanner, PlanDecision, Planner, PlannerUnavailable
from .registry import DEFAULT_TOOL_REGISTRY, ToolRegistry
from .tools import ToolIntent, ToolResult


@dataclass(frozen=True)
class CoreResult:
    kind: str
    message: str = ""
    mission: MissionOutcome | None = None
    follow_up: str | None = None

    @property
    def final_intent(self) -> ToolIntent | None:
        return self.mission.final_intent if self.mission else None

    @property
    def final_result(self) -> ToolResult | None:
        return self.mission.final_result if self.mission else None


class AgentCore:
    """Jarvis orchestration layer.

    Voice/UI are deliberately outside this class. The core receives text,
    understands the objective, plans tools, executes a Mission and remembers
    enough recent context for corrections and follow-up turns.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        planner: Planner | None = None,
    ) -> None:
        self.registry = registry or DEFAULT_TOOL_REGISTRY
        self.planner = planner or OllamaPlanner(self.registry)
        self.engine = MissionEngine(self.registry)
        self._recent: deque[str] = deque(maxlen=10)
        self._pending_objective: str | None = None

    def reset_session(self) -> None:
        self._pending_objective = None

    def context_text(self) -> str:
        lines = list(self._recent)
        if self._pending_objective:
            lines.append(
                "OBJECTIF EN ATTENTE OU À CORRIGER: "
                + self._pending_objective
            )
        return "\n".join(lines)

    def handle(
        self,
        user_text: str,
        *,
        deterministic_intent: ToolIntent | None = None,
        log=None,
        phase=None,
    ) -> CoreResult:
        if (
            deterministic_intent is not None
            and deterministic_intent.name != "unknown"
        ):
            mission = Mission(
                objective=user_text,
                steps=[
                    MissionStep(
                        tool=deterministic_intent.name,
                        args=dict(deterministic_intent.args),
                        reason="Compréhension déterministe fiable.",
                    )
                ],
            )
            return self._execute_mission(
                mission,
                user_text=user_text,
                log=log,
                phase=phase,
            )

        if phase:
            phase("planning")
        try:
            decision = self.planner.plan(
                user_text,
                context=self.context_text(),
            )
        except PlannerUnavailable as exc:
            if log:
                log(f"[PLANNER] unavailable: {exc}")
            return CoreResult(
                kind="answer",
                message=(
                    "Mon planner local n'est pas disponible pour le moment. "
                    "Les commandes directes restent utilisables."
                ),
            )

        if log:
            log(
                f"[PLANNER] kind={decision.kind} "
                f"steps={len(decision.steps)} "
                f"question={decision.question!r}"
            )

        if decision.kind == "answer":
            self._record(
                f"Utilisateur: {user_text}",
                f"Jarvis: {decision.message}",
            )
            return CoreResult(kind="answer", message=decision.message)

        if decision.kind == "clarify":
            if self._pending_objective is None:
                self._pending_objective = user_text
            self._record(
                f"Utilisateur: {user_text}",
                f"Jarvis demande: {decision.question}",
            )
            return CoreResult(
                kind="clarify",
                message=decision.question,
            )

        if decision.kind == "mission":
            objective = self._pending_objective or user_text
            mission = Mission(
                objective=objective,
                steps=[
                    MissionStep(
                        tool=step.tool,
                        args=dict(step.args),
                        reason=step.reason,
                    )
                    for step in decision.steps
                ],
            )
            return self._execute_mission(
                mission,
                user_text=user_text,
                log=log,
                phase=phase,
            )

        return CoreResult(
            kind="answer",
            message="Je n'ai pas réussi à déterminer la prochaine action.",
        )

    def _execute_mission(
        self,
        mission: Mission,
        *,
        user_text: str,
        log=None,
        phase=None,
    ) -> CoreResult:
        if phase:
            phase("acting")
        outcome = self.engine.execute(mission, log=log)
        result = outcome.final_result

        if outcome.success:
            self._pending_objective = None
        else:
            # Keep the objective alive so "non, je voulais dire..." can be
            # interpreted as a correction of the failed mission.
            self._pending_objective = mission.objective

        summary = self._mission_summary(outcome)
        self._record(
            f"Utilisateur: {user_text}",
            summary,
        )

        return CoreResult(
            kind="mission",
            mission=outcome,
            follow_up=result.follow_up if result else None,
        )

    def _mission_summary(self, outcome: MissionOutcome) -> str:
        status = "réussie" if outcome.success else "échouée"
        lines = [
            f"Mission {outcome.mission.id} {status}: "
            f"{outcome.mission.objective}"
        ]
        for step in outcome.mission.steps:
            if step.result is not None:
                lines.append(
                    f"- {step.tool} => success={step.result.success} "
                    f"detail={step.result.detail}"
                )
            elif step.error:
                lines.append(f"- {step.tool} => erreur={step.error}")
        return "\n".join(lines)

    def _record(self, *lines: str) -> None:
        for line in lines:
            if line:
                self._recent.append(line)
