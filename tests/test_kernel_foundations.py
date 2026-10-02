import tempfile
import threading
import time
import unittest
from pathlib import Path

from jarvis_agent.capability_registry import (
    DEFAULT_CAPABILITY_REGISTRY,
)
from jarvis_agent.agent_factory import (
    AgentFactory,
    AgentLifecycle,
)
from jarvis_agent.execution_managers import (
    ExecutionManagerRegistry,
    ManagerExecutionResult,
    ToolExecutionManager,
)
from jarvis_agent.mission_orchestrator import MissionOrchestrator
from jarvis_agent.plugin_loader import PluginCatalog
from jarvis_agent.replay_adapter import MCPReplaySandboxAdapter
from jarvis_agent.scoped_knowledge_store import SQLiteScopedKnowledgeBackend
from jarvis_agent.tool_gateway import (
    ScopedToolGateway,
    ToolGatewayResult,
)
from jarvis_agent.approval_manager import (
    ApprovalStatus,
    HumanApprovalManager,
)
from jarvis_agent.context_broker import ContextBroker, ContextItem
from jarvis_agent.component_registry import DEFAULT_COMPONENT_REGISTRY
from jarvis_agent.event_bus import BusEvent, MissionEventBus
from jarvis_agent.kernel_policy import KernelPolicy
from jarvis_agent.knowledge_broker import (
    InMemoryKnowledgeBackend,
    KnowledgeBroker,
    ScopedKnowledgeRecord,
)
from jarvis_agent.knowledge_policy import (
    KnowledgeAccessPolicy,
    KnowledgePrincipal,
)
from jarvis_agent.mission_context_store import MissionContextStore
from jarvis_agent.mission_scheduler import MissionScheduler
from jarvis_agent.model_router import (
    LightweightModelRouter,
    ModelCandidate,
    RouteRequest,
)
from jarvis_agent.plugin_manifest import PluginManifest
from jarvis_agent.replay_sandbox import (
    ReplayAction,
    ReplayPlan,
    ReplayRunner,
)
from jarvis_agent.task_graph import (
    MissionTaskGraph,
    TaskNode,
    TaskStatus,
)
from jarvis_agent.task_graph_store import TaskGraphStore
from jarvis_agent.supervisor_planner import SupervisorPlanner
from jarvis_agent.connector_gateway import (
    ConnectorGateway,
    ConnectorResult,
)
from jarvis_agent.connector_registry import (
    ConnectorBackend,
    DEFAULT_CONNECTOR_REGISTRY,
)
from jarvis_agent.correction_store import CorrectionCandidateStore
from jarvis_agent.kernel_service import JarvisKernel
from jarvis_agent.kernel_request_store import KernelRequestStore
from jarvis_agent.dev_supervisor import (
    FailureAssessment,
    FailureKind,
    candidate_from_assessment,
    validation_destination,
)
from jarvis_agent.event_journal import StructuredEventJournal
from jarvis_agent.incident_bundle import IncidentBundleBuilder
from jarvis_agent.kernel_contracts import (
    EventKind,
    KernelRequest,
    KnowledgeIdentity,
    KnowledgeScope,
    MissionContext,
    MissionStatus,
    PromotionTarget,
    RiskLevel,
    SharingPolicy,
    SyscallKind,
    SyscallStatus,
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

    def test_connector_gateway_enforces_confirmation_and_backend_priority(self):
        class FakeAdapter:
            connector_id = "whatsapp"
            backend = ConnectorBackend.WINDOWS_UI

            def execute(self, capability, arguments):
                return ConnectorResult(
                    connector_id=self.connector_id,
                    capability=capability,
                    success=True,
                    message="sent",
                    data={"arguments": arguments},
                )

        gateway = ConnectorGateway()
        gateway.register_adapter(FakeAdapter())

        blocked = gateway.execute(
            connector_id="whatsapp",
            capability="send_message",
            arguments={"contact": "Dali", "text": "Bonjour"},
            approved=False,
        )
        self.assertFalse(blocked.success)
        self.assertEqual(blocked.error, "approval_required")

        sent = gateway.execute(
            connector_id="whatsapp",
            capability="send_message",
            arguments={"contact": "Dali", "text": "Bonjour"},
            approved=True,
        )
        self.assertTrue(sent.success)
        self.assertEqual(sent.message, "sent")

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

    def test_mission_context_store_round_trips_and_detects_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MissionContextStore(
                Path(tmp) / "missions.sqlite3"
            )
            context = MissionContext(
                mission_id="m_ctx",
                user_goal="Open Chrome",
                status=MissionStatus.RUNNING,
                user_id="u1",
                owner_agent_id="windows",
                current_step="Open application",
                current_step_id="s1",
                expected_state={"app": "chrome"},
                proof_refs=["p1"],
            )
            version = store.save(context)
            self.assertEqual(version, 1)

            loaded = store.load("m_ctx")
            self.assertIsNotNone(loaded)
            restored, current_version = loaded
            self.assertEqual(current_version, 1)
            self.assertEqual(restored.current_step_id, "s1")
            self.assertEqual(restored.proof_refs, ["p1"])

            restored.current_step = "Verify window"
            version2 = store.save(
                restored,
                expected_version=current_version,
            )
            self.assertEqual(version2, 2)

            with self.assertRaisesRegex(
                RuntimeError,
                "mission_context_version_conflict",
            ):
                store.save(restored, expected_version=1)

    def test_knowledge_policy_is_fail_closed_by_user_and_agent(self):
        identity = KnowledgeIdentity(
            scope=KnowledgeScope.USER,
            owner_user_id="u1",
            owner_agent_id="windows",
            sharing_policy=SharingPolicy.PRIVATE,
        )
        owner = KnowledgePrincipal(
            user_id="u1",
            agent_id="windows",
        )
        other_user = KnowledgePrincipal(
            user_id="u2",
            agent_id="windows",
        )
        other_agent = KnowledgePrincipal(
            user_id="u1",
            agent_id="browser",
        )

        self.assertTrue(
            KnowledgeAccessPolicy.can_read(identity, owner)
        )
        self.assertTrue(
            KnowledgeAccessPolicy.can_write(identity, owner)
        )
        self.assertFalse(
            KnowledgeAccessPolicy.can_read(identity, other_user)
        )
        self.assertFalse(
            KnowledgeAccessPolicy.can_read(identity, other_agent)
        )

        core_identity = KnowledgeIdentity(
            scope=KnowledgeScope.CORE,
            sharing_policy=SharingPolicy.PUBLIC,
        )
        self.assertTrue(
            KnowledgeAccessPolicy.can_read(core_identity, owner)
        )
        self.assertFalse(
            KnowledgeAccessPolicy.can_write(core_identity, owner)
        )

    def test_knowledge_broker_applies_scope_isolation_and_write_barrier(self):
        backend = InMemoryKnowledgeBackend()
        broker = KnowledgeBroker(backend)
        owner = KnowledgePrincipal(
            user_id="u1",
            agent_id="windows",
        )
        other = KnowledgePrincipal(
            user_id="u2",
            agent_id="windows",
        )
        record = ScopedKnowledgeRecord(
            knowledge_id="k1",
            identity=KnowledgeIdentity(
                scope=KnowledgeScope.USER,
                owner_user_id="u1",
                owner_agent_id="windows",
                sharing_policy=SharingPolicy.PRIVATE,
            ),
            content="Prefer Chrome for browsing.",
            relevance=0.9,
        )

        self.assertTrue(
            broker.write(record, principal=owner)
        )
        self.assertEqual(
            [item.knowledge_id for item in broker.search(
                "Chrome",
                principal=owner,
            )],
            ["k1"],
        )
        self.assertEqual(
            broker.search("Chrome", principal=other),
            [],
        )

        denied = ScopedKnowledgeRecord(
            knowledge_id="core1",
            identity=KnowledgeIdentity(
                scope=KnowledgeScope.CORE,
                sharing_policy=SharingPolicy.PUBLIC,
            ),
            content="Global core rule",
        )
        self.assertFalse(
            broker.write(denied, principal=owner)
        )

    def test_scheduler_prioritizes_then_preserves_fifo(self):
        scheduler = MissionScheduler()
        scheduler.submit(
            KernelRequest(
                request_id="r1",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.observe",
                agent_id="windows",
                priority=100,
            )
        )
        scheduler.submit(
            KernelRequest(
                request_id="r2",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.observe",
                agent_id="windows",
                priority=10,
            )
        )
        scheduler.submit(
            KernelRequest(
                request_id="r3",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.observe",
                agent_id="windows",
                priority=10,
            )
        )

        first = scheduler.next_request()
        second = scheduler.next_request()
        third = scheduler.next_request()

        self.assertEqual(first.request.request_id, "r2")
        self.assertEqual(second.request.request_id, "r3")
        self.assertEqual(third.request.request_id, "r1")

        response = scheduler.complete(
            "r2",
            success=True,
            result={"verified": True},
        )
        self.assertTrue(response.success)
        self.assertGreaterEqual(response.waiting_ms, 0.0)
        self.assertGreaterEqual(response.turnaround_ms, 0.0)

    def test_scheduler_enforces_per_agent_concurrency_limit(self):
        scheduler = MissionScheduler(
            concurrency_limits={"windows": 1}
        )
        for request_id in ("r1", "r2"):
            scheduler.submit(
                KernelRequest(
                    request_id=request_id,
                    mission_id="m1",
                    syscall_kind=SyscallKind.TOOL,
                    capability="computer.observe",
                    agent_id="windows",
                    priority=10,
                )
            )

        first = scheduler.next_request()
        self.assertEqual(first.request.request_id, "r1")
        self.assertIsNone(scheduler.next_request(timeout_s=0.01))

        scheduler.complete(
            "r1",
            success=True,
            result={"verified": True},
        )
        second = scheduler.next_request(timeout_s=0.01)
        self.assertIsNotNone(second)
        self.assertEqual(second.request.request_id, "r2")

    def test_approval_manager_is_persistent_and_single_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = HumanApprovalManager(
                Path(tmp) / "approvals.sqlite3"
            )
            approval = manager.create(
                mission_id="m1",
                request_id="r_send",
                agent_id="communications",
                capability="communications.send",
                summary="Send message",
                risk=RiskLevel.EXTERNAL_SIDE_EFFECT,
                ttl_s=60,
            )
            self.assertEqual(
                approval.status,
                ApprovalStatus.PENDING,
            )
            self.assertTrue(
                manager.resolve(
                    approval.approval_id,
                    approved=True,
                )
            )
            self.assertTrue(manager.consume(approval.approval_id))
            self.assertFalse(manager.consume(approval.approval_id))
            final = manager.get(approval.approval_id)
            self.assertEqual(final.status, ApprovalStatus.CONSUMED)

    def test_kernel_policy_enforces_agent_capability_and_tool_permissions(self):
        policy = KernelPolicy()

        allowed = policy.authorize(
            KernelRequest(
                request_id="r_ok",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.observe",
                agent_id="windows",
                payload={"tool_name": "inspect_active_window"},
            )
        )
        self.assertTrue(allowed.allowed)
        self.assertFalse(allowed.requires_approval)

        denied = policy.authorize(
            KernelRequest(
                request_id="r_bad",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.observe",
                agent_id="windows",
                payload={"tool_name": "msf_commit_mutation"},
            )
        )
        self.assertFalse(denied.allowed)
        self.assertEqual(
            denied.reason,
            "tool_not_allowed_for_agent",
        )

        external = policy.authorize(
            KernelRequest(
                request_id="r_msf",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="msf.commit_mutation",
                agent_id="ms_football",
                payload={"tool_name": "msf_commit_mutation"},
            )
        )
        self.assertTrue(external.allowed)
        self.assertTrue(external.requires_approval)

    def test_replay_runner_is_backend_neutral_and_evaluates_final_state(self):
        class FakeSandbox:
            sandbox_id = "fake"

            def __init__(self):
                self.calls = []
                self.recording = False

            def reset(self, environment_id):
                self.calls.append(("reset", environment_id))

            def start_recording(self, replay_id):
                self.recording = True
                self.calls.append(("record", replay_id))

            def execute(self, action):
                self.calls.append(
                    ("execute", action.action_type)
                )
                return {"success": True, "action": action.action_type}

            def observe(self):
                return {"window": "Chrome"}

            def evaluate(
                self,
                *,
                expected_state,
                action_results,
                final_observation,
            ):
                return {
                    "success": (
                        final_observation.get("window")
                        == expected_state.get("window")
                    )
                }

            def stop_recording(self, replay_id):
                self.recording = False
                return [f"{replay_id}.mp4"]

        sandbox = FakeSandbox()
        runner = ReplayRunner(sandbox)
        result = runner.run(
            ReplayPlan(
                replay_id="rp1",
                mission_id="m1",
                environment_id="windows-snapshot",
                actions=[
                    ReplayAction(
                        "open_application",
                        {"name": "Chrome"},
                    )
                ],
                expected_state={"window": "Chrome"},
                record_video=True,
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(result.artifacts, ["rp1.mp4"])
        self.assertIn(("reset", "windows-snapshot"), sandbox.calls)

    def test_context_broker_keeps_required_mission_and_respects_budget(self):
        broker = ContextBroker()
        mission = MissionContext(
            mission_id="m1",
            user_goal="Open Chrome",
            status=MissionStatus.RUNNING,
        )
        required = broker.mission_item(mission)
        optional_high = ContextItem(
            item_id="high",
            source="lesson",
            content="important " * 20,
            scope=KnowledgeScope.SKILL,
            relevance=0.95,
            priority=10,
            estimated_tokens=20,
        )
        optional_low = ContextItem(
            item_id="low",
            source="history",
            content="less important " * 40,
            scope=KnowledgeScope.SESSION,
            relevance=0.2,
            priority=200,
            estimated_tokens=80,
        )

        selection = broker.select(
            [optional_low, required, optional_high],
            token_budget=required.token_estimate() + 30,
        )

        ids = [item.item_id for item in selection.items]
        self.assertIn(required.item_id, ids)
        self.assertIn("high", ids)
        self.assertIn("low", selection.omitted_item_ids)

    def test_model_router_uses_telemetry_and_circuit_breaker_without_wiring_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            telemetry = ModelTelemetryStore(
                Path(tmp) / "router.sqlite3"
            )
            telemetry.record(
                provider="cerebras",
                model="gpt-oss-120b",
                success=True,
                latency_s=0.4,
                task_class="windows",
            )
            telemetry.record(
                provider="groq",
                model="gpt-oss-120b",
                success=False,
                latency_s=0.8,
                task_class="windows",
                error_kind="rate_limit",
            )
            router = LightweightModelRouter(
                [
                    ModelCandidate(
                        provider="cerebras",
                        model="gpt-oss-120b",
                        task_tags=("windows",),
                    ),
                    ModelCandidate(
                        provider="groq",
                        model="gpt-oss-120b",
                        task_tags=("windows",),
                    ),
                ],
                telemetry=telemetry,
            )

            decision = router.route(
                RouteRequest(
                    task_class="windows",
                    required_tags=("windows",),
                    latency_budget_ms=1000,
                )
            )
            self.assertIsNotNone(decision)
            self.assertEqual(decision.provider, "cerebras")

            router.note_failure(
                "cerebras",
                "gpt-oss-120b",
                cooldown_s=60,
            )
            fallback = router.route(
                RouteRequest(
                    task_class="windows",
                    required_tags=("windows",),
                )
            )
            self.assertEqual(fallback.provider, "groq")

    def test_plugin_manifest_rejects_unsafe_high_risk_defaults(self):
        with self.assertRaisesRegex(
            ValueError,
            "high_risk_plugin_requires_confirmation",
        ):
            PluginManifest.from_dict(
                {
                    "plugin_id": "mail_plugin",
                    "name": "Mail",
                    "version": "1.0.0",
                    "agent_id": "communications",
                    "entrypoint": "mail.agent:MailAgent",
                    "risk_level": "external_side_effect",
                    "requires_confirmation": False,
                }
            )

        manifest = PluginManifest.from_dict(
            {
                "plugin_id": "msf_plugin",
                "name": "MS Football",
                "version": "1.0.0",
                "agent_id": "ms_football",
                "entrypoint": "plugins.msf:MSFAgent",
                "allowed_tools": ["msf_query_records"],
                "memory_scopes": ["domain", "skill", "test"],
                "risk_level": "read",
                "test_pack": ["TEST-MSF-001"],
            }
        )
        self.assertEqual(manifest.agent_id, "ms_football")

    def test_event_bus_isolates_failing_observers(self):
        bus = MissionEventBus()
        received = []

        def good(event):
            received.append(event.kind)

        def bad(_event):
            raise RuntimeError("observer failed")

        bus.subscribe(EventKind.TOOL_RESULT, bad)
        bus.subscribe(EventKind.TOOL_RESULT, good)

        errors = bus.publish(
            BusEvent(
                kind=EventKind.TOOL_RESULT.value,
                mission_id="m1",
                payload={"tool": "open_application"},
            )
        )

        self.assertEqual(received, ["tool.result"])
        self.assertEqual(errors, ["observer failed"])

    def test_correction_candidate_requires_validation_before_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CorrectionCandidateStore(
                Path(tmp) / "corrections.sqlite3"
            )
            assessment = FailureAssessment(
                kind=FailureKind.USER_PREFERENCE,
                summary="Use Chrome for this user.",
                confidence=0.95,
                proposed_scope=KnowledgeScope.USER,
                promotion_target=PromotionTarget.USER_PREFERENCE,
                evidence_event_ids=("e1",),
                app_id="chrome",
            )
            candidate = candidate_from_assessment(
                mission_id="m1",
                assessment=assessment,
                user_id="u1",
                agent_id="windows",
            )
            store.put(candidate)

            self.assertFalse(
                store.mark_promoted(candidate.candidate_id)
            )
            self.assertEqual(
                len(store.pending(mission_id="m1")),
                1,
            )
            self.assertTrue(
                store.validate(
                    candidate.candidate_id,
                    accepted=True,
                )
            )
            self.assertTrue(
                store.mark_promoted(candidate.candidate_id)
            )
            row = store.get(candidate.candidate_id)
            self.assertTrue(row["validated"])
            self.assertTrue(row["promoted"])

    def test_passive_kernel_requires_approval_then_queues_exact_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            approvals = HumanApprovalManager(
                Path(tmp) / "approvals.sqlite3"
            )
            journal = StructuredEventJournal(
                Path(tmp) / "events.sqlite3"
            )
            mission_id = journal.create_mission(
                mission_id="m_kernel",
                goal_summary="Update MS Football",
                user_id="u1",
                owner_agent_id="ms_football",
            )
            bus = MissionEventBus()
            observed = []
            bus.subscribe(
                None,
                lambda event: observed.append(event.kind),
            )
            kernel = JarvisKernel(
                approvals=approvals,
                event_bus=bus,
                journal=journal,
            )
            request = KernelRequest(
                request_id="r_kernel",
                mission_id=mission_id,
                syscall_kind=SyscallKind.TOOL,
                capability="msf.commit_mutation",
                agent_id="ms_football",
                payload={"tool_name": "msf_commit_mutation"},
            )

            submission = kernel.submit(
                request,
                approval_summary="Commit approved MSF change",
            )

            self.assertTrue(submission.accepted)
            self.assertFalse(submission.queued)
            self.assertTrue(submission.requires_approval)
            self.assertIsNotNone(submission.approval_id)
            self.assertIsNone(kernel.next_request())

            resumed = kernel.resolve_approval(
                submission.approval_id,
                approved=True,
            )
            self.assertTrue(resumed.queued)

            scheduled = kernel.next_request()
            self.assertEqual(
                scheduled.request.request_id,
                "r_kernel",
            )
            response = kernel.complete(
                "r_kernel",
                success=True,
                result={"verified": True},
            )
            self.assertTrue(response.success)
            self.assertIn(
                EventKind.APPROVAL_REQUESTED.value,
                observed,
            )
            self.assertIn(
                EventKind.APPROVAL_RESOLVED.value,
                observed,
            )
            self.assertIn(
                EventKind.SYSCALL_QUEUED.value,
                observed,
            )
            self.assertIn(
                EventKind.SYSCALL_COMPLETED.value,
                observed,
            )

    def test_task_graph_separates_dependencies_from_resource_scheduler(self):
        graph = MissionTaskGraph("m_graph")
        graph.add(
            TaskNode(
                task_id="observe",
                mission_id="m_graph",
                capability="computer.observe",
                agent_id="windows",
                priority=10,
            )
        )
        graph.add(
            TaskNode(
                task_id="act",
                mission_id="m_graph",
                capability="computer.interact",
                agent_id="windows",
                dependencies={"observe"},
                priority=20,
            )
        )
        graph.add(
            TaskNode(
                task_id="verify",
                mission_id="m_graph",
                capability="computer.observe",
                agent_id="windows",
                dependencies={"act"},
                priority=30,
            )
        )

        self.assertEqual(
            [item.task_id for item in graph.ready()],
            ["observe"],
        )
        graph.mark_running("observe")
        graph.mark_completed("observe", {"window": "Chrome"})
        self.assertEqual(
            [item.task_id for item in graph.ready()],
            ["act"],
        )
        graph.mark_running("act")
        graph.pause_for_user("act")
        self.assertEqual(
            graph.get("act").status,
            TaskStatus.WAITING_USER,
        )
        graph.mark_completed("act", {"clicked": True})
        self.assertEqual(
            [item.task_id for item in graph.ready()],
            ["verify"],
        )

    def test_supervisor_planner_selects_regressions_and_keeps_user_scope(self):
        planner = SupervisorPlanner()

        app_assessment = FailureAssessment(
            kind=FailureKind.APP_PROFILE,
            summary="Cursor title resolution changed.",
            confidence=0.9,
            proposed_scope=KnowledgeScope.APP,
            promotion_target=PromotionTarget.APP_PROFILE,
            app_id="cursor",
        )
        app_candidate = candidate_from_assessment(
            mission_id="m_app",
            assessment=app_assessment,
            agent_id="windows",
        )
        app_plan = planner.plan(
            app_candidate,
            changed_paths=["jarvis_agent/windows_perception.py"],
        )
        self.assertTrue(app_plan.requires_replay)
        self.assertTrue(app_plan.requires_user_validation)
        self.assertIn(
            "TEST-WIN-BASELINE",
            app_plan.selected_test_ids,
        )
        self.assertIn(
            "TEST-VISION-LAYER",
            app_plan.selected_test_ids,
        )
        self.assertIn(
            "windows_perception",
            app_plan.component_ids,
        )

        user_assessment = FailureAssessment(
            kind=FailureKind.USER_PREFERENCE,
            summary="Prefer Chrome for this user.",
            confidence=0.95,
            proposed_scope=KnowledgeScope.USER,
            promotion_target=PromotionTarget.USER_PREFERENCE,
        )
        user_candidate = candidate_from_assessment(
            mission_id="m_user",
            assessment=user_assessment,
            user_id="u1",
        )
        user_plan = planner.plan(user_candidate)
        self.assertFalse(user_plan.requires_replay)
        self.assertEqual(
            user_plan.destination,
            "user_preference",
        )
        self.assertIn("keep_user_scoped", user_plan.notes)

    def test_component_registry_finds_smallest_known_code_surfaces(self):
        components = DEFAULT_COMPONENT_REGISTRY.for_paths(
            [
                "jarvis_agent/windows_perception.py",
                "jarvis_agent/screen_vision.py",
            ]
        )
        ids = {item.component_id for item in components}

        self.assertIn("windows_perception", ids)
        self.assertIn("screen_vision", ids)
        self.assertNotIn("ms_football", ids)

    def test_agent_factory_requires_trusted_builder_and_tracks_children(self):
        class FakeAgent:
            def run(self, task):
                return {"echo": task.get("value")}

        factory = AgentFactory()
        with self.assertRaisesRegex(
            RuntimeError,
            "agent_builder_not_registered",
        ):
            factory.spawn(
                agent_id="windows",
                mission_id="m_factory",
            )

        factory.register_builder("windows", FakeAgent)
        parent = factory.spawn(
            agent_id="windows",
            mission_id="m_factory",
        )
        self.assertEqual(parent.lifecycle, AgentLifecycle.CREATED)

        result = factory.execute(
            parent.process_id,
            {"value": 7},
        )
        self.assertEqual(result, {"echo": 7})
        self.assertEqual(
            factory.get(parent.process_id).lifecycle,
            AgentLifecycle.COMPLETED,
        )

    def test_tool_gateway_denies_cross_agent_tool_use(self):
        gateway = ScopedToolGateway()
        gateway.register_tool(
            "inspect_active_window",
            lambda args: ToolGatewayResult(
                tool_name="inspect_active_window",
                success=True,
                message="ok",
                detail={"args": args},
            ),
        )

        allowed = gateway.execute(
            agent_id="windows",
            tool_name="inspect_active_window",
            arguments={"title": "Chrome"},
        )
        denied = gateway.execute(
            agent_id="communications",
            tool_name="inspect_active_window",
            arguments={},
        )

        self.assertTrue(allowed.success)
        self.assertFalse(denied.success)
        self.assertEqual(
            denied.error,
            "tool_not_allowed_for_agent",
        )

    def test_execution_manager_registry_dispatches_by_syscall_kind(self):
        gateway = ScopedToolGateway()
        gateway.register_tool(
            "inspect_active_window",
            lambda args: ToolGatewayResult(
                tool_name="inspect_active_window",
                success=True,
                message="observed",
                detail={"title": args.get("title")},
            ),
        )
        managers = ExecutionManagerRegistry()
        managers.register(ToolExecutionManager(gateway))

        result = managers.execute(
            KernelRequest(
                request_id="r_exec",
                mission_id="m_exec",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.observe",
                agent_id="windows",
                payload={
                    "tool_name": "inspect_active_window",
                    "arguments": {"title": "Chrome"},
                },
            )
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.result["detail"]["title"],
            "Chrome",
        )

    def test_persistent_scoped_knowledge_backend_keeps_identity_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            backend = SQLiteScopedKnowledgeBackend(
                Path(tmp) / "scoped.sqlite3"
            )
            broker = KnowledgeBroker(backend)
            owner = KnowledgePrincipal(
                user_id="u1",
                agent_id="windows",
            )
            other = KnowledgePrincipal(
                user_id="u2",
                agent_id="windows",
            )
            record = ScopedKnowledgeRecord(
                knowledge_id="pref_browser",
                identity=KnowledgeIdentity(
                    scope=KnowledgeScope.USER,
                    owner_user_id="u1",
                    owner_agent_id="windows",
                    sharing_policy=SharingPolicy.PRIVATE,
                ),
                content="Prefer Chrome for browsing.",
                metadata={"source": "validated_feedback"},
                relevance=0.95,
            )

            self.assertTrue(
                broker.write(record, principal=owner)
            )
            self.assertEqual(
                [item.knowledge_id for item in broker.search(
                    "Chrome",
                    principal=owner,
                )],
                ["pref_browser"],
            )
            self.assertEqual(
                broker.search("Chrome", principal=other),
                [],
            )

    def test_plugin_catalog_validates_metadata_without_importing_entrypoint(self):
        catalog = PluginCatalog()
        manifest = PluginManifest.from_dict(
            {
                "plugin_id": "demo_plugin",
                "name": "Demo",
                "version": "1.0.0",
                "agent_id": "windows",
                "entrypoint": "unsafe.module:Agent",
                "allowed_tools": ["inspect_active_window"],
                "risk_level": "read",
            }
        )
        catalog.add_manifest(
            manifest,
            manifest_path="plugins/demo/manifest.json",
        )

        record = catalog.get("demo_plugin")
        self.assertIsNotNone(record)
        self.assertEqual(
            record.manifest.entrypoint,
            "unsafe.module:Agent",
        )
        self.assertEqual(
            len(catalog.enabled_for_agent("windows")),
            1,
        )

    def test_mcp_replay_adapter_translates_backend_neutrally(self):
        class FakeTransport:
            transport_id = "fake-mcp"

            def __init__(self):
                self.calls = []

            def call(self, method, payload=None):
                self.calls.append((method, dict(payload or {})))
                if method == "observe":
                    return {"window": "Chrome"}
                if method == "evaluate":
                    return {"success": True}
                if method == "stop_recording":
                    return {"artifacts": ["replay.mp4"]}
                return {"success": True}

        transport = FakeTransport()
        adapter = MCPReplaySandboxAdapter(transport)

        adapter.reset("vm1")
        adapter.start_recording("rp1")
        action_result = adapter.execute(
            ReplayAction(
                "open_application",
                {"name": "Chrome"},
            )
        )
        observation = adapter.observe()
        evaluation = adapter.evaluate(
            expected_state={"window": "Chrome"},
            action_results=[action_result],
            final_observation=observation,
        )
        artifacts = adapter.stop_recording("rp1")

        self.assertTrue(action_result["success"])
        self.assertEqual(observation["window"], "Chrome")
        self.assertTrue(evaluation["success"])
        self.assertEqual(artifacts, ["replay.mp4"])

    def test_mission_orchestrator_dispatches_dependencies_and_persists_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            contexts = MissionContextStore(
                Path(tmp) / "mission_context.sqlite3"
            )
            journal = StructuredEventJournal(
                Path(tmp) / "events.sqlite3"
            )
            kernel = JarvisKernel(journal=journal)
            orchestrator = MissionOrchestrator(
                kernel=kernel,
                context_store=contexts,
            )
            context = MissionContext(
                mission_id="m_orch",
                user_goal="Observe then interact",
                user_id="u1",
                owner_agent_id="windows",
            )
            graph = MissionTaskGraph("m_orch")
            graph.add(
                TaskNode(
                    task_id="observe",
                    mission_id="m_orch",
                    capability="computer.observe",
                    agent_id="windows",
                    payload={
                        "tool_name": "inspect_active_window",
                    },
                    priority=10,
                )
            )
            graph.add(
                TaskNode(
                    task_id="act",
                    mission_id="m_orch",
                    capability="computer.interact",
                    agent_id="windows",
                    dependencies={"observe"},
                    payload={
                        "tool_name": "click_ui_element",
                    },
                    priority=20,
                )
            )
            orchestrator.register(context, graph)

            first = orchestrator.dispatch_ready("m_orch")
            self.assertEqual(
                [item.task_id for item in first],
                ["observe"],
            )
            scheduled = kernel.next_request()
            self.assertEqual(
                scheduled.request.step_id,
                "observe",
            )
            orchestrator.complete_request(
                scheduled.request.request_id,
                success=True,
                result={"window": "Chrome"},
            )

            second = orchestrator.dispatch_ready("m_orch")
            self.assertEqual(
                [item.task_id for item in second],
                ["act"],
            )
            scheduled2 = kernel.next_request()
            orchestrator.complete_request(
                scheduled2.request.request_id,
                success=True,
                result={"clicked": True},
            )

            loaded = contexts.load("m_orch")
            self.assertIsNotNone(loaded)
            restored, _version = loaded
            self.assertEqual(
                restored.status,
                MissionStatus.COMPLETED,
            )

    def test_task_graph_store_restores_completed_and_pending_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskGraphStore(
                Path(tmp) / "graphs.sqlite3"
            )
            graph = MissionTaskGraph("m_restore")
            graph.add(
                TaskNode(
                    task_id="observe",
                    mission_id="m_restore",
                    capability="computer.observe",
                    agent_id="windows",
                )
            )
            graph.add(
                TaskNode(
                    task_id="act",
                    mission_id="m_restore",
                    capability="computer.interact",
                    agent_id="windows",
                    dependencies={"observe"},
                )
            )
            graph.mark_running("observe")
            graph.mark_completed(
                "observe",
                {"window": "Chrome"},
            )
            store.save(graph)

            restored = store.load("m_restore")

            self.assertIsNotNone(restored)
            self.assertEqual(
                restored.get("observe").status,
                TaskStatus.COMPLETED,
            )
            self.assertEqual(
                restored.get("observe").result,
                {"window": "Chrome"},
            )
            self.assertEqual(
                [item.task_id for item in restored.ready()],
                ["act"],
            )

    def test_kernel_request_store_restores_only_safe_queued_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.sqlite3"
            store = KernelRequestStore(path)
            queued = KernelRequest(
                request_id="r_queued",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.observe",
                agent_id="windows",
                payload={"tool_name": "inspect_active_window"},
            )
            running = KernelRequest(
                request_id="r_running",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.interact",
                agent_id="windows",
                payload={"tool_name": "click_ui_element"},
            )
            store.put(queued, status=SyscallStatus.QUEUED)
            store.put(running, status=SyscallStatus.RUNNING)

            kernel = JarvisKernel(request_store=store)
            restored = kernel.restore_queued_requests()

            self.assertEqual(restored, ["r_queued"])
            scheduled = kernel.next_request()
            self.assertEqual(
                scheduled.request.request_id,
                "r_queued",
            )
            recovery = kernel.recovery_required_requests()
            self.assertEqual(
                [item.request_id for item in recovery],
                ["r_running"],
            )

    def test_kernel_request_store_tracks_live_passive_kernel_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KernelRequestStore(
                Path(tmp) / "requests.sqlite3"
            )
            kernel = JarvisKernel(request_store=store)
            request = KernelRequest(
                request_id="r_lifecycle",
                mission_id="m1",
                syscall_kind=SyscallKind.TOOL,
                capability="computer.observe",
                agent_id="windows",
                payload={"tool_name": "inspect_active_window"},
            )

            submission = kernel.submit(request)
            self.assertTrue(submission.queued)
            self.assertEqual(
                store.get("r_lifecycle")["status"],
                SyscallStatus.QUEUED,
            )

            scheduled = kernel.next_request()
            self.assertEqual(
                store.get("r_lifecycle")["status"],
                SyscallStatus.RUNNING,
            )

            response = kernel.complete(
                scheduled.request.request_id,
                success=True,
                result={"verified": True},
            )
            self.assertTrue(response.success)
            self.assertEqual(
                store.get("r_lifecycle")["status"],
                SyscallStatus.SUCCEEDED,
            )

    def test_scheduler_enforces_resource_class_limit_independently_of_agent(self):
        scheduler = MissionScheduler(
            resource_limits={SyscallKind.LLM: 1}
        )
        for request_id, agent_id in (
            ("llm1", "windows"),
            ("llm2", "browser"),
        ):
            scheduler.submit(
                KernelRequest(
                    request_id=request_id,
                    mission_id="m_resource",
                    syscall_kind=SyscallKind.LLM,
                    capability=(
                        "computer.observe"
                        if agent_id == "windows"
                        else "browser.search"
                    ),
                    agent_id=agent_id,
                    priority=10,
                )
            )

        first = scheduler.next_request()
        self.assertEqual(first.request.request_id, "llm1")
        self.assertIsNone(
            scheduler.next_request(timeout_s=0.01)
        )

        scheduler.complete(
            "llm1",
            success=True,
            result={},
        )
        second = scheduler.next_request(timeout_s=0.01)
        self.assertIsNotNone(second)
        self.assertEqual(second.request.request_id, "llm2")

    def test_incident_bundle_collects_feedback_tools_and_code_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = StructuredEventJournal(
                Path(tmp) / "incident.sqlite3"
            )
            mission_id = journal.create_mission(
                mission_id="m_incident",
                goal_summary="Open Chrome",
                user_id="u1",
                owner_agent_id="windows",
            )
            journal.append_event(
                mission_id=mission_id,
                kind=EventKind.USER_INPUT,
                agent_id="windows",
                component="agent_runtime",
                payload={"text": "Ouvre Chrome"},
            )
            journal.append_event(
                mission_id=mission_id,
                kind=EventKind.TOOL_REQUESTED,
                agent_id="windows",
                component="windows_perception",
                payload={
                    "tool_name": "open_application",
                    "arguments": {"name": "Edge"},
                },
            )
            journal.append_event(
                mission_id=mission_id,
                kind=EventKind.OBSERVATION,
                agent_id="windows",
                component="windows_perception",
                success=True,
                payload={"window_title": "Microsoft Edge"},
            )
            journal.append_event(
                mission_id=mission_id,
                kind=EventKind.USER_FEEDBACK,
                agent_id="windows",
                component="agent_runtime",
                success=False,
                payload={"summary": "Wrong browser opened"},
            )

            bundle = IncidentBundleBuilder(journal).build(
                mission_id,
                changed_paths=[
                    "jarvis_agent/windows_perception.py",
                ],
            )

            self.assertEqual(
                bundle.summary["event_count"],
                5,
            )
            self.assertTrue(bundle.summary["has_user_feedback"])
            self.assertIn(
                "windows_perception",
                bundle.component_ids,
            )
            self.assertEqual(
                bundle.user_inputs[0]["payload"]["text"],
                "Ouvre Chrome",
            )
            self.assertEqual(
                bundle.observations[0]["payload"]["window_title"],
                "Microsoft Edge",
            )

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
