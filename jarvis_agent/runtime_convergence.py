"""Opt-in, non-authoritative mission checkpoints for the existing live runtime.

This is a safe convergence seam, NOT a second executor or an automatic planner.
The delegate remains the only executor. An interrupted action is never replayed.
"""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

from .kernel_contracts import MissionContext, MissionStatus
from .mission_context_store import MissionContextStore
from .task_graph import MissionTaskGraph, TaskNode, TaskStatus
from .task_graph_store import TaskGraphStore


def _default_dir() -> Path:
    root = Path(os.getenv("LOCALAPPDATA") or os.getenv("XDG_STATE_HOME") or Path.home())
    return root / "JarvisPersonal" / "runtime_convergence"


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
        self.context_store = MissionContextStore(root / "mission_context.sqlite3")
        self.graph_store = TaskGraphStore(root / "task_graphs.sqlite3")
        self.owner_user_id = str(owner_user_id or "local-user")
        self._active_mission_id: str | None = None
        self._explicit = False
        self._lock = threading.RLock()

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
            self.graph_store.save(MissionTaskGraph(mission_id))
            self.context_store.save(context, expected_version=0)
            self._active_mission_id = mission_id
            self._explicit = True
            return mission_id

    def resume_mission(self, mission_id: str) -> MissionContext:
        """Attach an existing mission; any in-flight action needs manual review."""
        with self._lock:
            if self._active_mission_id is not None:
                raise RuntimeError("mission_already_active")
            loaded = self.context_store.load(mission_id)
            graph = self.graph_store.load(mission_id)
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
                self.graph_store.save(graph)
                self.context_store.save(context, expected_version=version)
            self._active_mission_id = str(mission_id)
            self._explicit = True
            return context

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
            context.proof_refs.append(str(proof_ref)[:500])
            context.observed_state["goal_verified"] = True
            context.status = MissionStatus.COMPLETED
            context.current_step_id = None
            context.current_step = ""
            self.context_store.save(context, expected_version=version)
            self._active_mission_id = None
            self._explicit = False

    def detach_mission(self) -> None:
        """Stop tracking locally without changing persistent state or replaying."""
        with self._lock:
            self._active_mission_id = None
            self._explicit = False

    def run(self, user_text: str, *, log=None, phase=None):
        return self._run(user_text, log=log, phase=phase)

    def run_with_context(self, user_text: str, context: str, *, log=None, phase=None):
        return self._run(user_text, context=context, log=log, phase=phase)

    def _run(self, user_text: str, *, context=None, log=None, phase=None):
        # Lock also prevents two threads executing a single mission step twice.
        with self._lock:
            implicit = self._active_mission_id is None
            if implicit:
                self.begin_mission(user_text)
                self._explicit = False
            mission_id = self._active_mission_id
            loaded = self.context_store.load(mission_id)
            graph = self.graph_store.load(mission_id)
            if loaded is None or graph is None:
                raise RuntimeError("mission_checkpoint_not_found")
            state, version = loaded
            if state.status == MissionStatus.BLOCKED:
                raise RuntimeError("mission_recovery_requires_review")
            if state.status in (MissionStatus.COMPLETED, MissionStatus.FAILED):
                raise RuntimeError("mission_terminal")
            index = len(graph.nodes()) + 1
            task_id = f"turn_{index:05d}"
            node = TaskNode(
                task_id=task_id,
                mission_id=mission_id,
                capability="interaction.live_turn",
                agent_id="interaction",
                status=TaskStatus.RUNNING,
                payload={"source": "live_runtime", "input_length": len(str(user_text))},
            )
            graph.add(node)
            self.graph_store.save(graph)
            state.status = MissionStatus.RUNNING
            state.current_step_id = task_id
            state.current_step = "interaction.live_turn"
            self.context_store.save(state, expected_version=version)

            try:
                if context is None:
                    result = self.delegate.run(user_text, log=log, phase=phase)
                else:
                    method = getattr(self.delegate, "run_with_context", None)
                    result = (
                        method(user_text, context, log=log, phase=phase)
                        if callable(method)
                        else self.delegate.run(user_text, log=log, phase=phase)
                    )
            except Exception as exc:
                # The delegate might have executed an external side effect.
                # Fail closed, including on a quota error or storage timeout.
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
                if implicit:
                    self.detach_mission()
                raise

            actions = tuple(getattr(result, "actions", ()) or ())
            failed = sum(not bool(getattr(action, "success", False)) for action in actions)
            node.status = TaskStatus.FAILED if failed else TaskStatus.COMPLETED
            node.result = {
                "action_count": len(actions),
                "failed_action_count": failed,
                "action_names": [
                    str(getattr(action, "name", "") or "")[:100]
                    for action in actions
                ],
            }
            state.observed_state["completed_turns"] = index
            state.observed_state["last_action_count"] = len(actions)
            state.observed_state["last_failed_action_count"] = failed
            state.observed_state["goal_verified"] = False
            # A successful click/write does not prove the intended recipient,
            # target, or overall mission. Explicit missions always await proof.
            state.status = (
                MissionStatus.BLOCKED if failed
                else MissionStatus.WAITING_EXTERNAL
                if actions or self._explicit
                else MissionStatus.COMPLETED
            )
            state.current_step_id = None
            state.current_step = ""
            self.graph_store.save(graph)
            self.context_store.save(state)
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
