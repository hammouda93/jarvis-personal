"""Real queue, scoped model payloads and Qt; simulated brains/phone transport.

No personal account, microphone, desktop action or actual call is exercised.
"""
from concurrent.futures import ThreadPoolExecutor, Future
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.agent_profiles import AgentProfileStore
from jarvis_agent.agent_runtime import AgentTurnResult, CerebrasResponsesAgent, GroqResponsesAgent, build_agent_runtime
from jarvis_agent.config import settings
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.orchestra_budget import TeamBudget, WorkerStopped, worker_scope, current_worker
from jarvis_agent.orchestra_service import OrchestraService
from jarvis_agent.orchestra_store import OrchestraStore, WATCH_DEFAULTS, VOICE_DEFAULTS, instant, next_daily
from jarvis_agent.orchestra_tools import OrchestraToolRegistry
from jarvis_agent.orchestra_voice import VoiceChannel
from jarvis_agent.orchestra_workers import AgentOrchestra, WorkerToolGate
from jarvis_agent.personal_workspace import PersonalWorkspaceStore, wire_size
from jarvis_agent.request_context import RequestContext, ContextToolRegistry
from jarvis_agent.workspace_tools import WorkspaceToolRegistry
from tests.test_agent_runtime import FakeTools
from tests.test_daily_use_regressions_v15 import response
from tests.test_mcp_read_autopilot_v1 import tool


class Fixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = OrchestraStore(self.root / "orchestra.sqlite3")
        self.profiles = AgentProfileStore(self.root / "profiles.json")
        self.workspace = PersonalWorkspaceStore(self.root / "workspace.sqlite3")
        env = patch.dict(os.environ, {"JARVIS_ORCHESTRA_PATH": str(self.store.path),
            "JARVIS_PERSONAL_WORKSPACE_PATH": str(self.workspace.path),
            "JARVIS_AGENT_PROFILES_PATH": str(self.profiles.path),
            "JARVIS_MCP_CONFIG_PATH": str(self.root / "mcp.json")})
        env.start()
        self.addCleanup(env.stop)

    def job(self, **changes):
        return {"title": "Préparer le matin", "request": "Analyse les priorités, sans envoyer de message.",
            "role": "reports", "first_at": "2026-01-01T07:00:00+00:00", "daily_time": "",
            "timezone": "Africa/Abidjan", "enabled": True, "notify_owner": False, **changes}

    def watch(self, enabled=True, **changes):
        current = self.store.config("watch", WATCH_DEFAULTS)
        return self.store.save_config("watch", {**current["data"], "enabled": enabled, **changes}, expected_revision=current["revision"])

    def orchestra(self, builder=None):
        team = AgentOrchestra(builder=builder or (lambda _: Mock(run_with_context=Mock(return_value=AgentTurnResult("Rapport fixture")))),
            store=self.store, profiles=self.profiles)
        self.addCleanup(team.stop)
        return team

    def gateway(self):
        registry = MCPRegistry(self.root / "mcp.json")
        if registry.get_server("fixture") is None:
            registry.add_http("fixture", "https://fixture.example/mcp")
        registry.set_enabled("fixture", True)
        registry.set_runtime_enabled(True)
        transport = Mock()
        transport.call_tool.return_value = {"success": True, "message": "Fixture accepted"}
        adapter = MCPToolRegistry(FakeTools(), registry=registry, transport=transport,
            runtime_gate=registry.runtime_enabled, observation_context=RequestContext(enabled=True))
        return adapter, registry, transport

    def phone(self, **changes):
        gateway, registry, transport = self.gateway()
        registry.discover("fixture", [tool("call_owner", contact_id={"type": "string"}, message={"type": "string"}, idempotency_key={"type": "string"}),
            tool("list_voice_requests", cursor={"type": "string"}),
            tool("reply_voice", event_id={"type": "string"}, message={"type": "string"}, idempotency_key={"type": "string"})])
        registry.note_successful_discovery("fixture")
        registry.set_auto_readonly("fixture", True)
        registry.allow_tool("fixture", "call_owner", True)
        registry.allow_tool("fixture", "reply_voice", True)
        voice = VoiceChannel(gateway, store=self.store, workspace=self.workspace)
        voice.save({**VOICE_DEFAULTS, "enabled": True, "server": "fixture", "contact_id": "owner-fixture",
            "dial_tool": "call_owner", "inbox_tool": "list_voice_requests", "reply_tool": "reply_voice", **changes}, expected_revision=0)
        return voice, registry, transport


class QueueTests(Fixture):
    def test_reads_are_passive_and_watch_defaults_off(self):
        snapshot = self.store.snapshot()
        self.assertFalse(snapshot["watch"]["data"]["enabled"])
        self.assertFalse(snapshot["engine"]["online"])
        self.assertEqual(snapshot["runs"], [])
        self.assertIsNone(self.store.claim_due())
        self.assertFalse(self.store.path.exists())

    def test_claim_is_atomic_across_process_stores_and_survives_reopen(self):
        job = self.store.save_job(self.job())
        self.watch()
        barrier = threading.Barrier(2)
        def claim(_):
            barrier.wait(timeout=5)
            return OrchestraStore(self.store.path).claim_due()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim, range(2)))
        self.assertEqual(sum(c is not None for c in claims), 1)
        reopened = OrchestraStore(self.store.path)
        self.assertEqual(reopened.job(job["id"])["state"], "running")
        self.assertIsNone(reopened.claim_due())
        self.assertEqual(reopened.job(job["id"])["revision"], 2)

    def test_failed_or_interrupted_job_is_never_requeued_automatically(self):
        job = self.store.save_job(self.job(daily_time="07:00"))
        self.watch()
        claimed = self.store.claim_due()
        with self.assertRaisesRegex(ValueError, "stop_engine"):
            self.store.review_interrupted(job["id"], expected_revision=claimed["revision"])
        self.watch(False)
        self.store.review_interrupted(job["id"], expected_revision=claimed["revision"])
        self.watch()
        self.assertEqual(self.store.job(job["id"])["state"], "needs_review")
        self.assertIsNone(self.store.claim_due())

    def test_stale_edits_and_running_edits_do_not_overwrite_work(self):
        job = self.store.save_job(self.job())
        with self.assertRaisesRegex(ValueError, "changed_reload"):
            self.store.save_job(self.job(title="Stale"), identity=job["id"], expected_revision=0)
        self.watch()
        self.store.claim_due()
        with self.assertRaisesRegex(ValueError, "running"):
            self.store.save_job(self.job(title="In flight"), identity=job["id"], expected_revision=2)

    def test_recurrence_skips_catchup_and_dst_nonexistent_time(self):
        after = instant("2026-03-29T00:00:00+00:00")
        # Paris 02:30 does not exist on spring-forward day.
        self.assertEqual(datetime.fromtimestamp(next_daily("02:30", "Europe/Paris", after), timezone.utc).isoformat(),
                         "2026-03-30T00:30:00+00:00")
        fold = next_daily("02:30", "Europe/Paris", instant("2026-10-25T00:00:00+00:00"))
        self.assertEqual(fold, instant("2026-10-25T00:30:00+00:00"))
        self.assertEqual(next_daily("02:30", "Europe/Paris", fold), instant("2026-10-26T01:30:00+00:00"))
        job = self.store.save_job(self.job(daily_time="07:00"))
        self.watch()
        self.store.claim_due()
        self.store.finish_job(job["id"], success=True)
        self.assertGreater(self.store.job(job["id"])["next_at"], time.time())

    def test_daily_cap_counts_jobs_with_actual_worker_runs(self):
        self.watch(max_runs_day=1)
        self.store.save_job(self.job())
        self.store.save_job(self.job(title="Second"))
        first = self.store.claim_due()
        self.store.start_run("reports", "Fixture", job_id=first["id"])
        self.store.finish_job(first["id"], success=True)
        self.assertIsNone(self.store.claim_due())

    def test_invalid_job_or_config_is_rejected_before_writing(self):
        for data in (self.job(first_at="2026-10-16T07:00:00"), self.job(role="unknown"), self.job(enabled=1)):
            with self.assertRaises(ValueError):
                self.store.save_job(data)
        with self.assertRaises(ValueError):
            self.store.save_config("voice", {**VOICE_DEFAULTS, "automatic_calls": "yes"}, expected_revision=0)
        self.assertFalse(self.store.path.exists())


class WorkerTests(Fixture):
    def test_real_model_histories_are_distinct_and_two_roles_overlap(self):
        barrier = threading.Barrier(2)
        requests, runtimes, lock = [], [], threading.Lock()
        def builder(policy):
            context = RequestContext(enabled=True)
            runtime = GroqResponsesAgent(ContextToolRegistry(FakeTools(), context))
            runtime.request_context, runtime.api_key = context, "offline-fixture"
            client = Mock()
            def model(**payload):
                with lock:
                    requests.append((policy["role"], deepcopy(payload)))
                barrier.wait(timeout=8)
                return response("Rapport " + policy["role"])
            client.chat.completions.create.side_effect = model
            runtime._client = client
            runtimes.append(runtime)
            return runtime
        with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
            result = self.orchestra(builder).delegate([
                {"agent": "research", "request": "ALPHA : analyse uniquement la source"},
                {"agent": "reports", "request": "BETA : analyse uniquement les priorités"}])
        self.assertEqual([r["state"] for r in result["reports"]], ["report_ready", "report_ready"])
        self.assertEqual(result["budget"]["used"]["model_calls"], 2)
        self.assertIsNot(runtimes[0]._messages, runtimes[1]._messages)
        for role, payload in requests:
            content = json.dumps(payload["messages"], ensure_ascii=False)
            self.assertIn("ISOLATED_SPECIALIST_ROLE", content)
            self.assertIn("ALPHA" if role == "research" else "BETA", content)
            self.assertNotIn("BETA" if role == "research" else "ALPHA", content)
        self.assertFalse(result["goal_verified"])

    def test_shared_model_cap_stops_before_second_request_and_does_not_fallback(self):
        calls = []
        def builder(policy):
            runtime = CerebrasResponsesAgent(FakeTools())
            runtime.api_key = "offline"
            client = Mock()
            client.chat.completions.create.side_effect = lambda **kw: calls.append(kw) or response("Source analysée")
            runtime._client = client
            return runtime
        result = self.orchestra(builder).delegate([{"agent": "reports", "request": "Analyse A"},
            {"agent": "research", "request": "Analyse B"}], limits={"model_calls": 1, "tool_calls": 2, "seconds": 30})
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(r["state"] for r in result["reports"]), ["report_ready", "stopped"])
        self.assertEqual(result["budget"]["used"]["model_calls"], 1)

    def test_fallback_request_consumes_shared_budget_and_stop_propagates(self):
        runtime = CerebrasResponsesAgent(FakeTools())
        runtime.api_key = "offline"
        primary, fallback = Mock(), Mock()
        primary.chat.completions.create.side_effect = RuntimeError("429 request_quota_exceeded")
        fallback.chat.completions.create.return_value = response("Must not run")
        runtime._client = primary
        configured = replace(settings, cerebras_secondary_api_key="", groq_api_key="offline", cerebras_fallback_to_groq=True)
        with patch("jarvis_agent.agent_runtime.settings", configured), patch("openai.OpenAI", return_value=fallback), \
             patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}), \
             worker_scope(TeamBudget(model_calls=1), self.profiles.get("reports")):
            with self.assertRaises(WorkerStopped):
                runtime.run("Analyse cette demande")
        self.assertEqual(primary.chat.completions.create.call_count, 1)
        fallback.chat.completions.create.assert_not_called()

    def test_one_role_cannot_run_twice_simultaneously(self):
        entered, release = threading.Event(), threading.Event()
        count, maximum, lock = 0, 0, threading.Lock()
        def work(*args, **kwargs):
            nonlocal count, maximum
            with lock:
                count += 1
                maximum = max(maximum, count)
            entered.set()
            release.wait(timeout=5)
            with lock:
                count -= 1
            return AgentTurnResult("Rapport")
        team = self.orchestra(lambda _: Mock(run_with_context=work))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(team.delegate, [{"agent": "reports", "request": "A"}, {"agent": "reports", "request": "B"}])
            self.assertTrue(entered.wait(timeout=5))
            release.set()
            result = future.result(timeout=8)
        self.assertEqual(maximum, 1)
        self.assertEqual(len(result["reports"]), 2)

    def gate(self, role="interaction", selected=None):
        gateway, registry, transport = self.gateway()
        delegate = WorkspaceToolRegistry(gateway, mcp_tools=gateway, store=self.workspace)
        policy = {"role": role, "profile": self.profiles.get(role), "profile_path": str(self.profiles.path), "tools": selected}
        return WorkerToolGate(delegate, policy=policy, mcp_tools=gateway), registry, transport

    def test_worker_can_prepare_locally_but_cannot_approve_or_mutate_external_services(self):
        gate, registry, transport = self.gate()
        registry.discover("fixture", [tool("send_message"), tool("list_items")])
        registry.note_successful_discovery("fixture")
        registry.set_auto_readonly("fixture", True)
        registry.allow_tool("fixture", "send_message", True)
        names = {s["function"]["name"] for s in gate.ollama_tools()}
        self.assertIn("workspace_prepare_action", names)
        self.assertIn("mcp__fixture__list_items", names)
        self.assertNotIn("mcp__fixture__send_message", names)
        self.assertNotIn("open_application", names)
        with worker_scope(TeamBudget(), self.profiles.get("interaction")):
            for name in ("open_application", "mcp__fixture__send_message", "workspace_update_profile"):
                result = gate.execute(name, {}, approved=False)
                self.assertFalse(result.success)
                self.assertFalse(json.loads(result.detail)["dispatched"])
            self.assertFalse(gate.execute("mcp__fixture__list_items", {}, approved=True).success)
            self.assertTrue(gate.execute("mcp__fixture__list_items", {}).success)
        self.assertEqual(transport.call_tool.call_count, 1)

    def test_tool_revocation_and_profile_changes_stop_existing_worker(self):
        gate, registry, transport = self.gate(selected=["mcp__fixture__list_items"])
        registry.discover("fixture", [tool("list_items")])
        registry.note_successful_discovery("fixture")
        registry.set_auto_readonly("fixture", True)
        self.assertIn("mcp__fixture__list_items", gate.allowed())
        registry.set_auto_readonly("fixture", False)
        self.assertNotIn("mcp__fixture__list_items", gate.allowed())
        profile = self.profiles.get("interaction")
        self.profiles.save("interaction", {**profile["settings"], "tools": []}, expected_revision=0)
        with self.assertRaisesRegex(WorkerStopped, "profile_changed"):
            gate.allowed()
        transport.call_tool.assert_not_called()

    def test_watch_disable_stops_next_model_or_tool_checkpoint(self):
        self.watch()
        budget = TeamBudget(admission=lambda: self.store.config("watch", WATCH_DEFAULTS)["data"]["enabled"])
        with worker_scope(budget, self.profiles.get("reports")) as scope:
            scope.reserve("model_calls")
            self.watch(False)
            with self.assertRaisesRegex(WorkerStopped, "watch_disabled"):
                scope.reserve("tool_calls")
        self.assertIsNone(current_worker())

    def test_core_factory_keeps_worker_gate_and_omits_shared_action_ledger(self):
        policy = {"role": "reports", "profile": self.profiles.get("reports"), "profile_path": str(self.profiles.path), "tools": None}
        configured = replace(settings, agent_provider="cerebras", structured_tracing_enabled=False)
        with patch("jarvis_agent.agent_runtime.settings", configured), \
             patch.dict(os.environ, {"JARVIS_HERMES_RELIABILITY_ENABLED": "1", "JARVIS_ACTIVE_SUPERVISOR_ENABLED": "1"}), \
             worker_scope(TeamBudget(), policy["profile"]):
            runtime = build_agent_runtime(_worker_policy=policy)
            self.assertIsInstance(runtime.tools.delegate, WorkerToolGate)
            self.assertNotIn("orchestra_delegate", {s["function"]["name"] for s in runtime.tools.ollama_tools()})


class ConductorTests(Fixture):
    def test_monitoring_omits_private_requests_owner_contact_and_reports(self):
        from jarvis_agent.operator_telemetry import snapshot
        self.store.save_job(self.job(request="PRIVATE_JOB_FIXTURE"))
        run = self.store.start_run("reports", "PRIVATE_REQUEST_FIXTURE")
        self.store.finish_run(run, state="report_ready", report="PRIVATE_REPORT_FIXTURE", receipt={})
        self.store.save_config("voice", {**VOICE_DEFAULTS, "contact_id": "PRIVATE_CONTACT_FIXTURE"}, expected_revision=0)
        projected = json.dumps(snapshot()["orchestra"])
        self.assertNotIn("PRIVATE_", projected)
        self.assertIn(run, projected)

    def test_root_model_delegates_to_real_factory_children_then_receives_their_reports(self):
        clients, payloads = {}, []
        lock = threading.Lock()
        def client_for(runtime):
            with lock:
                if id(runtime) in clients:
                    return clients[id(runtime)]
                client = Mock()
                clients[id(runtime)] = client
            def model(**payload):
                with lock:
                    payloads.append(deepcopy(payload))
                content = json.dumps(payload["messages"], ensure_ascii=False)
                if "ISOLATED_SPECIALIST_ROLE" in content:
                    return response("Rapport du spécialiste : priorités analysées, aucun message envoyé.")
                calls = client.chat.completions.create.call_count
                if calls == 1:
                    return response(name="request_tool_capabilities", arguments={"names": ["orchestra_delegate"]}, identity="select")
                if calls == 2:
                    self.assertIn("orchestra_delegate", {s["function"]["name"] for s in payload["tools"]})
                    return response(name="orchestra_delegate", arguments={"tasks": [
                        {"agent": "reports", "request": "Analyse les priorités. Sans envoyer de message."},
                        {"agent": "research", "request": "Analyse les informations manquantes. Sans envoyer de message."}]}, identity="team")
                self.assertIn("report_ready", content)
                return response("Voici la synthèse des deux rapports, avec leurs incertitudes.")
            client.chat.completions.create.side_effect = model
            return client
        configured = replace(settings, agent_provider="cerebras", structured_tracing_enabled=False, cerebras_api_key="offline")
        env = {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1", "JARVIS_HERMES_RELIABILITY_ENABLED": "0",
               "JARVIS_ACTIVE_SUPERVISOR_ENABLED": "0", "JARVIS_RUNTIME_CONVERGENCE_ENABLED": "0"}
        with patch.dict(os.environ, env), patch("jarvis_agent.agent_runtime.settings", configured), \
             patch.object(CerebrasResponsesAgent, "_get_client", autospec=True, side_effect=client_for):
            runtime = build_agent_runtime()
            self.addCleanup(runtime.orchestra.stop)
            result = runtime.run("Confie deux analyses indépendantes aux spécialistes, puis synthétise leurs rapports.")
        self.assertIn("synthèse", result.text)
        self.assertEqual(len(self.store.runs()), 2)
        self.assertEqual(len(clients), 3)
        self.assertEqual(sum("ISOLATED_SPECIALIST_ROLE" in json.dumps(p["messages"]) for p in payloads), 2)

    def test_large_unicode_reports_are_recoverable_without_new_model_call(self):
        full = "ملاحظات التقرير " * 3000
        team = self.orchestra(lambda _: Mock(run_with_context=Mock(return_value=AgentTurnResult(full))))
        registry = OrchestraToolRegistry(Mock(), orchestra=team, voice=Mock())
        outcome = registry.execute("orchestra_delegate", {"tasks": [{"agent": "reports", "request": "Analyse"}]})
        self.assertTrue(outcome.success)
        self.assertLess(wire_size(json.loads(outcome.detail)), 6500)
        identity = json.loads(outcome.detail)["result"]["reports"][0]["id"]
        self.assertEqual(self.store.run(identity)["report"], full.strip())
        view = registry.execute("orchestra_read", {"view": "report", "id": identity})
        self.assertTrue(view.success)
        self.assertLess(wire_size(json.loads(view.detail)), 6500)

    def test_same_turn_does_not_duplicate_a_team_or_job(self):
        team = self.orchestra()
        registry = OrchestraToolRegistry(Mock(), orchestra=team, voice=Mock())
        args = {"tasks": [{"agent": "reports", "request": "Analyse"}]}
        first, second = registry.execute("orchestra_delegate", args), registry.execute("orchestra_delegate", args)
        self.assertEqual(first.detail, second.detail)
        self.assertEqual(len(self.store.runs()), 1)
        self.assertFalse(registry.execute("orchestra_schedule", {"data": self.job()}).success)
        saved = registry.execute("orchestra_schedule", {"data": self.job()}, approved=True)
        repeated = registry.execute("orchestra_schedule", {"data": self.job()}, approved=True)
        self.assertEqual(saved.detail, repeated.detail)
        self.assertEqual(len(self.store.jobs()), 1)
        identity = self.store.jobs()[0]["id"]
        self.assertTrue(registry.execute("orchestra_read", {"view": "job", "id": identity}).success)

    def test_error_after_worker_start_is_uncertain_and_not_retried_in_turn(self):
        team = self.orchestra()
        registry = OrchestraToolRegistry(Mock(), orchestra=team, voice=Mock())
        with patch.object(team, "delegate", side_effect=OSError("Post-dispatch fixture")) as dispatch:
            args = {"tasks": [{"agent": "reports", "request": "Analyse"}]}
            first = registry.execute("orchestra_delegate", args)
            registry.execute("orchestra_delegate", args)
        self.assertTrue(json.loads(first.detail)["outcome_unknown"])
        self.assertEqual(dispatch.call_count, 1)

    def test_background_service_produces_report_once_without_audio_or_replay(self):
        job = self.store.save_job(self.job())
        team, voice = self.orchestra(), Mock()
        service = OrchestraService(team, voice)
        self.addCleanup(service.stop)
        service.tick()
        self.assertIsNone(service._future)
        self.watch()
        service.tick()
        service._future.result(timeout=8)
        service.tick()
        self.assertEqual(self.store.job(job["id"])["state"], "report_ready")
        self.assertIsNone(service._future)
        self.assertEqual(len(self.store.runs()), 1)
        voice.call_owner.assert_not_called()
        voice.reply.assert_not_called()


class VoiceTests(Fixture):
    def test_operator_review_archives_uncertainty_without_delivery_proof_or_service_replay(self):
        voice, registry, transport = self.phone()
        with patch.object(voice.gateway, "execute", side_effect=OSError("fixture")):
            voice.call_owner("Bonjour", request_key="one", approved=True)
        row = self.store.calls()[0]
        with self.assertRaisesRegex(ValueError, "disable_watch_and_voice"):
            self.store.review_call(row["id"], expected_state="unknown")
        config = self.store.config("voice", VOICE_DEFAULTS)
        voice.save({**config["data"], "enabled": False}, expected_revision=config["revision"])
        self.store.review_call(row["id"], expected_state="unknown")
        reviewed = self.store.calls()[0]
        self.assertEqual(reviewed["state"], "reviewed")
        self.assertTrue(reviewed["receipt"]["outcome_unknown"])
        self.assertFalse(reviewed["receipt"]["delivery_verified"])
        transport.call_tool.assert_not_called()

    def test_phone_requires_real_binding_and_owner_consent(self):
        voice, registry, transport = self.phone()
        self.assertEqual(voice.readiness()["state"], "ready")
        self.assertFalse(voice.readiness()["phone_session_verified"])
        refused = voice.call_owner("Bonjour", request_key="one")
        self.assertFalse(refused.success)
        transport.call_tool.assert_not_called()
        sent = voice.call_owner("Bonjour", request_key="one", approved=True)
        self.assertTrue(sent.success)
        self.assertFalse(json.loads(sent.detail)["phone_answered_verified"])
        self.assertIn("owner-fixture", str(transport.call_tool.call_args))
        voice.call_owner("Bonjour", request_key="one", approved=True)
        self.assertEqual(transport.call_tool.call_count, 1)

    def test_changed_schema_or_revoked_tool_blocks_saved_channel(self):
        voice, registry, transport = self.phone()
        registry.allow_tool("fixture", "call_owner", False)
        self.assertEqual(voice.readiness()["state"], "tools_not_allowed")
        self.assertFalse(voice.call_owner("Bonjour", request_key="one", approved=True).success)
        transport.call_tool.assert_not_called()
        registry.discover("fixture", [tool("call_owner", destination={"type": "string"})])
        self.assertEqual(voice.readiness()["state"], "binding_changed")

    def test_night_and_24h_call_caps_apply_to_automatic_notifications(self):
        voice, registry, transport = self.phone(automatic_calls=True, max_calls_day=1)
        with patch.object(voice, "_night", return_value=True):
            self.assertFalse(voice.call_owner("Nuit", request_key="night", automatic=True).success)
        transport.call_tool.assert_not_called()
        with patch.object(voice, "_night", return_value=False):
            self.assertTrue(voice.call_owner("Jour", request_key="day", automatic=True).success)
            self.assertFalse(voice.call_owner("Suite", request_key="next", automatic=True).success)
        self.assertEqual(transport.call_tool.call_count, 1)

    def test_transport_or_receipt_failure_never_allows_new_unknown_call(self):
        for fault in ("transport", "receipt"):
            with self.subTest(fault=fault):
                # Distinct file/registry per subcase, preserving actual gateway.
                if fault == "receipt":
                    self.store = OrchestraStore(self.root / "second.sqlite3")
                voice, registry, transport = self.phone()
                target = voice.gateway if fault == "transport" else self.store
                method = "execute" if fault == "transport" else "finish_call"
                with patch.object(target, method, side_effect=OSError("fixture")):
                    first = voice.call_owner("Bonjour", request_key="first", approved=True)
                self.assertTrue(json.loads(first.detail)["outcome_unknown"])
                self.assertFalse(voice.call_owner("Nouveau", request_key="different", approved=True).success)
                self.assertEqual(len(self.store.calls()), 1)

    def test_long_inbound_transcript_is_read_from_archive_deduped_and_cursor_saved(self):
        voice, registry, transport = self.phone(automatic_replies=True)
        text = "مهمة خاصة " * 300
        envelope = {"protocol": "jarvis.voice.v1", "next_cursor": "page-2", "events": [
            {"id": "event-1", "contact_id": "owner-fixture", "authenticated": True, "text": text,
             "occurred_at": datetime.now(timezone.utc).isoformat()},
            {"id": "foreign", "contact_id": "another-person", "authenticated": True, "text": "Refuse", "occurred_at": datetime.now(timezone.utc).isoformat()},
            {"id": "unauth", "contact_id": "owner-fixture", "authenticated": False, "text": "Refuse", "occurred_at": datetime.now(timezone.utc).isoformat()}]}
        transport.call_tool.return_value = {"success": True, "message": "", "data": envelope}
        self.assertEqual(voice.poll(), 1)
        self.assertEqual(voice.poll(), 0)
        job = self.store.jobs()[0]
        self.assertEqual(job["data"]["request"], text.strip())
        self.assertEqual(job["data"]["origin"], "voice")
        self.assertIn("page-2", str(transport.call_tool.call_args))
        self.assertEqual(len(self.store.jobs()), 1)
        self.assertEqual(transport.call_tool.call_count, 2)
        self.assertFalse(self.store.config("watch", WATCH_DEFAULTS)["data"]["enabled"])

    def test_untrusted_incomplete_or_old_inbound_is_not_a_task(self):
        voice, registry, transport = self.phone()
        envelope = {"protocol": "jarvis.voice.v1", "events": [{"id": "old", "contact_id": "owner-fixture", "authenticated": True,
            "text": "Old instruction", "occurred_at": "2000-01-01T00:00:00+00:00"}]}
        transport.call_tool.return_value = {"success": True, "message": "", "data": envelope}
        self.assertEqual(voice.poll(), 0)
        transport.call_tool.return_value = {"success": True, "message": "", "data": envelope, "output_truncated": True}
        self.assertEqual(voice.poll(), 0)
        self.assertEqual(self.store.jobs(), [])

    def test_reply_is_bound_to_original_owner_event_and_deduped(self):
        voice, registry, transport = self.phone(automatic_replies=True)
        config = self.store.config("voice", VOICE_DEFAULTS)["data"]
        event = {"id": "event", "contact_id": "owner-fixture", "binding": config["fingerprint"]}
        self.assertFalse(voice.reply({**event, "contact_id": "foreign"}, "Rapport", request_key="r").success)
        transport.call_tool.assert_not_called()
        self.assertTrue(voice.reply(event, "Rapport", request_key="r").success)
        voice.reply(event, "Rapport", request_key="r")
        self.assertEqual(transport.call_tool.call_count, 1)
        self.assertEqual(self.store.calls()[0]["kind"], "reply")
