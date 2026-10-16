"""Background research/preparation on an awake machine; no desktop actions."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import uuid

from .orchestra_store import WATCH_DEFAULTS, VOICE_DEFAULTS


class OrchestraService:
    def __init__(self, orchestra, voice, *, log=None):
        self.orchestra, self.voice, self.store = orchestra, voice, orchestra.store
        self.log = log
        self.stop_requested = threading.Event()
        self.owner = uuid.uuid4().hex[:24]
        self._thread = self._pool = self._future = self._job = None
        self._last_poll = self._last_heartbeat = 0

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self.run, name="Jarvis-watch", daemon=True)
        self._thread.start()

    def tick(self, *, now=None):
        now = time.time() if now is None else now
        policy = self.store.config("watch", WATCH_DEFAULTS)["data"]
        if self._future is not None and self._future.done():
            self._finish()
        if not policy["enabled"]:
            if self._last_heartbeat:
                self.store.heartbeat(self.owner, busy=self._future is not None, stopped=self._future is None)
                self._last_heartbeat = 0
            return
        if now - self._last_heartbeat >= 3:
            self.store.heartbeat(self.owner, busy=self._future is not None)
            self._last_heartbeat = now
        voice = self.store.config("voice", VOICE_DEFAULTS)["data"]
        if voice["enabled"] and now - self._last_poll >= 60:
            self._last_poll = now
            self.voice.poll()
        if self._future is None and not self.stop_requested.is_set():
            job = self.store.claim_due(now=now)
            if job:
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Jarvis-work-queue")
                self._job = job
                self._future = self._pool.submit(self.orchestra.delegate,
                    [{"agent": job["data"]["role"], "request": job["data"]["request"]}],
                    limits={"model_calls": policy["model_calls"], "tool_calls": policy["tool_calls"], "seconds": policy["seconds"]},
                    log=self.log, stop_event=self.stop_requested, job_id=job["id"], unattended=True)

    def _finish(self):
        future, job = self._future, self._job
        self._future = self._job = None
        try:
            result = future.result()
            success = all(r["state"] == "report_ready" for r in result["reports"])
            self.store.finish_job(job["id"], success=success)
            if success and not self.stop_requested.is_set() and self.store.config("watch", WATCH_DEFAULTS)["data"]["enabled"]:
                report = self.store.run(result["reports"][0]["id"])
                if job["data"].get("reply_event"):
                    self.voice.reply(job["data"]["reply_event"], report["report"], request_key="reply:" + job["id"])
                elif job["data"]["notify_owner"]:
                    self.voice.call_owner(report["report"][:1800], request_key="watch:" + report["id"], automatic=True)
        except Exception as exc:
            if self.log:
                self.log("[ORCHESTRA] event=watch_pause reason=" + type(exc).__name__)
            current = self.store.job(job["id"])
            if current and current["state"] == "running":
                self.store.finish_job(job["id"], success=False)

    def run(self):
        try:
            while not self.stop_requested.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    if self.log:
                        self.log("[ORCHESTRA] event=watch_pause reason=" + type(exc).__name__)
                self.stop_requested.wait(1)
        finally:
            if self._last_heartbeat:
                try:
                    self.store.heartbeat(self.owner, busy=self._future is not None,
                        stopped=self._future is None or self._future.done())
                except Exception:
                    pass

    def stop(self):
        self.stop_requested.set()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
