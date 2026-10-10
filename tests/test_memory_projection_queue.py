from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.memory_core_store import MemoryCoreStore, _hash_raw
from jarvis_agent.memory_projection import ProjectionQueue, ProjectionWorker, projection_policy
from jarvis_agent.memory_semantic_interpreter import ModelSemanticMemoryInterpreter
from jarvis_agent.memory_retrieval import relevance
from jarvis_agent.semantic_memory_runtime import SemanticMemoryEngine
from test_semantic_memory_v5 import FixtureInterpreter, projection


ENV = {name: "1" for name in (
    "JARVIS_MEMORY_CORE_ENABLED", "JARVIS_SEMANTIC_MEMORY_V5_ENABLED",
    "JARVIS_MEMORY_AGENT_TOOLS_ENABLED", "JARVIS_MEMORY_SCOPE_GUARD_ENABLED",
)}


class ProjectionQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "memory.sqlite3"
        self.store = MemoryCoreStore(self.path)
        self.raw = "Retiens que mon projet Test-A a le code SYNTH-582."
        self.interpreter = FixtureInterpreter(projections={self.raw: (projection("code", "SYNTH-582"),)})
        self.engine = SemanticMemoryEngine(self.store, self.interpreter)
        self.worker = ProjectionWorker(self.engine)

    def test_raw_admission_and_job_are_durable_before_projection(self):
        item = self.worker.queue.remember(self.raw)
        restarted = ProjectionWorker(SemanticMemoryEngine(MemoryCoreStore(self.path), self.interpreter))
        self.assertEqual(self.store.get_memory(item.id).content, self.raw)
        self.assertEqual(self.store.memory_provenance(item.id), "explicit")
        self.assertEqual(self.interpreter.project_calls, 0)
        self.assertEqual(restarted.queue.status(), {"queued": 1})
        self.assertTrue(restarted.process_one())
        self.assertEqual(self.store.semantic_state(item.id)["status"], "indexed")
        self.assertEqual(restarted.queue.status(), {"done": 1})

    def test_repeated_enqueue_and_processing_do_not_duplicate_projection(self):
        item = self.worker.queue.remember(self.raw)
        self.worker.queue.enqueue(item.id)
        self.worker.process_one()
        self.worker.queue.enqueue(item.id)
        self.assertFalse(self.worker.process_one())
        self.assertEqual(len(self.store.all_memories()), 1)
        self.assertEqual(len(self.store.semantic_facts()), 1)
        self.assertEqual(self.interpreter.project_calls, 1)

    def test_claim_is_exclusive_and_expired_lease_is_recoverable(self):
        item = self.worker.queue.remember(self.raw)
        first = self.worker.queue.claim(now=100, lease_seconds=5)
        another = ProjectionQueue(MemoryCoreStore(self.path), self.engine.parser_version,
                                  self.worker.queue.policy_key)
        self.assertIsNone(another.claim(now=101))
        second = another.claim(now=106)
        self.assertNotEqual(first["owner"], second["owner"])
        with self.assertRaisesRegex(RuntimeError, "projection_lease_lost"):
            self.store.save_projection(item.id, (), parser_version=self.engine.parser_version,
                provenance="explicit", expected_raw_hash=_hash_raw(self.raw), projection_job=first)
        self.store.save_projection(item.id, (projection("code", "SYNTH-582"),),
            parser_version=self.engine.parser_version, provenance="explicit",
            expected_raw_hash=_hash_raw(self.raw), projection_job=second)
        self.assertEqual(another.status(), {"done": 1})

    def test_failure_preserves_source_and_retries_require_explicit_bounded_request(self):
        item = self.worker.queue.remember(self.raw)
        with patch.object(self.interpreter, "project_batch", side_effect=TimeoutError("local_projection_timeout")):
            for attempt in range(3):
                if attempt:
                    self.assertEqual(self.worker.queue.retry_failed(), 1)
                self.assertTrue(self.worker.process_one())
                self.assertFalse(self.worker.process_one())
            self.assertEqual(self.worker.queue.retry_failed(), 0)
        self.assertEqual(self.store.get_memory(item.id).content, self.raw)
        state = self.store.semantic_state(item.id)
        self.assertEqual(state["status"], "error")
        self.assertIn("TimeoutError", state["error"])
        self.assertEqual(self.store.projection_coverage(self.engine.parser_version)["coverage"], "incomplete")

    def test_controlled_retry_can_index_failed_job_without_rewriting_raw(self):
        item = self.worker.queue.remember(self.raw)
        with patch.object(self.interpreter, "project_batch", side_effect=ValueError("invalid_projection")):
            self.worker.process_one()
        self.assertEqual(self.worker.queue.retry_failed(), 1)
        self.worker.process_one()
        self.assertEqual(self.store.semantic_state(item.id)["status"], "indexed")
        self.assertEqual(self.store.get_memory(item.id).content, self.raw)
        self.assertEqual(self.store.projection_coverage(self.engine.parser_version)["coverage"], "complete")

    def test_changed_provider_policy_never_consumes_old_pending_notes(self):
        self.worker.queue.remember(self.raw)
        other = ProjectionQueue(self.store, self.engine.parser_version, "different-provider-consent")
        self.assertIsNone(other.claim())
        self.assertEqual(other.status(), {"queued": 1})
        interpreter = ModelSemanticMemoryInterpreter(provider="ollama", allow_cloud=False)
        self.assertEqual(len(projection_policy(interpreter)), 64)

    def test_modified_source_is_not_overwritten_by_stale_projection(self):
        item = self.worker.queue.remember(self.raw)
        with self.store._connect() as conn:
            conn.execute("UPDATE memories SET content=? WHERE id=?", ("Revised synthetic source", item.id))
        self.worker.process_one()
        self.assertEqual(self.worker.queue.status(), {"failed": 1})
        self.assertIsNone(self.store.semantic_state(item.id))
        self.assertEqual(self.store.semantic_facts(), [])

    def test_write_returns_while_projection_is_blocked_and_duplicate_mutation_is_refused(self):
        entered, release = threading.Event(), threading.Event()
        original = self.interpreter.project_batch
        def slow(items, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("fixture_release_timeout")
            return original(items, **kwargs)
        adapter = FoundationToolAdapter(Mock(), memory=self.store)
        adapter.attach_semantic_memory_engine(self.engine)
        adapter.projection_worker = self.worker
        try:
            with patch.dict("os.environ", ENV), patch.object(self.interpreter, "project_batch", side_effect=slow):
                adapter.begin_turn(self.raw)
                result = adapter.execute("remember_information", {"content": "invented content"})
                self.assertTrue(result.success)
                self.assertIn("projection_queued", result.detail)
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.store.all_memories()[0].content, self.raw)
                read = adapter.execute("semantic_memory_search", {"query": "Test-A"})
                self.assertIn("SYNTH-582", read.detail)
                self.assertEqual(json.loads(read.detail)["coverage"], "incomplete")
                self.assertFalse(adapter.execute("remember_information", {"content": self.raw}).success)
        finally:
            release.set()
            self.assertTrue(self.worker.close(timeout=10))

    def test_read_only_turn_cannot_enqueue_a_write(self):
        adapter = FoundationToolAdapter(Mock(), memory=self.store)
        adapter.attach_semantic_memory_engine(self.engine)
        adapter.projection_worker = self.worker
        with patch.dict("os.environ", ENV):
            adapter.begin_turn("Quel est le code du projet ?")
            result = adapter.execute("remember_information", {"content": self.raw})
        self.assertFalse(result.success)
        self.assertEqual(self.worker.queue.status(), {})

    def test_opaque_entity_suffix_is_preserved_even_if_components_are_stopwords(self):
        self.assertGreater(relevance("Test-A", self.raw), 0)
        self.assertEqual(relevance("Test-B", self.raw), 0)

    def test_similar_opaque_identifiers_are_not_fuzzy_merged(self):
        self.assertEqual(relevance("PROJECT-ALPHA1", "Remember PROJECT-ALPHA2"), 0)

    def test_expired_leases_cannot_cause_unbounded_background_requests(self):
        self.worker.queue.remember(self.raw)
        for instant in (100, 106, 112):
            self.assertIsNotNone(self.worker.queue.claim(now=instant, lease_seconds=5))
        self.assertIsNone(self.worker.queue.claim(now=118))
        self.assertEqual(self.worker.queue.status(), {"failed": 1})
        self.assertEqual(self.worker.queue.retry_failed(), 0)

    def test_other_policy_cannot_mark_expired_jobs_failed(self):
        self.worker.queue.remember(self.raw)
        for instant in (100, 106, 112):
            self.worker.queue.claim(now=instant, lease_seconds=5)
        other = ProjectionQueue(self.store, self.engine.parser_version, "other-policy")
        self.assertIsNone(other.claim(now=118))
        self.assertEqual(other.status(), {"running": 1})
        self.assertIsNone(self.worker.queue.claim(now=118))
        self.assertEqual(other.status(), {"failed": 1})

    def test_old_error_can_be_explicitly_queued_without_inventing_admission(self):
        item = self.store.remember(self.raw)
        self.store.mark_projection_error(item.id, parser_version=self.engine.parser_version, error="old timeout")
        self.assertFalse(self.worker.process_one())
        self.assertEqual(self.worker.queue.enqueue_missing(), 1)
        self.worker.process_one()
        self.assertEqual(self.store.semantic_state(item.id)["status"], "indexed")
        self.assertEqual(self.store.memory_provenance(item.id), "")
        self.assertEqual(self.store.get_memory(item.id).content, self.raw)
        self.assertEqual(self.worker.queue.enqueue_missing(), 0)

    def test_read_only_telemetry_reports_real_queue_counts_without_raw_notes(self):
        from jarvis_agent.operator_telemetry import snapshot
        self.worker.queue.remember(self.raw)
        with patch.dict("os.environ", {**ENV, "JARVIS_DATA_DIR": str(self.path.parent)}):
            state = snapshot()
        self.assertEqual(state["memory_stats"]["projection_jobs"], {"queued": 1})
        self.assertNotIn("SYNTH-582", repr(state))

    def test_runtime_factory_wires_worker_only_with_explicit_opt_in(self):
        from jarvis_agent.agent_runtime import build_agent_runtime
        from jarvis_agent.memory import LOCAL_MEMORY
        for mode in ("0", "1"):
            worker = None
            with patch.dict("os.environ", {**ENV, "JARVIS_MEMORY_ASYNC_PROJECTION_ENABLED": mode}), \
                    patch.object(LOCAL_MEMORY, "db_path", self.path), \
                    patch("jarvis_agent.memory_semantic_interpreter.build_semantic_memory_interpreter",
                          return_value=self.interpreter):
                runtime = build_agent_runtime()
                worker = runtime.tools.projection_worker
                try:
                    self.assertEqual(worker is not None, mode == "1")
                    self.assertEqual(self.interpreter.turn_calls, 0)
                    self.assertEqual(self.interpreter.project_calls, 0)
                finally:
                    if worker is not None:
                        self.assertTrue(worker.close(timeout=5))

    def test_diagnostics_show_error_and_policy_mismatch_without_original_notes(self):
        self.worker.queue.remember(self.raw)
        with patch.object(self.interpreter, "project_batch", side_effect=TimeoutError("synthetic_timeout")):
            self.worker.process_one()
        other = ProjectionQueue(self.store, self.engine.parser_version, "new-policy")
        diagnostic = other.diagnostics()
        self.assertIn("TimeoutError", diagnostic[0]["error"])
        self.assertFalse(diagnostic[0]["policy_matches"])
        self.assertNotIn("SYNTH-582", repr(diagnostic))

