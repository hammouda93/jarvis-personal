import tempfile
import threading
import time
import unittest
from pathlib import Path

from jarvis_agent.capability_registry import (
    DEFAULT_CAPABILITY_REGISTRY,
)
from jarvis_agent.connector_registry import (
    ConnectorBackend,
    DEFAULT_CONNECTOR_REGISTRY,
)
from jarvis_agent.dev_supervisor import (
    FailureAssessment,
    FailureKind,
    candidate_from_assessment,
    validation_destination,
)
from jarvis_agent.event_journal import StructuredEventJournal
from jarvis_agent.kernel_contracts import (
    EventKind,
    KnowledgeScope,
    PromotionTarget,
)
from jarvis_agent.model_telemetry import ModelTelemetryStore
from jarvis_agent.regression_registry import (
    DEFAULT_REGRESSION_REGISTRY,
)
from jarvis_agent.write_barrier import ScopedWriteBarrier


class KernelFoundationTests(unittest.TestCase):
    def test_event_journal_records_ordered_trace_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = StructuredEventJournal(
                Path(tmp) / "events.sqlite3"
            )
            mission_id = journal.create_mission(
                goal_summary="Open Chrome",
                user_id="local-user",
                owner_agent_id="windows",
            )
            journal.append_event(
                mission_id=mission_id,
                kind=EventKind.TOOL_REQUESTED,
                agent_id="windows",
                component="tool_gateway",
                payload={
                    "tool": "open_application",
                    "arguments": {"name": "Chrome"},
                    "api_key": "should-not-be-stored",
                    "contact": "person@example.com",
                },
            )
            journal.finish_mission(
                mission_id,
                success=True,
                summary="Chrome window verified.",
                agent_id="windows",
            )

            trace = journal.mission_trace(mission_id)

            self.assertEqual(trace[0]["kind"], "mission.created")
            self.assertEqual(
                trace[1]["payload"]["api_key"],
                "<redacted>",
            )
            self.assertEqual(
                trace[1]["payload"]["contact"],
                "<email>",
            )
            self.assertEqual(
                trace[-1]["kind"],
                "mission.completed",
            )
            self.assertEqual(
                journal.stats(),
                {"missions": 1, "events": 3},
            )

    def test_write_barrier_waits_only_for_snapshot_writes(self):
        barrier = ScopedWriteBarrier()
        first = barrier.begin_write("user:1")
        second = barrier.begin_write("user:1")
        snapshot = barrier.snapshot("user:1")

        def complete():
            time.sleep(0.02)
            barrier.finish_write("user:1", first)
            time.sleep(0.02)
            barrier.finish_write("user:1", second)

        thread = threading.Thread(target=complete)
        thread.start()
        try:
            self.assertTrue(
                barrier.wait_for_snapshot(
                    "user:1",
                    snapshot,
                    timeout_s=0.5,
                )
            )
        finally:
            thread.join()

        third = barrier.begin_write("user:1")
        old_snapshot = second
        self.assertTrue(
            barrier.wait_for_snapshot(
                "user:1",
                old_snapshot,
                timeout_s=0.02,
            )
        )
        self.assertEqual(third, 3)

    def test_agent_registry_enforces_declared_tool_boundaries(self):
        windows = DEFAULT_CAPABILITY_REGISTRY.get_agent("windows")
        comms = DEFAULT_CAPABILITY_REGISTRY.get_agent("communications")

        self.assertIsNotNone(windows)
        self.assertIsNotNone(comms)
        self.assertTrue(
            DEFAULT_CAPABILITY_REGISTRY.tool_allowed(
                "windows",
                "inspect_active_window",
            )
        )
        self.assertFalse(
            DEFAULT_CAPABILITY_REGISTRY.tool_allowed(
                "communications",
                "close_window",
            )
        )
        self.assertEqual(
            DEFAULT_CAPABILITY_REGISTRY.providers_for(
                ["computer.observe", "browser.search"]
            ),
            {"windows", "browser"},
        )

    def test_connector_registry_prefers_api_then_mcp_then_ui(self):
        backend = DEFAULT_CONNECTOR_REGISTRY.choose_backend(
            "gmail",
            [ConnectorBackend.BROWSER, ConnectorBackend.API],
        )
        self.assertEqual(backend, ConnectorBackend.API)

        whatsapp_backend = DEFAULT_CONNECTOR_REGISTRY.choose_backend(
            "whatsapp",
            [ConnectorBackend.WINDOWS_UI, ConnectorBackend.MCP],
        )
        self.assertEqual(whatsapp_backend, ConnectorBackend.MCP)
        self.assertTrue(
            DEFAULT_CONNECTOR_REGISTRY.supports(
                "whatsapp",
                "send_message",
            )
        )

    def test_dev_supervisor_candidate_stays_pending_until_validation(self):
        assessment = FailureAssessment(
            kind=FailureKind.APP_PROFILE,
            summary="Cursor setup title variant should resolve to Cursor.",
            confidence=0.94,
            proposed_scope=KnowledgeScope.APP,
            promotion_target=PromotionTarget.APP_PROFILE,
            evidence_event_ids=("e_1", "e_2"),
            component="windows_perception",
            app_id="cursor",
        )

        candidate = candidate_from_assessment(
            mission_id="m_1",
            assessment=assessment,
            user_id="local-user",
            agent_id="windows",
            test_ids=["TEST-WIN-BASELINE"],
        )

        self.assertFalse(candidate.validated)
        self.assertFalse(candidate.rejected)
        self.assertEqual(
            validation_destination(candidate),
            "app_profile",
        )
        self.assertEqual(candidate.app_id, "cursor")

    def test_regression_registry_selects_tests_by_changed_path(self):
        selected = DEFAULT_REGRESSION_REGISTRY.select(
            changed_paths=[
                "jarvis_agent/windows_perception.py",
            ]
        )
        ids = {item.test_id for item in selected}

        self.assertIn("TEST-WIN-BASELINE", ids)
        self.assertIn("TEST-VISION-LAYER", ids)

    def test_model_telemetry_summarizes_provider_health_passively(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ModelTelemetryStore(
                Path(tmp) / "models.sqlite3"
            )
            store.record(
                provider="cerebras",
                model="gpt-oss-120b",
                success=True,
                latency_s=0.5,
                task_class="windows",
            )
            store.record(
                provider="cerebras",
                model="gpt-oss-120b",
                success=False,
                latency_s=0.8,
                task_class="windows",
                error_kind="rate_limit",
            )

            summary = store.summary(
                task_class="windows",
                since_hours=1,
            )

            self.assertEqual(len(summary), 1)
            self.assertEqual(summary[0]["calls"], 2)
            self.assertEqual(summary[0]["successes"], 1)
            self.assertEqual(summary[0]["rate_limits"], 1)
            self.assertEqual(store.stats()["model_calls"], 2)


if __name__ == "__main__":
    unittest.main()
