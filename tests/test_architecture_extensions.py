import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from jarvis_agent.agent_knowledge import AgentKnowledgeStore
from jarvis_agent.agent_knowledge_adapter import LegacyAgentKnowledgeBackend
from jarvis_agent.approval_manager import HumanApprovalManager
from jarvis_agent.event_journal import StructuredEventJournal
from jarvis_agent.execution_managers import (
    ExecutionManagerRegistry,
    ToolExecutionManager,
)
from jarvis_agent.kernel_contracts import (
    KernelRequest,
    KnowledgeScope,
    PromotionTarget,
    SyscallKind,
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
