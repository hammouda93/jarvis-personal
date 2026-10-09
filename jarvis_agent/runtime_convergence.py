"""Opt-in, non-authoritative mission checkpoints for the existing live runtime.

This is a safe convergence seam, NOT a second executor or an automatic planner.
The delegate remains the only executor. An interrupted action is never replayed.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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
            }
            for node in graph.nodes()[-5:]
        ]
        summary = {
            "mission_id": state.mission_id,
            "goal": state.user_goal[:650],
            "recent_turns": previous,
            "goal_proved": bool(state.observed_state.get("goal_verified")),
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
        result = {
            "mission_id": state.mission_id,
            "user_goal": state.user_goal,
            "status": state.status.value,
            "version": version,
            "goal_verified": bool(state.observed_state.get("goal_verified")),
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
            if os.getenv("JARVIS_MCP_ENABLED", "0").lower() in (
                "1", "true", "yes", "on"
            ):
                tools = MCPRegistry().exposed_tools()
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

    def run(self, user_text: str, *, log=None, phase=None):
        result = self._supervised_conversation(user_text, log=log, phase=phase)
        if result is not None:
            return result
        return self._run(user_text, log=log, phase=phase)

    def run_with_context(self, user_text: str, context: str, *, log=None, phase=None):
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

            try:
                extra_context = "\n\n".join(
                    item for item in (str(context or "").strip(), continuity_context)
                    if item
                )
                method = getattr(self.delegate, "run_with_context", None)
                if extra_context and callable(method):
                    result = method(
                        user_text, extra_context, log=log, phase=phase
                    )
                else:
                    # Preserve the older execution path for runtimes that
                    # do not implement run_with_context.
                    result = self.delegate.run(user_text, log=log, phase=phase)
            except Exception as exc:
                # The delegate might have executed an external side effect.
                # Fail closed, including on a quota error or storage timeout.
                state, _ = self.context_store.load(mission_id)
                node.status = TaskStatus.WAITING_EXTERNAL
                node.error = type(exc).__name__[:100]
                state.status = MissionStatus.BLOCKED
                state.pending_action = {
                    "reason": "delegate_exception_outcome_unknown",
                    "manual_review_required": True,
                }
                state.observed_state["last_exception_type"] = type(exc).__name__[:100]
                self.graph_store.save(graph)
                self.context_store.save(state)
                self._audit(
                    mission_id, "mission.uncertain_outcome",
                    {"task_id": task_id, "error_type": type(exc).__name__[:100]},
                )
                self._set_audit_status(mission_id, MissionStatus.BLOCKED)
                if implicit:
                    self.detach_mission()
                raise

            # Registry gates may have persisted budgets/proofs during the turn.
            state, _ = self.context_store.load(mission_id)
            actions = tuple(getattr(result, "actions", ()) or ())
            failed = sum(not bool(getattr(action, "success", False)) for action in actions)
            node.status = TaskStatus.FAILED if failed else TaskStatus.COMPLETED
            action_names = [
                str(getattr(action, "name", "") or "")[:100]
                for action in actions
            ]
            routes = self._observe_routes(action_names, state.user_goal)
            node.result = {
                "action_count": len(actions),
                "failed_action_count": failed,
                "action_names": action_names,
                "agent_routes": routes,
            }
            state.observed_state["completed_turns"] = index
            state.observed_state["last_action_count"] = len(actions)
            state.observed_state["last_failed_action_count"] = failed
            state.observed_state["last_routing_needs_review"] = any(
                route["needs_review"] for route in routes
            )
            state.observed_state["goal_verified"] = False
            # A successful click/write does not prove the intended recipient,
            # target, or overall mission. Explicit missions always await proof.
            state.status = (
                MissionStatus.BLOCKED if failed
                else MissionStatus.WAITING_EXTERNAL
                if actions or self._explicit
                else MissionStatus.COMPLETED
            )
            if failed:
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
                    "goal_verified": False,
                    "mission_status": state.status.value,
                },
            )
            self._set_audit_status(mission_id, state.status)
            if implicit:
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
