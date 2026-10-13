"""Opt-in, non-authoritative mission checkpoints for the existing live runtime.

This is a safe convergence seam, NOT a second executor or an automatic planner.
The delegate remains the only executor. An interrupted action is never replayed.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
import hashlib
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from .agent_router import AgentRoutingContext, CapabilityAgentRouter
from .capability_registry import DEFAULT_CAPABILITY_REGISTRY
from .event_journal import StructuredEventJournal
from .kernel_contracts import EventKind, MissionContext, MissionStatus
from .kernel_service import JarvisKernel
from .mission_context_store import MissionContextStore
from .mission_orchestrator import MissionOrchestrator
from .mission_semantics import MissionContract
from .task_graph import MissionTaskGraph, TaskNode, TaskStatus
from .task_graph_store import TaskGraphStore

_LIVE_USAGE = ContextVar("jarvis_live_mission_usage", default=None)


@contextmanager
def live_mission_usage_scope(runtime, mission_id):
    token = _LIVE_USAGE.set((runtime, mission_id))
    try:
        yield
    finally:
        _LIVE_USAGE.reset(token)


def note_live_model_request():
    scope = _LIVE_USAGE.get()
    if scope is not None:
        scope[0]._note_execution_usage(scope[1], request=True)


def note_live_model_usage(usage, provider):
    scope = _LIVE_USAGE.get()
    if scope is not None:
        scope[0]._note_execution_usage(scope[1], usage=usage, provider=provider)


def note_live_tool_approval(name, arguments):
    scope = _LIVE_USAGE.get()
    if scope is not None:
        scope[0]._record_tool_approval(scope[1], name, arguments)


def _default_dir() -> Path:
    root = Path(os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME") or Path.home())
    return root / "JarvisPersonal" / "runtime_convergence"


class _ClosingMissionContextStore(MissionContextStore):
    """The legacy with-connection pattern commits but does not close SQLite.

    Close connections deterministically so Windows can cleanly delete temporary
    test workspaces, and release file handles after every checkpoint.
    """

    @contextmanager
    def _connect(self):
        conn = super()._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()


class _ClosingTaskGraphStore(TaskGraphStore):
    @contextmanager
    def _connect(self):
        conn = super()._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()


class _ClosingEventJournal(StructuredEventJournal):
    @contextmanager
    def _connect(self):
        conn = super()._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()


class LiveMissionContinuityRuntime:
    """Checkpoint real turns without ever dispatching or repeating a tool call.

    An implicit mission represents one ordinary conversation turn. Explicit
    begin_mission/resume_mission calls group later turns under the same goal.
    Completion of a tool call is deliberately NOT proof of the user's goal.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        base_dir: str | Path | None = None,
        owner_user_id: str = "local-user",
    ):
        self.delegate = delegate
        root = Path(base_dir) if base_dir is not None else _default_dir()
        self.context_store = _ClosingMissionContextStore(root / "mission_context.sqlite3")
        self.graph_store = _ClosingTaskGraphStore(root / "task_graphs.sqlite3")
        self.journal = _ClosingEventJournal(root / "mission_events.sqlite3")
        self.agent_router = CapabilityAgentRouter(DEFAULT_CAPABILITY_REGISTRY)
        # Use existing mission ownership/orchestration contracts, while the
        # dispatcher remains strictly inactive until a later live gate.
        self.kernel = JarvisKernel()
        self.orchestrator = MissionOrchestrator(
            kernel=self.kernel,
            context_store=self.context_store,
            graph_store=self.graph_store,
        )
        self.owner_user_id = str(owner_user_id or "local-user")
        self._active_mission_id: str | None = None
        self._explicit = False
        self._lock = threading.RLock()
        self.active_supervisor = None
        self._prepared_turn = None
        self._control_reply = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    @property
    def active_mission_id(self) -> str | None:
        return self._active_mission_id

    def begin_mission(self, goal: str) -> str:
        """Open an explicit multi-turn mission; never infer a plan from keywords."""
        with self._lock:
            if self._active_mission_id is not None:
                raise RuntimeError("mission_already_active")
            if not str(goal or "").strip():
                raise ValueError("mission_goal_required")
            mission_id = "live_" + uuid.uuid4().hex
            context = MissionContext(
                mission_id=mission_id,
                user_goal=str(goal)[:2000],
                user_id=self.owner_user_id,
                owner_agent_id="interaction",
                observed_state={
                    "mode": "runtime_convergence_v1",
                    "executor": "legacy_runtime",
                    "goal_verified": False,
                    "completed_turns": 0,
                },
                tags=["runtime-convergence", "non-authoritative"],
            )
            self._initialize_goal_control(context)
            import platform
            context.observed_state["evidence_ledger"] = [{
                "id": "environment_os", "source": "runtime_environment",
                "kind": "environment", "identity": platform.system(),
                "observed": {"operating_system": platform.system()},
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "reliability": "observed", "scope": "mission_only", "goal_proof": False,
            }]
            self.orchestrator.register(context, MissionTaskGraph(mission_id))
            self.journal.create_mission(
                mission_id=mission_id,
                goal_summary=context.user_goal[:600],
                user_id=self.owner_user_id,
                owner_agent_id="interaction",
            )
            self._active_mission_id = mission_id
            self._explicit = True
            return mission_id

    @staticmethod
    def _initialize_goal_control(state):
        # Additive JSON migration: old checkpoints remain readable.
        state.expected_state.setdefault("goal_control", {
            "version": 2, "initial_goal": state.user_goal, "revision": 1,
            "history": [{"revision": 1, "goal": state.user_goal, "kind": "initial"}],
            "permissions": [], "instruction_history": [],
            "unresolved": [],
        })

    def prepare_mission_turn(self, user_text, *, log=None):
        """Interpret a new human turn before trace/planning/execution.

        No tools may execute in this phase. A failed interpretation conserves
        the prior objective rather than quietly executing a contradictory goal.
        """
        mid = self._active_mission_id
        self._prepared_turn = (mid, str(user_text))
        self._control_reply = None
        if not mid or self._mission_information_requested(user_text):
            return
        with self._lock:
            state, version = self.context_store.load(mid)
            if state.user_id != self.owner_user_id:
                raise PermissionError("mission_owner_mismatch")
            self._initialize_goal_control(state)
            goal_control = state.expected_state["goal_control"]
            if state.observed_state.get("active_supervisor"):
                # This separate, explicitly approved mode already has a
                # bounded disposition/approval protocol. Do not consume its
                # provider response or replace its reviewed plan.
                return
            text = str(user_text or "").strip()
            if (text == state.user_goal.strip()
                    or text.casefold().strip(" .!?") in {
                        "continue", "continue la mission", "poursuis", "oui", "yes",
                        "ok", "okay", "go on", "continue the mission", "تابع",
                    }):
                # Exact continuation doesn't revoke any original prohibition.
                return
            from .mission_turn_contract import request_decision, MissionTurnDecision
            facts = list(state.observed_state.get("evidence_ledger") or [])
            payload = {
                "current_goal": state.user_goal, "goal_revision": goal_control["revision"],
                "human_message": text, "status": state.status.value,
                "pending_confirmation": state.pending_confirmation,
                "constraints": list((state.expected_state.get("semantic_contract") or {}).get("constraints") or []),
                "unresolved": list((state.expected_state.get("semantic_contract") or {}).get("unresolved") or []),
                "facts": [f for f in facts if f.get("kind") != "expired_reference"][-12:],
                "permissions_are_not_granted_by_interpretation": True,
            }
            try:
                with live_mission_usage_scope(self, mid):
                    raw = request_decision(self.delegate, payload)
                if raw is None:
                    return
                decision = MissionTurnDecision.parse(raw, human_text=text, current_goal=state.user_goal)
            except Exception as exc:
                # Model/API failure has no external effect and cannot weaken
                # an existing constraint or unblock an unknown action.
                if log:
                    log("[MISSION_CONTROL] decision=unavailable error_type=" + type(exc).__name__)
                self._control_reply = "Je n'ai pas pu vérifier le changement de mission. L'objectif et les permissions précédents sont conservés."
                return
            if decision.kind == "new":
                self.detach_mission()
                self._audit(mid, "mission.detached_for_new_goal", {"checkpoint_preserved": True})
                self._prepared_turn = (None, text)
                return
            if decision.kind == "status":
                self._control_reply = self._mission_status_text(mid)
                return
            if decision.kind in {"cancel", "pause"}:
                if decision.kind == "cancel":
                    state.status = MissionStatus.FAILED
                    state.observed_state["cancelled_by_user"] = True
                elif state.status != MissionStatus.BLOCKED:
                    state.status = MissionStatus.WAITING_USER
                self.context_store.save(state, expected_version=version)
                self._audit(mid, "mission." + decision.kind, {"goal_revision": goal_control["revision"]})
                self._control_reply = ("Mission annulée. Les preuves et les effets incertains restent conservés."
                                       if decision.kind == "cancel" else "Mission suspendue.")
                if decision.kind == "cancel":
                    self.detach_mission()
                return
            if decision.kind in {"revise", "correct", "clarify", "authorize", "constrain"}:
                if len(goal_control["history"]) >= 32:
                    self._control_reply = "La limite de révisions de cette mission est atteinte."
                    return
                # Keep the source verbatim; the model cannot introduce an
                # invented destination, path, instruction or permission.
                revised = state.user_goal
                for span in decision.superseded:
                    revised = revised.replace(span, "", 1)
                revised = revised.strip() + "\n" + text
                if len(revised) > 4000:
                    self._control_reply = "La révision dépasse le budget de contexte de la mission."
                    return
                old_plan = state.expected_state.get("semantic_contract")
                goal_control["unresolved"] = list((old_plan or {}).get("unresolved")
                                                  or goal_control.get("unresolved") or [])
                if state.observed_state.get("active_supervisor"):
                    # An approved semantic plan/digest cannot silently change.
                    self._control_reply = "Ce plan supervisé doit être révisé et approuvé avant de changer l'objectif."
                    return
                revision = int(goal_control["revision"]) + 1
                goal_control["history"].append({
                    "revision": revision, "kind": decision.kind, "goal": revised,
                    "human_source": text, "superseded": list(decision.superseded),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                })
                goal_control["instruction_history"].append(text)
                goal_control["revision"] = revision
                state.user_goal = revised
                # Reusing old tool facts is useful, reusing a goal certificate
                # for a changed objective is forbidden.
                state.observed_state["goal_verified"] = False
                state.observed_state["proof_scope"] = ""
                if old_plan:
                    state.expected_state.setdefault("semantic_plan_history", []).append(old_plan)
                if decision.plan is not None:
                    decision.plan["source_text"] = revised
                    decision.plan["objective"] = revised
                    decision.plan["metadata"]["goal_revision"] = revision
                    state.expected_state["semantic_contract"] = decision.plan
                    goal_control["unresolved"] = list(decision.plan.get("unresolved") or [])
                else:
                    # An obsolete abstract plan must not keep directing tools.
                    state.expected_state.pop("semantic_contract", None)
                state.observed_state["plan_evidence_history"] = {
                    **state.observed_state.get("plan_evidence_history", {}),
                    str(revision - 1): dict(state.observed_state.get("plan_evidence") or {}),
                }
                state.observed_state["plan_evidence"] = {}
            plan = state.expected_state.get("semantic_contract") or {}
            known = {f["id"]: f for f in facts if isinstance(f, dict) and f.get("id")}
            unresolved = list(plan.get("unresolved") or goal_control.get("unresolved") or [])
            resolutions = state.observed_state.setdefault("resolved_preconditions", [])
            for resolution in decision.resolved:
                fact = known.get(resolution["fact_id"])
                if (resolution["question"] in unresolved and fact
                        and fact.get("kind") != "expired_reference"):
                    unresolved.remove(resolution["question"])
                    resolutions.append(dict(resolution))
            if plan:
                plan["unresolved"] = unresolved
            goal_control["unresolved"] = unresolved
            state.expected_state["goal_control"] = goal_control
            # A provider callback may have recorded usage while interpreting.
            # Preserve its counters using the latest optimistic version.
            latest, current_version = self.context_store.load(mid)
            for key in ("model_usage", "model_calls", "api_usage", "execution_usage"):
                if key in latest.observed_state:
                    state.observed_state[key] = latest.observed_state[key]
            self.context_store.save(state, expected_version=current_version)
            self._audit(mid, "mission.turn_interpreted", {
                "kind": decision.kind, "goal_revision": goal_control["revision"],
                "permissions_granted": False,
            })
            if log:
                log("[MISSION_CONTROL] kind=" + decision.kind + " goal_revision=" + str(goal_control["revision"]))

    def _mission_status_text(self, mid):
        snap = self.mission_snapshot(mid)
        return ("Objectif actuel : " + snap["user_goal"] + "\n"
                + "Révision : " + str(snap["goal_revision"]) + "\n"
                + "État réel : " + snap["status"] + "\n"
                + "Objectif vérifié : " + ("oui" if snap["goal_verified"] else "non"))

    def _consume_turn_control(self, user_text, *, log=None):
        from .active_mission_supervisor import execution_scope_active
        if execution_scope_active():
            # Internal step text is not a new human turn. The approved plan
            # and its existing supervisor remain authoritative.
            return None
        if self._prepared_turn is None or self._prepared_turn[1] != str(user_text):
            self.prepare_mission_turn(user_text, log=log)
        self._prepared_turn = None
        text, self._control_reply = self._control_reply, None
        if text:
            from .agent_runtime import AgentTurnResult
            return AgentTurnResult(text=text, actions=())
        return None

    def _note_execution_usage(self, mid, *, request=False, usage=None, provider=""):
        """Actual API attempt/counter telemetry, never invented pricing."""
        state, version = self.context_store.load(mid)
        stats = state.observed_state.setdefault("execution_usage", {
            "model_requests": 0, "usage_responses": 0, "missing_usage_responses": 0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "estimated_cost": None, "providers": [],
        })
        if request:
            stats["model_requests"] += 1
        else:
            if usage is None:
                stats["missing_usage_responses"] += 1
            else:
                stats["usage_responses"] += 1
                for target, source in (("input_tokens", "prompt_tokens"),
                                       ("output_tokens", "completion_tokens"),
                                       ("total_tokens", "total_tokens")):
                    value = getattr(usage, source, None)
                    if type(value) is int and value >= 0:
                        stats[target] += value
            if provider and provider not in stats["providers"]:
                stats["providers"].append(str(provider)[:60])
        self.context_store.save(state, expected_version=version)

    def _record_tool_approval(self, mid, name, arguments):
        """Record an existing executor approval; never grant/reuse a token."""
        state, version = self.context_store.load(mid)
        self._initialize_goal_control(state)
        control = state.expected_state["goal_control"]
        digest = hashlib.sha256(json.dumps(
            {"tool": name, "arguments": arguments}, sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if any(p.get("arguments_digest") == digest and p.get("goal_revision") == control["revision"]
               for p in control["permissions"]):
            return
        control["permissions"].append({
            "tool": name, "arguments_digest": digest,
            "goal_revision": control["revision"], "source": "existing_runtime_explicit_approval",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "reusable": False,
        })
        control["permissions"] = control["permissions"][-32:]
        self.context_store.save(state, expected_version=version)

    def resume_mission(self, mission_id: str) -> MissionContext:
        """Attach an existing mission; any in-flight action needs manual review."""
        with self._lock:
            if self._active_mission_id is not None:
                raise RuntimeError("mission_already_active")
            loaded = self.context_store.load(mission_id)
            graph = self.orchestrator.graph(mission_id)
            if loaded is None or graph is None:
                raise KeyError("mission_checkpoint_not_found")
            context, version = loaded
            if context.user_id != self.owner_user_id:
                raise PermissionError("mission_owner_mismatch")
            if context.status in (MissionStatus.COMPLETED, MissionStatus.FAILED):
                raise RuntimeError("mission_terminal")
            interrupted = (
                context.status == MissionStatus.RUNNING
                or any(node.status == TaskStatus.RUNNING for node in graph.nodes())
                or (context.observed_state.get("active_supervisor") or {}).get("state")
                in {"ACTING", "OBSERVING", "VERIFYING", "WAITING_APPROVAL"}
            )
            if interrupted:
                for node in graph.nodes():
                    if node.status == TaskStatus.RUNNING:
                        node.status = TaskStatus.WAITING_EXTERNAL
                        node.error = "interrupted_outcome_unknown"
                context.status = MissionStatus.BLOCKED
                context.pending_action = {
                    "reason": "interrupted_outcome_unknown",
                    "manual_review_required": True,
                }
                if context.observed_state.get("active_supervisor"):
                    context.observed_state["active_supervisor"]["state"] = "BLOCKED"
                    context.observed_state["active_supervisor"].pop("approval_digest", None)
                self.graph_store.save(graph)
                self.context_store.save(context, expected_version=version)
                self._audit(
                    mission_id,
                    "mission.recovery_required",
                    {"outcome": "unknown", "manual_review_required": True},
                )
            self._active_mission_id = str(mission_id)
            self._explicit = True
            return context

    def register_semantic_plan(self, contract: MissionContract) -> None:
        """Validate and persist a goal plan without generating/executing tools.

        An authorized controller or the existing model may supply a structured
        contract in the future. This method itself never calls an LLM.
        """
        contract.validate()
        if len(contract.steps) > 24:
            raise ValueError("mission_plan_too_large")
        with self._lock:
            if self._active_mission_id is None:
                raise RuntimeError("no_active_mission")
            loaded = self.context_store.load(self._active_mission_id)
            if loaded is None:
                raise KeyError("mission_checkpoint_not_found")
            state, version = loaded
            if state.status in (MissionStatus.COMPLETED, MissionStatus.FAILED):
                raise RuntimeError("mission_terminal")
            if state.status == MissionStatus.BLOCKED:
                raise RuntimeError("mission_recovery_requires_review")
            if state.user_id != self.owner_user_id:
                raise PermissionError("mission_owner_mismatch")
            if state.expected_state.get("semantic_contract") is not None:
                raise RuntimeError("mission_plan_already_registered")
            state.expected_state["semantic_contract"] = contract.as_dict()
            state.observed_state["plan_evidence"] = {}
            from .mission_evidence import reconcile_preconditions
            reconcile_preconditions(state)
            self.context_store.save(state, expected_version=version)
            self._audit(
                state.mission_id, EventKind.INTENT_RESOLVED,
                {"steps": len(contract.steps),
                 "required_evidence": len(contract.completion_requirements()),
                 "tool_execution": False},
            )

    def generate_semantic_plan(self, *, mission_id: str | None = None) -> dict[str, Any]:
        """Explicit one-request model draft; no tools, no task dispatch, no proof.

        The worker thread is the only caller and the existing convergence
        store verifies mission ownership and the absence of a prior plan.
        """
        with self._lock:
            mid = str(mission_id or self._active_mission_id or "")
            if not mid or mid != self._active_mission_id:
                raise RuntimeError("planning_requires_active_mission")
            loaded = self.context_store.load(mid)
            if loaded is None:
                raise KeyError("mission_checkpoint_not_found")
            state, _version = loaded
            if state.user_id != self.owner_user_id:
                raise PermissionError("mission_owner_mismatch")
            if state.status in (MissionStatus.COMPLETED, MissionStatus.FAILED):
                raise RuntimeError("mission_terminal")
            if state.status == MissionStatus.BLOCKED:
                raise RuntimeError("mission_recovery_requires_review")
            if state.expected_state.get("semantic_contract") is not None:
                raise RuntimeError("mission_plan_already_registered")

            from .llm_mission_planner import generate_draft

            # Unlike a normal agent turn, this does NOT supply or execute
            # native/MCP tools, even when the model proposes to use one.
            proposal = generate_draft(self.delegate, state.user_goal)
            self.register_semantic_plan(proposal)
            return {
                "mission_id": mid,
                "status": "llm_plan_registered_unverified",
                "step_count": len(proposal.steps),
                "evidence_count": len(proposal.completion_requirements()),
                "unresolved_count": len(proposal.unresolved),
                "model_request_count": 1,
                "tool_execution": False,
                "goal_verified": False,
                "review": self.review_mission(mid),
            }

    def clarify_semantic_plan(self, answer: str) -> dict[str, Any]:
        """Revise an unapproved draft; never revise after any execution or proof.

        The prior draft and user clarification stay in the owner-scoped
        checkpoint. The LLM only authors a fresh, untrusted plan: it executes
        no tools and grants no permissions or evidence.
        """
        response = str(answer or "").strip()
        if not 1 <= len(response) <= 2000:
            raise ValueError("mission_clarification_length_invalid")
        with self._lock:
            mid = self._active_mission_id
            if not mid:
                raise RuntimeError("no_active_mission")
            loaded = self.context_store.load(mid)
            graph = self.orchestrator.graph(mid)
            if loaded is None or graph is None:
                raise RuntimeError("mission_checkpoint_not_found")
            state, version = loaded
            if state.user_id != self.owner_user_id:
                raise PermissionError("mission_owner_mismatch")
            if state.status in (MissionStatus.BLOCKED, MissionStatus.COMPLETED, MissionStatus.FAILED):
                raise RuntimeError("mission_revision_requires_review")
            if state.observed_state.get("active_supervisor"):
                raise RuntimeError("mission_already_approved")
            if graph.nodes() or state.observed_state.get("plan_evidence"):
                raise RuntimeError("mission_plan_has_execution_or_evidence")
            previous = state.expected_state.get("semantic_contract")
            if not isinstance(previous, dict):
                raise RuntimeError("mission_plan_not_registered")
            if not previous.get("unresolved"):
                raise RuntimeError("mission_has_no_clarifications")
            versions = list(state.expected_state.get("semantic_plan_history") or [])
            if len(versions) >= 8:
                raise RuntimeError("mission_plan_revision_limit")

            from .llm_mission_planner import generate_draft
            # One bounded request; if it fails the old draft stays unchanged.
            draft = generate_draft(
                self.delegate, state.user_goal,
                previous_plan=previous, clarification=response,
            )
            draft.validate()
            versions.append(previous)
            revised = draft.as_dict()
            revised.setdefault("metadata", {})["revision"] = len(versions)
            state.expected_state["semantic_plan_history"] = versions
            state.expected_state["semantic_contract"] = revised
            clarifications = list(state.expected_state.get("mission_clarification_answers") or [])
            clarifications.append({"answer": response, "revision": len(versions)})
            state.expected_state["mission_clarification_answers"] = clarifications
            state.observed_state["plan_evidence"] = {}
            self.context_store.save(state, expected_version=version)
            self._audit(mid, "mission.plan_revised", {
                "revision": len(versions), "steps": len(draft.steps),
                "unresolved": len(draft.unresolved), "tool_execution": False,
            })
            return {
                "mission_id": mid, "status": "plan_revised_unverified",
                "revision": len(versions),
                "step_count": len(draft.steps),
                "unresolved_count": len(draft.unresolved),
                "model_request_count": 1,
                "tool_execution": False, "goal_verified": False,
            }

    def register_goal_evidence(self, requirement: str, *, proof_ref: str) -> None:
        """Trusted external verifier records evidence against a planned goal."""
        with self._lock:
            if not str(proof_ref or "").strip():
                raise ValueError("evidence_reference_required")
            if self._active_mission_id is None:
                raise RuntimeError("no_active_mission")
            loaded = self.context_store.load(self._active_mission_id)
            if loaded is None:
                raise KeyError("mission_checkpoint_not_found")
            state, version = loaded
            if state.user_id != self.owner_user_id:
                raise PermissionError("mission_owner_mismatch")
            if state.status == MissionStatus.BLOCKED:
                raise RuntimeError("mission_recovery_requires_review")
            plan = state.expected_state.get("semantic_contract")
            if not isinstance(plan, dict):
                raise RuntimeError("mission_plan_not_registered")
            required = {
                str(evidence)
                for step in plan.get("steps", ())
                for evidence in step.get("required_evidence", ())
            }
            if str(requirement) not in required:
                raise ValueError("evidence_requirement_not_in_plan")
            recorded = dict(state.observed_state.get("plan_evidence") or {})
            recorded[str(requirement)] = str(proof_ref)[:500]
            state.observed_state["plan_evidence"] = recorded
            state.proof_refs.append(str(proof_ref)[:500])
            self.context_store.save(state, expected_version=version)
            self._audit(
                state.mission_id, EventKind.PROOF,
                {"source": "trusted_external_verifier",
                 "requirement": str(requirement)[:200],
                 "proof_ref": str(proof_ref)[:500]},
            )

    def complete_mission(self, *, proof_ref: str) -> None:
        """Explicit external goal-proof gate, never called from a tool success."""
        with self._lock:
            if not str(proof_ref or "").strip():
                raise ValueError("independent_goal_proof_required")
            if self._active_mission_id is None:
                raise RuntimeError("no_active_mission")
            loaded = self.context_store.load(self._active_mission_id)
            if loaded is None:
                raise KeyError("mission_checkpoint_not_found")
            context, version = loaded
            if context.status == MissionStatus.BLOCKED:
                raise RuntimeError("mission_recovery_requires_review")
            contract = context.expected_state.get("semantic_contract")
            if isinstance(contract, dict):
                required = {
                    str(item)
                    for step in contract.get("steps", ())
                    for item in step.get("required_evidence", ())
                }
                recorded = set(context.observed_state.get("plan_evidence") or {})
                if required - recorded:
                    raise RuntimeError("mission_plan_evidence_missing")
                if contract.get("unresolved"):
                    raise RuntimeError("mission_semantic_ambiguity_unresolved")
            context.proof_refs.append(str(proof_ref)[:500])
            context.observed_state["goal_verified"] = True
            context.status = MissionStatus.COMPLETED
            context.current_step_id = None
            context.current_step = ""
            self.context_store.save(context, expected_version=version)
            self._audit(
                context.mission_id,
                EventKind.PROOF,
                {"proof_ref": str(proof_ref)[:500], "source": "explicit_trusted_caller"},
            )
            self._audit(
                context.mission_id, EventKind.MISSION_COMPLETED,
                {"goal_verified": True},
            )
            self._set_audit_status(context.mission_id, context.status)
            self._active_mission_id = None
            self._explicit = False

    def resolve_recovery(
        self, *, verified_outcome: str, proof_ref: str
    ) -> MissionContext:
        """Manually reconcile an interrupted/uncertain action before continuing.

        Never reruns the prior call. The trusted UI/operator must independently
        verify whether the external effect happened; LLM text is not evidence.
        """
        with self._lock:
            if self._active_mission_id is None:
                raise RuntimeError("no_active_mission")
            if verified_outcome not in ("completed", "not_executed", "cancel"):
                raise ValueError("unsupported_recovery_outcome")
            if not str(proof_ref or "").strip():
                raise ValueError("recovery_requires_independent_proof")
            mission_id = self._active_mission_id
            loaded = self.context_store.load(mission_id)
            graph = self.orchestrator.graph(mission_id)
            if loaded is None or graph is None:
                raise KeyError("mission_checkpoint_not_found")
            state, version = loaded
            if state.user_id != self.owner_user_id:
                raise PermissionError("mission_owner_mismatch")
            if (
                state.status != MissionStatus.BLOCKED
                or not state.pending_action.get("manual_review_required")
            ):
                raise RuntimeError("mission_not_awaiting_manual_recovery")
            pending = [
                node for node in graph.nodes()
                if node.status in (
                    TaskStatus.RUNNING, TaskStatus.WAITING_EXTERNAL, TaskStatus.FAILED
                )
            ]
            if verified_outcome == "cancel":
                for node in pending:
                    node.status = TaskStatus.CANCELLED
                    node.error = "mission_cancelled_after_review"
                state.status = MissionStatus.FAILED
            else:
                for node in pending:
                    node.status = (
                        TaskStatus.COMPLETED if verified_outcome == "completed"
                        else TaskStatus.CANCELLED
                    )
                    node.result = {
                        "recovery_verified": True,
                        "outcome": verified_outcome,
                    }
                    node.error = ""
                state.status = MissionStatus.WAITING_EXTERNAL
            state.proof_refs.append(str(proof_ref)[:500])
            state.pending_action = {}
            state.current_step_id = None
            state.current_step = ""
            state.observed_state["last_recovery"] = verified_outcome
            supervised = state.observed_state.get("active_supervisor")
            if supervised:
                used = int(supervised["usage"].get("recoveries", 0))
                total = int(supervised["usage"].get("total_units", 0))
                if verified_outcome != "cancel" and (used >= supervised["limits"]["recoveries"]
                        or total >= supervised["limits"]["total_units"]):
                    raise RuntimeError("mission_recovery_budget_exhausted")
                if verified_outcome != "cancel":
                    supervised["usage"]["recoveries"] = used + 1
                    supervised["usage"]["total_units"] = total + 1
                step_id = supervised.get("step_id")
                if step_id and verified_outcome == "completed":
                    supervised["executed_steps"] = list(dict.fromkeys([
                        *supervised.get("executed_steps", []), step_id,
                    ]))
                elif step_id and verified_outcome == "not_executed":
                    supervised["executed_steps"] = [s for s in supervised.get("executed_steps", []) if s != step_id]
                supervised.pop("approval_digest", None)
                supervised["state"] = (
                    "FAILED" if verified_outcome == "cancel" else
                    "RECOVERING" if verified_outcome == "completed" else "READY"
                )
            # Resolving an individual action NEVER verifies the whole goal.
            state.observed_state["goal_verified"] = False
            self.graph_store.save(graph)
            self.context_store.save(state, expected_version=version)
            self._audit(
                mission_id, EventKind.PROOF,
                {"source": "explicit_trusted_recovery", "outcome": verified_outcome,
                 "proof_ref": str(proof_ref)[:500]},
            )
            self._set_audit_status(mission_id, state.status)
            if verified_outcome == "cancel":
                self.detach_mission()
            return state

    def _audit(self, mission_id: str, kind: EventKind | str, payload: dict) -> None:
        """Best-effort, privacy-minimal telemetry. Never triggers a retry."""
        try:
            self.journal.append_event(
                mission_id=mission_id, kind=kind, payload=payload,
                agent_id="interaction", component="runtime_convergence",
            )
        except Exception:
            # An audit write cannot undo or repeat an already executed action.
            # Mission state remains canonical in MissionContextStore.
            pass

    def _set_audit_status(self, mission_id: str, status: MissionStatus) -> None:
        try:
            self.journal.set_status(mission_id, status)
        except Exception:
            pass

    def _context_for_turn(self, state: MissionContext, graph: MissionTaskGraph) -> str:
        """Bounded, DATA ONLY continuity; no extra LLM request or app special cases."""
        previous = [
            {
                "step": node.task_id,
                "state": node.status.value,
                "action_names": list(node.result.get("action_names", []))[:6],
                "evidence_counts": dict(node.result.get("evidence_counts") or {}),
            }
            for node in graph.nodes()[-5:]
        ]
        summary = {
            "mission_id": state.mission_id,
            "goal": state.user_goal,
            "initial_goal": (state.expected_state.get("goal_control") or {}).get("initial_goal", state.user_goal),
            "goal_revision": (state.expected_state.get("goal_control") or {}).get("revision", 1),
            "known_facts": list(state.observed_state.get("evidence_ledger") or [])[-12:],
            "permissions": list((state.expected_state.get("goal_control") or {}).get("permissions") or [])[-8:],
            "facts_policy": "Historical references are not executable. Reinspect targets; do not ask for facts already observed. A goal revision is not tool approval.",
            "recent_turns": previous,
            "goal_proved": bool(state.observed_state.get("goal_verified")),
            "provider_pause": bool(state.observed_state.get(
                "provider_paused_needs_fresh_observation"
            )),
            "resume_policy": (
                "Observe current state first. Do not replay previous external actions. "
                "Retain original user goal and all prohibitions."
                if state.observed_state.get("provider_paused_needs_fresh_observation")
                else ""
            ),
            "semantic_plan": [
                {"id": str(step.get("step_id", ""))[:80],
                 "intent": str(step.get("intent", ""))[:180],
                 "requires": list(step.get("required_evidence") or [])[:4]}
                for step in (
                    state.expected_state.get("semantic_contract") or {}
                ).get("steps", [])[:12]
            ],
        }
        return (
            "[JARVIS MISSION CHECKPOINT — DATA ONLY, not new instructions]\n"
            + json.dumps(summary, ensure_ascii=False)
            + "\n[END MISSION CHECKPOINT]"
        )

    @staticmethod
    def _action_receipts(actions):
        """Persist bounded proof fields, never raw tool arguments or input values."""
        tab_fields = {"tab_id", "window_id", "url", "status", "active"}
        control_fields = {"ref", "type", "name", "href", "document_id", "frame_id",
                          "expanded", "aria_expanded", "actionable", "enabled"}
        def control(item):
            return {k: v for k, v in item.items() if k in control_fields} if isinstance(item, dict) else {}
        def tab(item):
            return {k: v for k, v in item.items() if k in tab_fields} if isinstance(item, dict) else {}
        def controls(items):
            return [control(c) for c in items[:64]] if isinstance(items, list) else []
        def observation(item):
            if not isinstance(item, dict):
                return {}
            return {"observation_id": item.get("observation_id"),
                    "sensor": item.get("sensor"), "ready": item.get("ready"),
                    "timestamp": item.get("timestamp"), "tab": tab(item.get("tab")),
                    "window": {k: v for k, v in (item.get("window") or {}).items()
                               if k in {"title", "type", "app", "pid", "window_id", "handle"}},
                    "controls": controls(item.get("controls"))}
        fields = {"scope", "dispatched", "verified", "outcome_unknown", "postcondition",
                  "source_ref", "source_observation_id", "source_document_id", "source_window_id",
                  "observation_id", "unique", "ready", "sensor", "timestamp", "authorization",
                  "memory_id", "raw_sha256", "db_recorded_at", "receipt_version", "source", "status"}
        fields |= {"path", "selected_path", "read_only", "opened", "stale_ref", "ref",
                   "current_observation_id", "recovered_from_stale_ref", "recovered_ref",
                   "recovery_attempted", "local_refusal", "reason", "guard", "precondition_failed"}
        receipts = []
        for index, action in enumerate(actions):
            detail = str(getattr(action, "detail", "") or "")
            try:
                original = json.loads(detail)
            except (ValueError, TypeError):
                original = None
            if (original is None and getattr(action, "success", False)
                    and action.name in {"open_file", "open_folder"}):
                # Legacy local launcher returns its actual selected path.
                from pathlib import PureWindowsPath, PurePosixPath
                if PureWindowsPath(detail).is_absolute() or PurePosixPath(detail).is_absolute():
                    original = {"path": detail, "dispatched": True, "opened": True,
                                "outcome_unknown": False, "verified": False}
            if not isinstance(original, dict) and not getattr(action, "success", False):
                from .mission_outcome_policy import READ_ONLY_TOOLS, failure_requires_review
                if action.name in READ_ONLY_TOOLS and not failure_requires_review((action,)):
                    original = {"reason": "read_only_sensor_failed"}
                elif action.name == "open_url" and detail == "open_url_blocked_for_existing_browser_context":
                    original = {"reason": detail, "dispatched": False, "outcome_unknown": False}
            if isinstance(original, dict):
                data = {k: v for k, v in original.items() if k in fields}
                data.update(tab(original))
                for key in ("tab", "independent_readback"):
                    if key in original:
                        data[key] = tab(original[key])
                for key in ("post_observation", "observation"):
                    if key in original:
                        data[key] = observation(original[key])
                if "controls" in original:
                    data["controls"] = controls(original["controls"])
                if "window" in original:
                    data["window"] = observation(original)["window"]
                for key in ("matches", "available_controls"):
                    if key in original:
                        data[key] = controls(original[key])
                detail = json.dumps(data, ensure_ascii=False)
            # Oversized/unstructured evidence cannot become a certificate.
            usable = len(detail) <= 48000 and index < 128 and isinstance(original, dict)
            receipts.append({"schema_version": 1, "sequence": index,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "name": str(getattr(action, "name", "")),
                "success": bool(getattr(action, "success", False)),
                "usable": usable, "detail": detail if usable else ""})
        return receipts

    @staticmethod
    def _proof_actions(graph):
        """Recover ordered receipts; incomplete legacy checkpoints fail closed."""
        from .native_tools import AgentActionResult
        actions = []
        for node in graph.nodes():
            count = node.result.get("action_count", 0)
            if type(count) is not int or count < 0:
                return (), False
            if not count:
                continue
            receipts = node.result.get("action_receipts")
            if (not isinstance(receipts, list) or len(receipts) != count
                    or len(actions) + count > 128):
                return (), False
            for index, item in enumerate(receipts):
                if (not isinstance(item, dict) or item.get("schema_version") != 1
                        or type(item.get("schema_version")) is not int
                        or type(item.get("sequence")) is not int
                        or item.get("sequence") != index or item.get("usable") is not True
                        or type(item.get("success")) is not bool
                        or not isinstance(item.get("detail"), str)
                        or not isinstance(item.get("name"), str)):
                    return (), False
                actions.append(AgentActionResult(item["name"], item["success"],
                                                "Persisted local tool receipt", item["detail"]))
        return tuple(actions), True

    @staticmethod
    def _certificate_allowed(state):
        control = state.expected_state.get("goal_control") or {}
        if control.get("unresolved"):
            return False
        contract = state.expected_state.get("semantic_contract")
        if not isinstance(contract, dict):
            return True
        return ((contract.get("metadata") or {}).get("origin") == "same_provider_no_tools_draft"
                and not contract.get("unresolved"))

    def _observe_routes(self, action_names: list[str], goal: str) -> list[dict]:
        """Compare real tools with registered agents; no authority to dispatch."""
        manifest_list = DEFAULT_CAPABILITY_REGISTRY.agents()
        available = tuple(m.agent_id for m in manifest_list) + ("interaction",)
        last_agent = ""
        decisions: list[dict] = []
        for name in action_names:
            prefix = name.split("_", 1)[0] if "_" in name else ""
            candidates = tuple(
                manifest.agent_id for manifest in manifest_list
                if (
                    name in manifest.allowed_tools
                    or (
                        prefix in ("browser", "computer")
                        and manifest.agent_id == (
                            "browser" if prefix == "browser" else "windows"
                        )
                    )
                )
            )
            context = AgentRoutingContext(
                user_goal=str(goal or "")[:650],
                current_tool=name, previous_agent=last_agent,
                available_agents=available,
            )
            decision = self.agent_router.route_contextual(
                context, candidate_agents=candidates
            )
            decisions.append({
                "tool": name,
                "candidate_agents": list(decision.candidate_agents),
                "proposed_agent": decision.agent_id,
                "reason": decision.reason,
                "needs_review": decision.needs_review,
                "authoritative": False,
            })
            if not decision.needs_review:
                last_agent = decision.agent_id
        return decisions

    def mission_snapshot(self, mission_id: str) -> dict[str, Any]:
        """Inspect durable progress without asking the model or running tools."""
        loaded = self.context_store.load(mission_id)
        graph = self.orchestrator.graph(mission_id)
        if loaded is None or graph is None:
            raise KeyError("mission_checkpoint_not_found")
        state, version = loaded
        if state.user_id != self.owner_user_id:
            raise PermissionError("mission_owner_mismatch")
        self._initialize_goal_control(state)
        control = state.expected_state["goal_control"]
        result = {
            "mission_id": state.mission_id,
            "user_goal": state.user_goal,
            "initial_goal": control["initial_goal"],
            "goal_revision": control["revision"],
            "goal_history": list(control["history"]),
            "permissions": list(control["permissions"]),
            "evidence_ledger": list(state.observed_state.get("evidence_ledger") or []),
            "execution_usage": dict(state.observed_state.get("execution_usage") or {}),
            "open_questions": list(control.get("unresolved") or []),
            "suspension_reason": str(state.pending_action.get("reason") or state.observed_state.get("last_pause_reason") or ""),
            "status": state.status.value,
            "version": version,
            "goal_verified": bool(state.observed_state.get("goal_verified")),
            "proof_scope": str(state.observed_state.get("proof_scope") or ""),
            "proof_receipt_count": int(state.observed_state.get("proof_receipt_count") or 0),
            "semantic_plan": state.expected_state.get("semantic_contract"),
            "plan_evidence": dict(state.observed_state.get("plan_evidence") or {}),
            "active_supervisor": dict(state.observed_state.get("active_supervisor") or {}),
            "manual_review_required": bool(
                state.pending_action.get("manual_review_required")
            ),
            "task_summary": graph.summary(),
            "tasks": [
                {"id": node.task_id, "status": node.status.value, "agent_id": node.agent_id,
                 "action_names": list(node.result.get("action_names", [])),
                 "agent_routes": list(node.result.get("agent_routes", [])),
                 "error": node.error}
                for node in graph.nodes()
            ],
        }
        from .semantic_goal_supervisor import evaluate_mission

        result["supervisor"] = evaluate_mission(result)
        return result

    def review_mission(self, mission_id: str | None = None) -> dict[str, Any]:
        """Read only; owner-scoped, no model/tool calls or persisted mutation."""
        target = str(mission_id or self._active_mission_id or "")
        if not target:
            raise RuntimeError("no_active_mission")
        return self.mission_snapshot(target)["supervisor"]

    def propose_mission_capabilities(self, mission_id: str | None = None) -> dict[str, Any]:
        """Owner-scoped read-only specialist/MCP plan; NEVER delegates or calls a model."""
        target = str(mission_id or self._active_mission_id or "")
        if not target:
            raise RuntimeError("no_active_mission")
        saved = self.mission_snapshot(target)  # ownership is enforced here
        from .capability_planner import propose_capabilities

        tools = []
        try:
            from .mcp_server_registry import MCPRegistry
            registry = MCPRegistry()
            if registry.runtime_enabled():
                tools = registry.exposed_tools()
        except (OSError, ValueError, KeyError, TypeError):
            # Broken/absent MCP config never prevents native mission review.
            tools = []
        return propose_capabilities(saved, allowed_mcp=tools)

    def detach_mission(self) -> None:
        """Stop tracking locally without changing persistent state or replaying."""
        with self._lock:
            self._active_mission_id = None
            self._explicit = False

    def approve_supervised_plan(self, *, digest: str, rules=None, limits=None, assignments=None):
        if self.active_supervisor is None:
            raise RuntimeError("active_supervisor_not_enabled")
        return self.active_supervisor.approve(digest=digest, rules=rules, limits=limits, assignments=assignments)

    def advance_supervised_mission(self, user_text=None, *, log=None, phase=None, stop_event=None):
        if self.active_supervisor is None:
            raise RuntimeError("active_supervisor_not_enabled")
        return self.active_supervisor.advance(user_text, log=log, phase=phase, stop_event=stop_event)

    def run_supervised_mission(self, *, max_steps=24, stop_event=None, progress=None, log=None, phase=None):
        if self.active_supervisor is None:
            raise RuntimeError("active_supervisor_not_enabled")
        return self.active_supervisor.run_until_pause(max_steps=max_steps, stop_event=stop_event,
                                                    progress=progress, log=log, phase=phase)

    @staticmethod
    def _mission_information_requested(user_text: str) -> bool:
        """Narrow, local read-only intent for mission status, not an action router."""
        text = str(user_text or "").strip().casefold()
        if not text or len(text) > 260:
            return False
        if not any(word in text for word in ("mission", "plan", "étape", "etape")):
            return False
        return text.startswith((
            "c quoi", "c'est quoi", "quelle est", "quel est",
            "où en", "ou en", "montre", "affiche", "résume",
            "resume", "rappelle", "statut", "état", "etat",
            "what is", "what's", "show", "status",
        ))

    def _mission_information_reply(self, user_text: str):
        """Consult an attached checkpoint even when execution is blocked.

        Neither the model nor any browser/desktop tool is invoked.
        """
        if self._active_mission_id is None or not self._mission_information_requested(user_text):
            return None
        with self._lock:
            snap = self.mission_snapshot(self._active_mission_id)
        review = snap.get("supervisor") or {}
        steps = review.get("steps") or []
        questions = review.get("unresolved") or []
        lines = [
            "Mission : " + str(snap.get("user_goal") or "")[:400],
            "État réel : " + str(snap.get("status") or "inconnu"),
            "Objectif vérifié : " + ("oui" if snap.get("goal_verified") else "non"),
            "Plan : " + str(len(steps)) + " étape(s).",
        ]
        for index, step in enumerate(steps[:6], 1):
            lines.append(
                str(index) + ". " + str(step.get("intent") or "")[:150]
                + " — " + str(step.get("state") or "non vérifié")
            )
        if questions:
            lines.append("À clarifier : " + " | ".join(str(q) for q in questions[:4])[:800])
        if snap.get("manual_review_required"):
            lines.append("Reprise des actions bloquée : vérification manuelle requise.")
        from .agent_runtime import AgentTurnResult
        return AgentTurnResult(text="\n".join(lines), actions=())

    def run(self, user_text: str, *, log=None, phase=None):
        controlled = self._consume_turn_control(user_text, log=log)
        if controlled is not None:
            return controlled
        info = self._mission_information_reply(user_text)
        if info is not None:
            return info
        result = self._supervised_conversation(user_text, log=log, phase=phase)
        if result is not None:
            return result
        return self._run(user_text, log=log, phase=phase)

    def run_with_context(self, user_text: str, context: str, *, log=None, phase=None):
        controlled = self._consume_turn_control(user_text, log=log)
        if controlled is not None:
            return controlled
        info = self._mission_information_reply(user_text)
        if info is not None:
            return info
        result = self._supervised_conversation(user_text, context=context, log=log, phase=phase)
        if result is not None:
            return result
        return self._run(user_text, context=context, log=log, phase=phase)

    def _supervised_conversation(self, user_text, *, context="", log=None, phase=None):
        if self.active_supervisor is None or self._active_mission_id is None:
            return None
        from .active_mission_supervisor import execution_scope_active, conversation_stop_event
        if execution_scope_active():
            return None
        with self._lock:
            state, _ = self.context_store.load(self._active_mission_id)
            if not state.observed_state.get("active_supervisor"):
                return None
            report = self.active_supervisor.run_until_pause(user_text, context=context, log=log, phase=phase,
                stop_event=conversation_stop_event(),
                progress=(lambda event: phase("mission_progress:" + json.dumps(event, ensure_ascii=False))) if phase else None)
            from .agent_runtime import AgentTurnResult
            turn = report.get("_turn_result")
            text = report.get("text") or ("Objectif verifie." if report["goal_verified"] else "Preuve independante attendue.")
            return AgentTurnResult(text=text, actions=tuple(getattr(turn, "actions", ()) or ()),
                end_session=bool(getattr(turn, "end_session", False)), should_exit=bool(getattr(turn, "should_exit", False)))

    def _run(self, user_text: str, *, context=None, log=None, phase=None):
        # Lock also prevents two threads executing a single mission step twice.
        with self._lock:
            implicit = self._active_mission_id is None
            if implicit:
                self.begin_mission(user_text)
                self._explicit = False
            mission_id = self._active_mission_id
            loaded = self.context_store.load(mission_id)
            graph = self.orchestrator.graph(mission_id)
            if loaded is None or graph is None:
                raise RuntimeError("mission_checkpoint_not_found")
            state, version = loaded
            if state.status == MissionStatus.BLOCKED:
                raise RuntimeError("mission_recovery_requires_review")
            if state.status in (MissionStatus.COMPLETED, MissionStatus.FAILED):
                raise RuntimeError("mission_terminal")
            index = len(graph.nodes()) + 1
            if state.user_id != self.owner_user_id:
                raise PermissionError("mission_owner_mismatch")
            previous_actions, previous_complete = self._proof_actions(graph)
            task_id = f"turn_{index:05d}"
            continuity_context = (
                self._context_for_turn(state, graph)
                if self._explicit
                else ""
            )
            # A cancelled unknown-effect attempt is never a prerequisite:
            # only independently settled prior work can be a dependency.
            settled = [
                n for n in graph.nodes()
                if n.status == TaskStatus.COMPLETED
            ]
            previous_step = settled[-1].task_id if settled else None
            from .active_mission_supervisor import delegation_context
            delegated = delegation_context()
            node = TaskNode(
                task_id=task_id,
                mission_id=mission_id,
                capability="delegation.live_turn" if delegated else "interaction.live_turn",
                agent_id=delegated.get("agent", "interaction"),
                status=TaskStatus.RUNNING,
                dependencies=({previous_step} if previous_step else set()),
                payload={"source": "live_runtime", "input_length": len(str(user_text)),
                         **({"delegation": delegated} if delegated else {})},
            )
            graph.add(node)
            self.graph_store.save(graph)
            state.status = MissionStatus.RUNNING
            state.current_step_id = task_id
            state.current_step = "interaction.live_turn"
            self.context_store.save(state, expected_version=version)
            self._set_audit_status(mission_id, MissionStatus.RUNNING)
            self._audit(
                mission_id, EventKind.USER_INPUT,
                {"task_id": task_id, "text_length": len(str(user_text))},
            )

            turn_evidence = {"grounding": []}
            try:
                extra_context = "\n\n".join(
                    item for item in (str(context or "").strip(), continuity_context)
                    if item
                )
                method = getattr(self.delegate, "run_with_context", None)
                from .browser_goal_proof import navigation_goal_scope
                with live_mission_usage_scope(self, mission_id), navigation_goal_scope(
                    state.user_goal, previous_actions,
                    certificate_allowed=previous_complete and self._certificate_allowed(state),
                ) as turn_evidence:
                    if extra_context and callable(method):
                        result = method(user_text, extra_context, log=log, phase=phase)
                    else:
                        result = self.delegate.run(user_text, log=log, phase=phase)
                from .agent_runtime import AgentTurnResult
                if turn_evidence["grounding"] and isinstance(result, AgentTurnResult):
                    from dataclasses import replace
                    result = replace(result, actions=tuple(turn_evidence["grounding"]) + tuple(result.actions))
            except Exception as exc:
                # A provider failure occurs at a synchronous model boundary:
                # no browser/Windows tool is in flight. Preserve tool receipts
                # already returned earlier in the turn, and allow the NEXT
                # user turn to re-observe the real state rather than requiring
                # a manual side-effect review for a quota/budget error.
                # Any unknown mutating tool effect still fails closed.
                from .agent_runtime import AgentRuntimeUnavailable
                from .mission_outcome_policy import failure_requires_review
                state, _ = self.context_store.load(mission_id)
                partial = tuple(getattr(self.delegate, "_last_partial_actions", ()) or ())
                partial = tuple(turn_evidence["grounding"]) + partial
                uncertain = getattr(getattr(self.delegate, "tools", None),
                                    "uncertain_scopes", None)
                provider_pause = (
                    isinstance(exc, AgentRuntimeUnavailable)
                    and not failure_requires_review(partial)
                    and not bool(uncertain)
                )
                node.status = TaskStatus.WAITING_EXTERNAL
                node.error = type(exc).__name__[:100]
                node.result = {
                    "action_count": len(partial),
                    "action_names": [str(getattr(a, "name", ""))[:100]
                                     for a in partial],
                    "provider_paused": provider_pause,
                    "action_receipts": self._action_receipts(partial),
                }
                from .mission_evidence import ledger_facts, merge_facts
                state.observed_state["evidence_ledger"] = merge_facts(
                    state.observed_state.get("evidence_ledger") or [],
                    ledger_facts(node.result["action_receipts"], task_id=task_id,
                                 goal_revision=(state.expected_state.get("goal_control") or {}).get("revision", 1)),
                )
                from .mission_evidence import reconcile_preconditions
                reconcile_preconditions(state)
                state.status = (
                    MissionStatus.WAITING_EXTERNAL if provider_pause
                    else MissionStatus.BLOCKED
                )
                state.pending_action = (
                    {} if provider_pause else {
                        "reason": "delegate_exception_outcome_unknown",
                        "manual_review_required": True,
                    }
                )
                state.observed_state["last_exception_type"] = type(exc).__name__[:100]
                state.observed_state["provider_paused_needs_fresh_observation"] = provider_pause
                state.current_step_id = None
                state.current_step = ""
                self.graph_store.save(graph)
                self.context_store.save(state)
                self._audit(
                    mission_id,
                    "mission.provider_paused" if provider_pause else "mission.uncertain_outcome",
                    {
                        "task_id": task_id,
                        "error_type": type(exc).__name__[:100],
                        "action_names": node.result["action_names"],
                        "no_automatic_action_replay": True,
                        "fresh_read_required": provider_pause,
                    },
                )
                self._set_audit_status(mission_id, state.status)
                if log and provider_pause:
                    log("[MISSION] provider_paused=1 resume=read_current_state_without_replay")
                if implicit:
                    self.detach_mission()
                raise

            # Registry gates may have persisted budgets/proofs during the turn.
            state, _ = self.context_store.load(mission_id)
            actions = tuple(getattr(result, "actions", ()) or ())
            failed = sum(not bool(getattr(action, "success", False)) for action in actions)
            # A failed read-only observation is not a failed side-effect.
            # Preserve genuine uncertain/mutating failures as BLOCKED.
            from .mission_outcome_policy import failure_requires_review
            needs_review = failure_requires_review(actions)
            recoverable_failure = bool(failed) and not needs_review
            node.status = (TaskStatus.WAITING_EXTERNAL if recoverable_failure or needs_review
                           else TaskStatus.FAILED if failed else TaskStatus.COMPLETED)
            action_names = [
                str(getattr(action, "name", "") or "")[:100]
                for action in actions
            ]
            routes = self._observe_routes(action_names, state.user_goal)
            from .mission_progress import action_evidence_counts
            evidence_counts = action_evidence_counts(actions)
            node.result = {
                "action_count": len(actions),
                "failed_action_count": failed,
                "action_names": action_names,
                "agent_routes": routes,
                "evidence_counts": evidence_counts,
                "action_receipts": self._action_receipts(actions),
            }
            from .mission_evidence import ledger_facts, merge_facts
            state.observed_state["evidence_ledger"] = merge_facts(
                state.observed_state.get("evidence_ledger") or [],
                ledger_facts(node.result["action_receipts"], task_id=task_id,
                             goal_revision=(state.expected_state.get("goal_control") or {}).get("revision", 1)),
            )
            from .mission_evidence import reconcile_preconditions
            reconcile_preconditions(state)
            state.observed_state["completed_turns"] = index
            state.observed_state["last_action_count"] = len(actions)
            state.observed_state["last_failed_action_count"] = failed
            state.observed_state["last_evidence_counts"] = evidence_counts
            state.observed_state["last_pause_reason"] = str(
                getattr(result, "pause_reason", "") or "")[:80]
            state.observed_state["last_routing_needs_review"] = any(
                route["needs_review"] for route in routes
            )
            from .browser_goal_proof import verify_browser_navigation_goal, has_navigation_readback
            from .memory_goal_proof import (
                foundation_memory_store,
                verify_explicit_memory_write,
            )
            proof_actions, receipts_complete = self._proof_actions(graph)
            state.observed_state["cumulative_evidence_counts"] = action_evidence_counts(proof_actions)
            state.observed_state["proof_receipt_count"] = len(proof_actions)
            ready_for_proof = (
                receipts_complete and self._certificate_allowed(state)
                and not failure_requires_review(proof_actions)
                and getattr(result, "pause_reason", "") in ("", "turn_budget_exhausted")
                and (not previous_actions or has_navigation_readback(actions)
                     or any(a.name == "remember_information" for a in actions))
            )
            verified_navigation = (
                verify_browser_navigation_goal(state.user_goal, proof_actions)
                if ready_for_proof else None
            )
            verified_memory = (
                verify_explicit_memory_write(
                    state.user_goal, proof_actions,
                    foundation_memory_store(self.delegate),
                )
                if ready_for_proof else None
            )
            contract = state.expected_state.get("semantic_contract")
            # A generated plan is advisory; explicit registered or
            # semantically unresolved plans keep their existing proof gate.
            if isinstance(contract, dict):
                metadata = contract.get("metadata") or {}
                if (metadata.get("origin") != "same_provider_no_tools_draft"
                        or contract.get("unresolved")):
                    verified_navigation = None
                    verified_memory = None
            verified_goal = verified_navigation or verified_memory
            if verified_navigation is not None:
                state.proof_refs.append(verified_navigation.proof_ref)
                state.observed_state["goal_verified"] = True
                state.observed_state["verified_navigation_url"] = verified_navigation.url
                state.observed_state["verified_navigation_target"] = verified_navigation.target
                state.observed_state["proof_scope"] = "browser_navigation_goal"
            elif verified_memory is not None:
                state.proof_refs.append(verified_memory.proof_ref)
                state.observed_state["goal_verified"] = True
                state.observed_state["verified_memory_id"] = verified_memory.memory_id
                state.observed_state["proof_scope"] = "durable_user_memory"
            else:
                state.observed_state["goal_verified"] = False
            # A successful click/write does not prove the intended recipient,
            # target, or overall mission. Explicit missions always await proof.
            state.status = (
                MissionStatus.COMPLETED if verified_goal is not None
                else MissionStatus.WAITING_EXTERNAL if recoverable_failure
                else MissionStatus.BLOCKED if failed or needs_review
                else MissionStatus.WAITING_EXTERNAL
                if actions or self._explicit
                else MissionStatus.COMPLETED
            )
            if recoverable_failure:
                state.pending_action = {}
                state.observed_state["recoverable_read_only_failure"] = True
                self._audit(mission_id, "mission.observation_recoverable", {
                    "task_id": task_id,
                    "failed_tools": [str(getattr(a, "name", ""))[:100]
                                     for a in actions if not a.success],
                    "next": "observe_fresh_state_without_replaying_mutations",
                })
            elif failed or needs_review:
                state.pending_action = {
                    "reason": "tool_reported_failure",
                    "manual_review_required": True,
                }
            state.current_step_id = None
            state.current_step = ""
            self.graph_store.save(graph)
            self.context_store.save(state)
            self._audit(
                mission_id, EventKind.OBSERVATION,
                {
                    "task_id": task_id,
                    "action_count": len(actions),
                    "failed_action_count": failed,
                    "goal_verified": verified_goal is not None,
                    "mission_status": state.status.value,
                },
            )
            if verified_navigation is not None:
                self._audit(mission_id, EventKind.PROOF, {
                    "source": "independent_browser_navigation",
                    "proof_ref": verified_navigation.proof_ref,
                    "verified_url": verified_navigation.url[:300],
                })
                self._audit(mission_id, EventKind.MISSION_COMPLETED, {
                    "goal_verified": True,
                    "scope": "browser_navigation_goal",
                })
            if verified_memory is not None:
                self._audit(mission_id, EventKind.PROOF, {
                    "source": "independent_sqlite_memory",
                    "proof_ref": verified_memory.proof_ref,
                    "memory_id": verified_memory.memory_id,
                })
                self._audit(mission_id, EventKind.MISSION_COMPLETED, {
                    "goal_verified": True,
                    "scope": "durable_user_memory",
                })
            self._set_audit_status(mission_id, state.status)
            if verified_goal is not None:
                self._active_mission_id = None
                self._explicit = False
                if log:
                    evidence_type = (
                        "independent_browser_navigation"
                        if verified_navigation is not None
                        else "independent_sqlite_memory"
                    )
                    log(f"[MISSION] state=COMPLETED evidence={evidence_type}")
                # The independent certificate also controls the conversational
                # outcome, even when the model yielded a partial reply.
                from .agent_runtime import AgentTurnResult
                if isinstance(result, AgentTurnResult):
                    from dataclasses import replace
                    text = (f"Mission vérifiée : lien « {verified_navigation.target} » ouvert. "
                            f"URL finale : {verified_navigation.url}"
                            if verified_navigation is not None else
                            "C'est mémorisé durablement dans ma mémoire locale.")
                    result = replace(result, text=text, pause_reason="")
            elif implicit:
                self.detach_mission()
            return result

    def record_external_turn(self, user_text, assistant_text, **kwargs):
        return self.delegate.record_external_turn(user_text, assistant_text, **kwargs)

    def reset(self):
        with self._lock:
            self.detach_mission()
            return self.delegate.reset()

    def warm_up(self, *, log=None):
        return self.delegate.warm_up(log=log)
