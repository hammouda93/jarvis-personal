"""Bounded step delegation through the existing runtime and tool registry.

Plans describe goals; operator-reviewed observation rules decide evidence.
No LLM, executor, retry thread or service-specific workflow is created here.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import time
from typing import Any
import uuid

from .kernel_contracts import MissionStatus


class SupervisorState(str, Enum):
    PLANNING = "PLANNING"
    READY = "READY"
    ACTING = "ACTING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SupervisorStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class MissionLimits:
    actions: int = 32
    model_calls: int = 12
    network_calls: int = 48
    recoveries: int = 2
    total_units: int = 96
    seconds: int = 900

    def validate(self) -> None:
        for key, value in asdict(self).items():
            cap = 86400 if key == "seconds" else 512
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError("invalid_mission_limit:" + key)


OBSERVATION_TOOLS = frozenset({
    "browser_list_tabs", "browser_get_active_tab", "browser_observe_dom",
    "browser_verify", "computer_observe", "computer_verify",
    "computer_list_windows", "computer_get_active_window", "list_windows",
    "inspect_active_window", "inspect_interface", "observe_screen",
})
_REVERSIBLE_TOOLS = frozenset({"open_url", "open_application", "browser_navigate",
                              "browser_activate_tab", "browser_back", "browser_forward"})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(_canonical(plan).encode("utf-8")).hexdigest()


def _detail(result: Any) -> dict:
    raw = getattr(result, "detail", "")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def validate_rule(rule: dict) -> None:
    if not isinstance(rule, dict) or set(rule) != {"tool", "arguments", "path", "equals"}:
        raise ValueError("invalid_observation_rule")
    if rule["tool"] not in OBSERVATION_TOOLS:
        raise ValueError("evidence_requires_native_read_only_observation")
    if not isinstance(rule["arguments"], dict) or len(_canonical(rule)) > 6000:
        raise ValueError("invalid_observation_arguments")
    path = rule["path"]
    if (not isinstance(path, list) or not 1 <= len(path) <= 12
            or any(type(part) not in (str, int) for part in path)
            or any(isinstance(part, int) and part < 0 for part in path)):
        raise ValueError("invalid_evidence_path")
    if path[-1] in ("success", "verified", "message", "outcome_unknown"):
        raise ValueError("tool_success_is_not_goal_evidence")


def matches_rule(result: Any, rule: dict) -> bool:
    if not bool(getattr(result, "success", False)):
        return False
    value = _detail(result)
    if value.get("outcome_unknown") is True:
        return False
    try:
        for part in rule["path"]:
            if type(part) is int:
                if not isinstance(value, list):
                    return False
                value = value[part]
            else:
                if not isinstance(value, dict):
                    return False
                value = value[part]
        # JSON equality preserves the distinction between true and 1.
        return _canonical(value) == _canonical(rule["equals"])
    except (KeyError, IndexError, TypeError, ValueError):
        return False


_SCOPE: ContextVar[Any] = ContextVar("jarvis_supervised_execution", default=None)


def reserve_model_request() -> None:
    """Called at each provider request, including Cerebras secondary/Groq."""
    scope = _SCOPE.get()
    if scope is not None:
        scope.reserve(model_calls=1, network_calls=1)


def execution_scope_active() -> bool:
    return _SCOPE.get() is not None


class SupervisedToolRegistry:
    """Transparent gate on the single existing registry; inactive outside a mission."""

    def __init__(self, delegate: Any):
        self.delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def requires_confirmation(self, name: str) -> bool:
        required = self.delegate.requires_confirmation(name)
        scope = _SCOPE.get()
        if scope is not None:
            from .hermes_reliability import is_mutating
            required = required or (is_mutating(name) and name not in _REVERSIBLE_TOOLS)
        if required and scope is not None:
            scope.waiting_approval = True
            scope.transition(SupervisorState.WAITING_APPROVAL, tool=str(name))
        return required

    def execute(self, name: str, arguments: dict, *, approved: bool = False):
        scope = _SCOPE.get()
        if scope is None:
            return self.delegate.execute(name, arguments, approved=approved)
        if not approved and self.requires_confirmation(name):
            from .native_tools import AgentActionResult
            return AgentActionResult(name=name, success=False,
                message="Confirmation explicite requise pour cette action de mission.",
                detail='{"approval_required":true,"verified":false}')
        scope.reserve(actions=1, network_calls=1)
        scope.transition(SupervisorState.ACTING, tool=str(name))
        result = self.delegate.execute(name, arguments, approved=approved)
        detail = _detail(result)
        scope.actions.append({
            "tool": str(name)[:120], "success": bool(getattr(result, "success", False)),
            "approved": bool(approved), "outcome_unknown": detail.get("outcome_unknown") is True,
        })
        if detail.get("approval_required") is True:
            scope.waiting_approval = True
            scope.transition(SupervisorState.WAITING_APPROVAL, tool=str(name))
            return result
        if detail.get("outcome_unknown") is True or detail.get("reliability_guard"):
            raise SupervisorStopped("action_outcome_requires_review")
        scope.observe(self.delegate)
        return result


class ActiveMissionSupervisor:
    def __init__(self, runtime: Any, tools: SupervisedToolRegistry):
        self.runtime = runtime
        self.tools = tools
        self.actions: list[dict] = []
        self.observations: list[dict] = []
        self.waiting_approval = False
        self.step: dict = {}
        self.mission_id = ""

    def _load(self):
        mid = str(self.runtime.active_mission_id or "")
        if not mid:
            raise RuntimeError("no_active_mission")
        loaded = self.runtime.context_store.load(mid)
        if loaded is None:
            raise KeyError("mission_checkpoint_not_found")
        state, version = loaded
        if state.user_id != self.runtime.owner_user_id:
            raise PermissionError("mission_owner_mismatch")
        return state, version

    def approve(self, *, digest: str, rules: dict | None = None,
                limits: MissionLimits | None = None) -> dict:
        """Operator-only API, never exposed as a model tool."""
        with self.runtime._lock:
            state, version = self._load()
            if state.status in (MissionStatus.BLOCKED, MissionStatus.COMPLETED, MissionStatus.FAILED):
                raise RuntimeError("mission_not_ready_for_supervision")
            plan = state.expected_state.get("semantic_contract")
            if not isinstance(plan, dict) or not plan.get("steps"):
                raise ValueError("mission_plan_required")
            if plan.get("unresolved"):
                raise ValueError("mission_ambiguities_require_review")
            if digest != plan_digest(plan):
                raise ValueError("reviewed_plan_changed")
            if state.observed_state.get("active_supervisor"):
                raise RuntimeError("supervision_already_approved")
            requirements = {str(x) for s in plan["steps"] for x in s.get("required_evidence", ())}
            if any(not s.get("required_evidence") for s in plan["steps"]):
                raise ValueError("all_steps_require_evidence")
            approved_rules = json.loads(_canonical(rules or {}))
            if set(approved_rules) - requirements:
                raise ValueError("evidence_requirement_not_in_plan")
            for rule in approved_rules.values():
                validate_rule(rule)
            budget = limits or MissionLimits()
            budget.validate()
            state.observed_state["active_supervisor"] = {
                "state": SupervisorState.READY.value, "plan_digest": digest,
                "limits": asdict(budget), "usage": {}, "started_at": time.time(),
                "rules": approved_rules, "step_id": "", "reports": [], "executed_steps": [],
                "reason": "", "tool": "", "executor": "legacy_runtime",
            }
            self.runtime.context_store.save(state, expected_version=version)
            self.runtime._audit(state.mission_id, "supervisor.approved", {
                "plan_digest": digest, "rule_count": len(approved_rules), "limits": asdict(budget),
            })
            return self.snapshot()

    def snapshot(self) -> dict:
        state, _ = self._load()
        record = dict(state.observed_state.get("active_supervisor") or {})
        return {"mission_id": state.mission_id, **record}

    def transition(self, target: SupervisorState, *, reason: str = "", tool: str = "") -> None:
        state, version = self._load()
        record = state.observed_state["active_supervisor"]
        record.update(state=target.value, step_id=self.step.get("step_id", ""),
                      reason=reason[:120], tool=tool[:120])
        self.runtime.context_store.save(state, expected_version=version)
        self.runtime._audit(state.mission_id, "supervisor.state", {
            "state": target.value, "step_id": record["step_id"], "tool": tool[:120], "reason": reason[:120],
        })

    def reserve(self, **increments: int) -> None:
        state, version = self._load()
        record = state.observed_state["active_supervisor"]
        limits, usage = record["limits"], dict(record["usage"])
        if time.time() - record["started_at"] > limits["seconds"]:
            raise SupervisorStopped("mission_time_budget_exhausted")
        units = sum(increments.values())
        changes = {**increments, "total_units": units}
        for key, amount in changes.items():
            if usage.get(key, 0) + amount > limits[key]:
                raise SupervisorStopped("mission_budget_exhausted:" + key)
        for key, amount in changes.items():
            usage[key] = usage.get(key, 0) + amount
        record["usage"] = usage
        # Persist consumption before the provider/tool can start an effect.
        self.runtime.context_store.save(state, expected_version=version)

    def observe(self, registry: Any) -> None:
        state, _ = self._load()
        rules = state.observed_state["active_supervisor"]["rules"]
        for requirement in self.step.get("required_evidence", ()):
            rule = rules.get(requirement)
            if rule is None:
                continue
            self.reserve(actions=1, network_calls=1)
            self.transition(SupervisorState.OBSERVING, tool=rule["tool"])
            result = registry.execute(rule["tool"], rule["arguments"], approved=False)
            ref = "observation:" + uuid.uuid4().hex
            self.transition(SupervisorState.VERIFYING, tool=rule["tool"])
            matched = matches_rule(result, rule)
            self.observations.append({"requirement": requirement, "ref": ref,
                                      "matched": matched, "tool": rule["tool"]})
            if matched:
                self.runtime.register_goal_evidence(requirement, proof_ref=ref)
            else:
                current, version = self._load()
                current.observed_state.get("plan_evidence", {}).pop(requirement, None)
                self.runtime.context_store.save(current, expected_version=version)

    def advance(self, user_text: str | None = None, *, context: str = "", log=None, phase=None):
        """One bounded specialist turn. Reverification never redispatches the turn."""
        with self.runtime._lock:
            state, version = self._load()
            record = state.observed_state.get("active_supervisor")
            if not record:
                raise RuntimeError("supervision_requires_reviewed_plan")
            plan = state.expected_state["semantic_contract"]
            if record["plan_digest"] != plan_digest(plan):
                raise SupervisorStopped("reviewed_plan_changed")
            if state.status == MissionStatus.BLOCKED or record["state"] in (
                SupervisorState.BLOCKED.value, SupervisorState.FAILED.value,
                SupervisorState.ACTING.value, SupervisorState.OBSERVING.value, SupervisorState.VERIFYING.value,
            ):
                raise SupervisorStopped("mission_recovery_requires_review")
            review = self.runtime.review_mission(state.mission_id)
            by_id = {s["id"]: s for s in review.get("steps", ())}
            ready = [s for s in plan["steps"] if by_id[s["step_id"]]["state"] != "verified"
                     and not by_id[s["step_id"]]["unmet_dependencies"]]
            self.mission_id = state.mission_id
            if not ready:
                if not all(s["state"] == "verified" for s in by_id.values()):
                    raise SupervisorStopped("mission_dependencies_not_verified")
            self.step = ready[0] if ready else plan["steps"][0]
            self.actions, self.observations, self.waiting_approval = [], [], False
            # A prior successful turn is only reobserved, never repeated while waiting for proof.
            waiting_proof = (not ready or
                             self.step["step_id"] in record.get("executed_steps", []))
            record.update(state=(SupervisorState.OBSERVING if waiting_proof else SupervisorState.ACTING).value,
                          step_id=self.step["step_id"], reason="", tool="")
            # Optimistic claim also excludes other processes sharing the checkpoint.
            self.runtime.context_store.save(state, expected_version=version)
            token = _SCOPE.set(self)
            result = None
            try:
                self.reserve()
                if not waiting_proof:
                    proposed = self.runtime.propose_mission_capabilities(self.mission_id)
                    candidates = next((s.get("candidates", []) for s in proposed.get("steps", [])
                                       if s.get("step_id") == self.step["step_id"]), [])
                    agents = {str(c["agent"]) for c in candidates}
                    agent_id = next(iter(agents)) if len(agents) == 1 else "interaction"
                    step_context = "[SUPERVISED STEP DATA]\n" + _canonical({
                        "step": self.step, "objective": plan["objective"],
                        "capability_proposal": proposed, "executor": "legacy_runtime", "responsible_agent": agent_id,
                        "rule": "Observe actual targets before choosing arguments. Preserve existing approvals. Tool success is not goal success.",
                    }) + "\n[END SUPERVISED STEP DATA]"
                    result = self.runtime._run(user_text or self.step["intent"], context="\n\n".join([context, step_context]), log=log, phase=phase)
                self.observe(self.tools.delegate)
                state, version = self._load()
                if state.status == MissionStatus.BLOCKED:
                    raise SupervisorStopped("runtime_requires_independent_review")
                current_review = self.runtime.review_mission(self.mission_id)
                complete = all(s["state"] == "verified" for s in current_review["steps"])
                if complete:
                    # Later work may have changed an earlier target. Recheck all
                    # configured predicates immediately before the completion gate.
                    original_step = self.step
                    try:
                        for candidate in plan["steps"]:
                            self.step = candidate
                            self.observe(self.tools.delegate)
                    finally:
                        self.step = original_step
                    current_review = self.runtime.review_mission(self.mission_id)
                    complete = all(s["state"] == "verified" for s in current_review["steps"])
                    state, version = self._load()
                verified = next(s for s in current_review["steps"] if s["id"] == self.step["step_id"])["state"] == "verified"
                report = {
                    "step_id": self.step["step_id"], "objective": self.step["intent"],
                    "agent": agent_id if not waiting_proof else "independent_verifier",
                    "action_requested": user_text or self.step["intent"], "executor": "legacy_runtime",
                    "actions": self.actions, "observations": self.observations,
                    "permissions": "existing_runtime_confirmation", "verified": verified,
                    "risks": [] if verified else ["independent_evidence_missing"],
                }
                record = state.observed_state["active_supervisor"]
                record["reports"] = [*record["reports"], report][-24:]
                if not waiting_proof and not self.waiting_approval:
                    record["executed_steps"] = list(dict.fromkeys([
                        *record.get("executed_steps", []), self.step["step_id"],
                    ]))
                self.runtime.context_store.save(state, expected_version=version)
                target = (SupervisorState.WAITING_APPROVAL if self.waiting_approval else
                          SupervisorState.COMPLETED if complete else SupervisorState.READY if verified else
                          SupervisorState.RECOVERING)
                self.transition(target)
                if complete and not self.waiting_approval:
                    self.runtime.complete_mission(proof_ref="supervisor:" + uuid.uuid4().hex)
                return {"mission_id": self.mission_id, "state": target.value,
                        "goal_verified": complete and not self.waiting_approval, "report": report,
                        "text": str(getattr(result, "text", "") or ""), "_turn_result": result}
            except Exception as exc:
                self.transition(SupervisorState.BLOCKED, reason=(str(exc) if isinstance(exc, SupervisorStopped) else type(exc).__name__))
                state, version = self._load()
                state.status = MissionStatus.BLOCKED
                state.pending_action = {"reason": "supervisor_requires_review", "manual_review_required": True}
                self.runtime.context_store.save(state, expected_version=version)
                raise
            finally:
                _SCOPE.reset(token)
