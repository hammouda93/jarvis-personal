"""Worker-local context with a shared, bounded budget across a team."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time

from .active_mission_supervisor import SupervisorStopped


class WorkerStopped(SupervisorStopped):
    pass


_WORKER = ContextVar("jarvis_isolated_worker", default=None)


class TeamBudget:
    def __init__(self, *, model_calls=8, tool_calls=16, seconds=180, stop_event=None, admission=None):
        for value, cap in ((model_calls, 24), (tool_calls, 48), (seconds, 600)):
            if type(value) is not int or not 1 <= value <= cap:
                raise ValueError("orchestra_invalid_budget")
        self.limits = {"model_calls": model_calls, "tool_calls": tool_calls, "seconds": seconds}
        self.used = {"model_calls": 0, "tool_calls": 0}
        self.start = time.monotonic()
        self.stop_event, self.admission = stop_event, admission
        self.lock = threading.RLock()

    def check(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise WorkerStopped("orchestra_stop_requested")
        if self.admission is not None and not self.admission():
            raise WorkerStopped("orchestra_watch_disabled")
        if time.monotonic() - self.start >= self.limits["seconds"]:
            raise WorkerStopped("orchestra_time_budget")

    def reserve(self, kind):
        with self.lock:
            self.check()
            if self.used[kind] >= self.limits[kind]:
                raise WorkerStopped("orchestra_" + kind + "_budget")
            self.used[kind] += 1

    def snapshot(self):
        with self.lock:
            return {"limits": dict(self.limits), "used": dict(self.used), "elapsed_seconds": round(time.monotonic()-self.start, 2)}


class WorkerScope:
    def __init__(self, budget, profile):
        self.budget, self.profile = budget, profile
        self.used = {"model_calls": 0, "tool_calls": 0}
        self.actions = []

    def reserve(self, kind):
        self.budget.check()
        cap = self.profile["settings"]["model_calls" if kind == "model_calls" else "actions"]
        if self.used[kind] >= cap:
            raise WorkerStopped("orchestra_role_budget")
        if time.monotonic() - self.budget.start >= self.profile["settings"]["seconds"]:
            raise WorkerStopped("orchestra_role_time_budget")
        self.budget.reserve(kind)
        self.used[kind] += 1


@contextmanager
def worker_scope(budget, profile):
    scope = WorkerScope(budget, profile)
    token = _WORKER.set(scope)
    try:
        yield scope
    finally:
        _WORKER.reset(token)


def current_worker():
    return _WORKER.get()


def reserve_worker_model():
    scope = _WORKER.get()
    if scope is not None:
        scope.reserve("model_calls")

