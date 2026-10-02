import tempfile
import unittest
from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path

from jarvis_agent.agent_knowledge import AgentKnowledgeStore
from jarvis_agent.agent_knowledge_adapter import LegacyAgentKnowledgeBackend
from jarvis_agent.agent_router import (
    AgentRoutingContext,
    CapabilityAgentRouter,
)
from jarvis_agent.approval_manager import HumanApprovalManager
from jarvis_agent.event_journal import StructuredEventJournal
from jarvis_agent.execution_managers import (
    ExecutionManagerRegistry,
    ToolExecutionManager,
)
from jarvis_agent.kernel_contracts import (
    KernelRequest,
    KnowledgeScope,
    MissionStatus,
    PromotionTarget,
    SyscallKind,
    SyscallStatus,
)
from jarvis_agent.kernel_dispatcher import KernelDispatcher
from jarvis_agent.kernel_stack import build_passive_kernel_stack
from jarvis_agent.kernel_request_store import KernelRequestStore
from jarvis_agent.kernel_service import JarvisKernel
from jarvis_agent.knowledge_broker import KnowledgeBroker
from jarvis_agent.knowledge_policy import KnowledgePrincipal
from jarvis_agent.config import settings as real_settings
from jarvis_agent.model_catalog import CurrentModelCatalog
from jarvis_agent.promotion_gate import CorrectionPromotionGate
from jarvis_agent.supervisor_validation import SupervisorValidationState
from jarvis_agent.shadow_kernel_runtime import KernelShadowObserver
from jarvis_agent.dev_supervisor import candidate_from_assessment, FailureAssessment, FailureKind
from jarvis_agent.local_rpc_security import (
    CapabilityTokenAuthority,
    LocalRPCPolicy,
)
from jarvis_agent.tool_gateway import (
    ScopedToolGateway,
    ToolGatewayResult,
)


class ArchitectureExtensionTests(unittest.TestCase):
    def test_existing_agent_knowledge_projects_into_user_scoped_kernel_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AgentKnowledgeStore(Path(tmp) / "knowledge.sqlite3")
            store.upsert_skill(
                name="open_cursor_installer",
                goal="Open the Cursor installer",
                procedure=[
                    "Find the installer in Downloads",
                    "Open the matching Cursor setup file",
                ],
                app_scope="cursor",
                confidence=0.9,
            )
            store.record_lesson(
                scope="behavior",
                pattern="c'est bon",
                rule="Do not repeat the previous mutation.",
                confidence=0.95,
            )
            store.upsert_app_profile(
                display_name="Cursor",
                aliases=["Cursor Setup"],
                observed_capabilities=["installer"],
                confidence=0.88,
            )

            backend = LegacyAgentKnowledgeBackend(
                store,
                owner_user_id="user-1",
            )
            broker = KnowledgeBroker(backend)
            principal = KnowledgePrincipal(
                user_id="user-1",
                agent_id="windows",
            )

            records = broker.search(
                "Cursor installer",
                principal=principal,
                limit=8,
            )

            self.assertTrue(records)
            self.assertTrue(
                all(
                    item.identity.owner_user_id == "user-1"
                    for item in records
                )
            )
            self.assertTrue(
                any(
                    item.identity.scope == KnowledgeScope.SKILL
                    for item in records
                )
            )
            self.assertTrue(
                any(
                    item.identity.scope == KnowledgeScope.APP
                    for item in records
                )
            )

            other_user = broker.search(
                "Cursor installer",
                principal=KnowledgePrincipal(
                    user_id="user-2",
                    agent_id="windows",
                ),
                limit=8,
            )
            self.assertEqual(other_user, [])

    def test_kernel_dispatcher_closes_scheduler_manager_response_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = StructuredEventJournal(
                Path(tmp) / "events.sqlite3"
            )
            mission_id = journal.create_mission(
                goal_summary="Open Notepad",
                user_id="user-1",
                owner_agent_id="windows",
            )
            approvals = HumanApprovalManager(
                Path(tmp) / "approvals.sqlite3"
            )
            request_store = KernelRequestStore(
                Path(tmp) / "requests.sqlite3"
            )
            kernel = JarvisKernel(
                approvals=approvals,
                journal=journal,
                request_store=request_store,
            )

            gateway = ScopedToolGateway()
            gateway.register_tool(
                "open_application",
                lambda args: ToolGatewayResult(
                    tool_name="open_application",
                    success=True,
                    message="opened",
                    detail={"name": args.get("name")},
                ),
            )
            managers = ExecutionManagerRegistry()
            managers.register(ToolExecutionManager(gateway))

            request = KernelRequest(
                request_id="r_test_open",
                mission_id=mission_id,
                syscall_kind=SyscallKind.TOOL,
                capability="computer.interact",
                agent_id="windows",
                user_id="user-1",
                payload={
                    "tool_name": "open_application",
                    "arguments": {"name": "Notepad"},
                },
            )
            submission = kernel.submit(request)
            self.assertTrue(submission.accepted)
            self.assertTrue(submission.queued)

            dispatched = KernelDispatcher(
                kernel=kernel,
                managers=managers,
            ).run_once(timeout_s=0.0)

            self.assertIsNotNone(dispatched)
            self.assertTrue(dispatched.response.success)
            self.assertEqual(
                dispatched.response.result["detail"]["name"],
                "Notepad",
            )
            trace = journal.mission_trace(mission_id)
            kinds = [item["kind"] for item in trace]
            self.assertIn("syscall.queued", kinds)
            self.assertIn("syscall.started", kinds)
            self.assertIn("syscall.completed", kinds)

    def test_local_rpc_policy_is_loopback_only(self):
        self.assertTrue(LocalRPCPolicy("127.0.0.1").validate_host())
        self.assertTrue(LocalRPCPolicy("localhost").validate_host())
        self.assertTrue(LocalRPCPolicy("::1").validate_host())
        self.assertFalse(LocalRPCPolicy("0.0.0.0").validate_host())
        self.assertFalse(LocalRPCPolicy("192.168.1.10").validate_host())

    def test_model_catalog_describes_current_providers_without_routing(self):
        configured = replace(
            real_settings,
            cerebras_api_key="cerebras-test",
            groq_api_key="groq-test",
            vision_enabled=True,
            openai_api_key="",
        )
        catalog = CurrentModelCatalog(configured)

        entries = catalog.entries()
        providers = {item.candidate.provider for item in entries}
        self.assertIn("cerebras", providers)
        self.assertIn("groq", providers)
        self.assertIn("ollama", providers)
        self.assertIn("openai", providers)

        candidates = catalog.configured_candidates()
        configured_pairs = {
            (item.provider, item.model)
            for item in candidates
        }
        self.assertIn(
            ("cerebras", configured.cerebras_agent_model),
            configured_pairs,
        )
        self.assertIn(
            ("groq", configured.groq_agent_model),
            configured_pairs,
        )
        self.assertNotIn(
            ("openai", configured.openai_agent_model),
            configured_pairs,
        )

    def test_core_feedback_cannot_be_promoted_as_automatic_memory(self):
        assessment = FailureAssessment(
            kind=FailureKind.CORE_INVARIANT,
            summary="An explicit tab request must never close the full window.",
            confidence=0.98,
            proposed_scope=KnowledgeScope.CORE,
            promotion_target=PromotionTarget.CORE_INVARIANT,
            evidence_event_ids=("e1", "e2"),
            component="agent_runtime",
        )
        candidate = candidate_from_assessment(
            mission_id="m-core",
            assessment=assessment,
            user_id="user-1",
            agent_id="windows",
            test_ids=["TEST-WIN-BASELINE"],
            evidence={"proof": "verified"},
        )
        candidate.validated = True
        gate_state = SupervisorValidationState(
            candidate_id=candidate.candidate_id,
            tests_passed=True,
            replay_passed=True,
            proof_present=True,
            ready_for_user_validation=True,
            blockers=(),
        )

        decision = CorrectionPromotionGate().evaluate(
            candidate,
            prevalidation=gate_state,
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.automatic_write_allowed)
        self.assertTrue(decision.requires_dev_patch_pipeline)

    def test_user_preference_can_only_promote_after_evidence_and_validation(self):
        assessment = FailureAssessment(
            kind=FailureKind.USER_PREFERENCE,
            summary="Use the user's preferred delivery wording.",
            confidence=0.9,
            proposed_scope=KnowledgeScope.USER,
            promotion_target=PromotionTarget.USER_PREFERENCE,
            evidence_event_ids=("e1",),
        )
        candidate = candidate_from_assessment(
            mission_id="m-user",
            assessment=assessment,
            user_id="user-1",
            agent_id="communications",
            evidence={"proof": "confirmed"},
        )
        not_ready = SupervisorValidationState(
            candidate_id=candidate.candidate_id,
            tests_passed=True,
            replay_passed=True,
            proof_present=True,
            ready_for_user_validation=False,
            blockers=("replay_not_green",),
        )

        blocked = CorrectionPromotionGate().evaluate(
            candidate,
            prevalidation=not_ready,
        )
        self.assertFalse(blocked.allowed)

        candidate.validated = True
        ready = SupervisorValidationState(
            candidate_id=candidate.candidate_id,
            tests_passed=True,
            replay_passed=True,
            proof_present=True,
            ready_for_user_validation=True,
            blockers=(),
        )
        allowed = CorrectionPromotionGate().evaluate(
            candidate,
            prevalidation=ready,
        )
        self.assertTrue(allowed.allowed)
        self.assertTrue(allowed.automatic_write_allowed)
        self.assertFalse(allowed.requires_dev_patch_pipeline)

    def test_passive_kernel_stack_builds_without_touching_live_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = replace(
                real_settings,
                cerebras_api_key="",
                groq_api_key="",
                vision_enabled=False,
                openai_api_key="",
            )
            stack = build_passive_kernel_stack(
                base_dir=tmp,
                owner_user_id="user-1",
                settings=configured,
            )

            self.assertEqual(
                stack.journal.stats()["missions"],
                0,
            )
            self.assertEqual(
                stack.telemetry.stats()["model_calls"],
                0,
            )
            self.assertIsNotNone(
                stack.registry.get_agent("windows")
            )
            self.assertIsNotNone(
                stack.agent_router.route("computer.observe")
            )
            self.assertTrue(
                stack.tool_gateway.registry.tool_allowed(
                    "windows",
                    "inspect_active_window",
                )
            )
            self.assertTrue(
                Path(tmp, "mission_events.sqlite3").exists()
            )
            self.assertTrue(
                Path(tmp, "agent_knowledge.sqlite3").exists()
            )

    def test_kernel_shadow_observes_live_turn_without_dispatching(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = replace(
                real_settings,
                kernel_shadow_enabled=True,
                kernel_shadow_dir=str(Path(tmp) / "shadow"),
                kernel_shadow_user_id="user-1",
                cerebras_api_key="",
                groq_api_key="",
                openai_api_key="",
            )
            observer = KernelShadowObserver(
                base_dir=Path(tmp) / "shadow",
                owner_user_id="user-1",
                settings=configured,
            )
            turn = SimpleNamespace(
                text="Le Bloc-notes est ouvert.",
                actions=(
                    SimpleNamespace(
                        name="open_application",
                        success=True,
                        message="ok",
                        detail='{"name":"Notepad","verified":true}',
                        end_session=False,
                        should_exit=False,
                    ),
                ),
            )

            observation = observer.observe_agent_turn(
                "Ouvre le Bloc-notes.",
                turn,
            )

            self.assertTrue(observation.success)
            self.assertEqual(observation.action_count, 1)
            self.assertIn("windows", observation.candidate_agents)
            self.assertFalse(observation.needs_review)
            self.assertFalse(observation.recovered_after_failure)

            loaded = observer.stack.mission_store.load(
                observation.mission_id
            )
            self.assertIsNotNone(loaded)
            context, _version = loaded
            self.assertEqual(context.status, MissionStatus.COMPLETED)
            self.assertIn("shadow", context.tags)
            self.assertEqual(
                context.observed_state["actual_live_tools"],
                ["open_application"],
            )
            self.assertEqual(
                context.observed_state["shadow_task_count"],
                1,
            )
            graph = observer.stack.graph_store.load(
                observation.mission_id
            )
            self.assertIsNotNone(graph)
            self.assertEqual(len(graph.nodes()), 1)
            self.assertEqual(
                graph.nodes()[0].payload["tool_name"],
                "open_application",
            )
            self.assertEqual(
                graph.nodes()[0].status.value,
                "completed",
            )

            trace = observer.stack.journal.mission_trace(
                observation.mission_id
            )
            kinds = [item["kind"] for item in trace]
            self.assertIn("user.input", kinds)
            self.assertIn("agent.selected", kinds)
            self.assertIn("tool.result", kinds)
            self.assertIn("observation", kinds)
            self.assertIn("mission.completed", kinds)

            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.QUEUED
                ),
                [],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.RUNNING
                ),
                [],
            )
            self.assertIsNone(
                observer.stack.kernel.next_request(timeout_s=0.0)
            )

    def test_kernel_shadow_resolves_shared_windows_tools_from_mission_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = replace(
                real_settings,
                kernel_shadow_enabled=True,
                kernel_shadow_dir=str(Path(tmp) / "shadow"),
                kernel_shadow_user_id="user-1",
            )
            observer = KernelShadowObserver(
                base_dir=Path(tmp) / "shadow",
                owner_user_id="user-1",
                settings=configured,
            )
            turn = SimpleNamespace(
                text="Le Bloc-notes contient Bonjour Jarvis.",
                actions=(
                    SimpleNamespace(
                        name="open_application",
                        success=True,
                        message="ok",
                        detail="Application ouverte: notepad",
                        end_session=False,
                        should_exit=False,
                    ),
                    SimpleNamespace(
                        name="inspect_active_window",
                        success=True,
                        message="ok",
                        detail=(
                            '{"window":{"title":"Bloc-notes"},'
                            '"controls":[{"type":"Button",'
                            '"name":"Ajouter un nouvel onglet"}]}'
                        ),
                        end_session=False,
                        should_exit=False,
                    ),
                    SimpleNamespace(
                        name="write_ui_element",
                        success=True,
                        message="ok",
                        detail='{"verified":true,"value":"Bonjour Jarvis"}',
                        end_session=False,
                        should_exit=False,
                    ),
                ),
            )

            observation = observer.observe_agent_turn(
                "Ouvre le Bloc-notes et écris Bonjour Jarvis.",
                turn,
            )

            self.assertTrue(observation.success)
            self.assertFalse(observation.needs_review)
            context, _version = observer.stack.mission_store.load(
                observation.mission_id
            )
            self.assertEqual(
                context.observed_state["mission_agent_hint"],
                "windows",
            )
            self.assertEqual(
                context.observed_state["resolved_agents"],
                ["windows", "windows", "windows"],
            )
            graph = observer.stack.graph_store.load(
                observation.mission_id
            )
            self.assertEqual(
                [node.agent_id for node in graph.nodes()],
                ["windows", "windows", "windows"],
            )
            self.assertEqual(
                context.observed_state["routing_reasons"],
                [
                    "application_context",
                    "application_context",
                    "application_context",
                ],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.QUEUED
                ),
                [],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.RUNNING
                ),
                [],
            )

    def test_kernel_shadow_resolves_shared_browser_tools_from_goal_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = replace(
                real_settings,
                kernel_shadow_enabled=True,
                kernel_shadow_dir=str(Path(tmp) / "shadow"),
                kernel_shadow_user_id="user-1",
            )
            observer = KernelShadowObserver(
                base_dir=Path(tmp) / "shadow",
                owner_user_id="user-1",
                settings=configured,
            )
            turn = SimpleNamespace(
                text="L'onglet YouTube a été fermé.",
                actions=(
                    SimpleNamespace(
                        name="list_windows",
                        success=True,
                        message="ok",
                        detail='[{"title":"YouTube - Google Chrome"}]',
                        end_session=False,
                        should_exit=False,
                    ),
                    SimpleNamespace(
                        name="close_tab",
                        success=True,
                        message="ok",
                        detail='{"target":"YouTube","verified":true}',
                        end_session=False,
                        should_exit=False,
                    ),
                ),
            )

            observation = observer.observe_agent_turn(
                "Ferme seulement l'onglet YouTube.",
                turn,
            )

            self.assertTrue(observation.success)
            self.assertFalse(observation.needs_review)
            context, _version = observer.stack.mission_store.load(
                observation.mission_id
            )
            self.assertEqual(
                context.observed_state["mission_agent_hint"],
                "browser",
            )
            self.assertEqual(
                context.observed_state["resolved_agents"],
                ["windows", "browser"],
            )
            graph = observer.stack.graph_store.load(
                observation.mission_id
            )
            self.assertEqual(
                [node.agent_id for node in graph.nodes()],
                ["windows", "browser"],
            )
            self.assertEqual(
                context.observed_state["routing_reasons"],
                ["exclusive_tool", "application_context"],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.QUEUED
                ),
                [],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.RUNNING
                ),
                [],
            )

    def test_kernel_shadow_keeps_recovered_agent_turn_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = replace(
                real_settings,
                kernel_shadow_enabled=True,
                kernel_shadow_dir=str(Path(tmp) / "shadow"),
                kernel_shadow_user_id="user-1",
            )
            observer = KernelShadowObserver(
                base_dir=Path(tmp) / "shadow",
                owner_user_id="user-1",
                settings=configured,
            )
            turn = SimpleNamespace(
                text="L'onglet YouTube est fermé.",
                actions=(
                    SimpleNamespace(
                        name="close_window",
                        success=False,
                        message="Utilisez close_tab.",
                        detail="close_window_blocked_for_tab_request",
                        end_session=False,
                        should_exit=False,
                    ),
                    SimpleNamespace(
                        name="close_tab",
                        success=True,
                        message="ok",
                        detail='{"target":"YouTube","verified":true}',
                        end_session=False,
                        should_exit=False,
                    ),
                ),
            )

            observation = observer.observe_agent_turn(
                "Ferme seulement l'onglet YouTube.",
                turn,
            )

            self.assertTrue(observation.success)
            self.assertTrue(observation.needs_review)
            self.assertTrue(observation.recovered_after_failure)
            context, _version = observer.stack.mission_store.load(
                observation.mission_id
            )
            self.assertEqual(context.status, MissionStatus.COMPLETED)
            self.assertEqual(
                context.observed_state["failed_action_count"],
                1,
            )
            graph = observer.stack.graph_store.load(
                observation.mission_id
            )
            self.assertEqual(
                [node.status.value for node in graph.nodes()],
                ["failed", "completed"],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.QUEUED
                ),
                [],
            )

    def test_kernel_shadow_records_direct_fast_path_without_rerouting(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = replace(
                real_settings,
                kernel_shadow_enabled=True,
                kernel_shadow_dir=str(Path(tmp) / "shadow"),
                kernel_shadow_user_id="user-1",
            )
            observer = KernelShadowObserver(
                base_dir=Path(tmp) / "shadow",
                owner_user_id="user-1",
                settings=configured,
            )
            direct_result = SimpleNamespace(
                success=True,
                message="Il est 20 heures.",
                detail="20:00",
                end_session=False,
                should_exit=False,
            )

            observation = observer.observe_direct_turn(
                "Quelle heure est-il ?",
                intent_name="system.time",
                result=direct_result,
                response_text="Il est 20 heures.",
            )

            self.assertTrue(observation.success)
            self.assertEqual(observation.action_count, 1)
            self.assertIn("interaction", observation.candidate_agents)
            self.assertFalse(observation.needs_review)
            loaded = observer.stack.mission_store.load(
                observation.mission_id
            )
            self.assertIsNotNone(loaded)
            context, _version = loaded
            self.assertEqual(
                context.observed_state["actual_live_tools"],
                ["system.time"],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.QUEUED
                ),
                [],
            )

    def test_contextual_agent_router_uses_continuity_and_marks_real_ambiguity(self):
        router = CapabilityAgentRouter()

        continuity = router.route_contextual(
            AgentRoutingContext(
                user_goal="Continue la mission.",
                current_tool="press_key",
                previous_tool="inspect_active_window",
                previous_agent="browser",
                available_agents=(
                    "browser",
                    "windows",
                    "ms_football",
                    "interaction",
                ),
            ),
            candidate_agents=("browser", "windows"),
        )
        self.assertEqual(continuity.agent_id, "browser")
        self.assertEqual(continuity.reason, "mission_continuity")
        self.assertFalse(continuity.needs_review)

        ambiguous = router.route_contextual(
            AgentRoutingContext(
                user_goal="Continue.",
                current_tool="press_key",
                available_agents=(
                    "browser",
                    "windows",
                    "ms_football",
                    "interaction",
                ),
            ),
            candidate_agents=("browser", "windows"),
        )
        self.assertEqual(ambiguous.agent_id, "interaction")
        self.assertEqual(ambiguous.reason, "unresolved_ambiguity")
        self.assertTrue(ambiguous.needs_review)

    def test_kernel_shadow_routes_ms_football_by_explicit_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = replace(
                real_settings,
                kernel_shadow_enabled=True,
                kernel_shadow_dir=str(Path(tmp) / "shadow"),
                kernel_shadow_user_id="user-1",
            )
            observer = KernelShadowObserver(
                base_dir=Path(tmp) / "shadow",
                owner_user_id="user-1",
                settings=configured,
            )
            turn = SimpleNamespace(
                text="Données MS Football lues.",
                actions=(
                    SimpleNamespace(
                        name="msf_query_records",
                        success=True,
                        message="ok",
                        detail='{"records":2}',
                        end_session=False,
                        should_exit=False,
                    ),
                ),
            )

            observation = observer.observe_agent_turn(
                "Dans MS Football, affiche les abonnements actifs.",
                turn,
            )

            self.assertTrue(observation.success)
            self.assertFalse(observation.needs_review)
            context, _version = observer.stack.mission_store.load(
                observation.mission_id
            )
            self.assertEqual(
                context.observed_state["resolved_agents"],
                ["ms_football"],
            )
            self.assertEqual(
                context.observed_state["routing_reasons"],
                ["explicit_domain"],
            )
            graph = observer.stack.graph_store.load(
                observation.mission_id
            )
            self.assertEqual(graph.nodes()[0].agent_id, "ms_football")
            self.assertEqual(
                graph.nodes()[0].payload["routing_context"]["domain"],
                "ms_football",
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.QUEUED
                ),
                [],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.RUNNING
                ),
                [],
            )

    def test_kernel_shadow_marks_true_shared_tool_ambiguity_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            configured = replace(
                real_settings,
                kernel_shadow_enabled=True,
                kernel_shadow_dir=str(Path(tmp) / "shadow"),
                kernel_shadow_user_id="user-1",
            )
            observer = KernelShadowObserver(
                base_dir=Path(tmp) / "shadow",
                owner_user_id="user-1",
                settings=configured,
            )
            turn = SimpleNamespace(
                text="Entrée envoyée.",
                actions=(
                    SimpleNamespace(
                        name="press_key",
                        success=True,
                        message="ok",
                        detail='{"key":"enter"}',
                        end_session=False,
                        should_exit=False,
                    ),
                ),
            )

            observation = observer.observe_agent_turn(
                "Appuie sur Entrée.",
                turn,
            )

            self.assertTrue(observation.success)
            self.assertTrue(observation.needs_review)
            context, _version = observer.stack.mission_store.load(
                observation.mission_id
            )
            self.assertEqual(
                context.observed_state["resolved_agents"],
                ["interaction"],
            )
            self.assertEqual(
                context.observed_state["routing_reasons"],
                ["unresolved_ambiguity"],
            )
            graph = observer.stack.graph_store.load(
                observation.mission_id
            )
            self.assertEqual(graph.nodes()[0].agent_id, "interaction")
            self.assertTrue(
                graph.nodes()[0].payload["routing_needs_review"]
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.QUEUED
                ),
                [],
            )
            self.assertEqual(
                observer.stack.request_store.by_status(
                    SyscallStatus.RUNNING
                ),
                [],
            )

    def test_capability_token_cannot_authorize_undeclared_capability(self):
        authority = CapabilityTokenAuthority()
        token = authority.issue(
            {"developer.test", "developer.replay"}
        )

        self.assertTrue(
            authority.authorize(token, "developer.test")
        )
        self.assertFalse(
            authority.authorize(token, "computer.interact")
        )
        self.assertFalse(
            authority.authorize("wrong-token", "developer.test")
        )

        authority.revoke(token)
        self.assertFalse(
            authority.authorize(token, "developer.test")
        )


if __name__ == "__main__":
    unittest.main()
