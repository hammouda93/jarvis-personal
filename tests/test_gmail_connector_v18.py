"""Real registry, bindings, gateway, model payloads and OAuth SDK.

Provider HTTP responses and brains are fixtures; no real mailbox or login.
"""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import httpx2
from mcp.shared.auth import AuthorizationCodeResult

from jarvis_agent.agent_profiles import AgentProfileStore
from jarvis_agent.agent_runtime import CerebrasResponsesAgent, GroqResponsesAgent
from jarvis_agent.gmail_connection import (PIPEDREAM_ENDPOINT, PIPEDREAM_SCOPE, error_reason,
    managed_server_id, snapshot, tool_group)
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.mcp_credentials import CredentialVault
from jarvis_agent.mcp_oauth import build_oauth_provider
from jarvis_agent.mcp_read_policy import is_conservative_read_tool
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.orchestra_budget import TeamBudget, worker_scope
from jarvis_agent.orchestra_workers import WorkerToolGate
from jarvis_agent.personal_workspace import PersonalWorkspaceStore
from jarvis_agent.request_context import RequestContext, ContextToolRegistry
from jarvis_agent.workspace_tools import WorkspaceToolRegistry
from tests.test_agent_runtime import FakeTools
from tests.test_daily_use_regressions_v15 import response
from tests.test_mcp_oauth import MemoryStore


def schema(name, properties=None):
    return {"name": name, "description": "Fixture " + name, "input_schema": {
        "type": "object", "properties": properties or {}, "additionalProperties": False}}


TOOLS = [schema("GMAIL-GET-PROFILE"), schema("GMAIL-SEARCH-EMAILS", {"query": {"type": "string"}}),
    schema("GMAIL-CREATE-DRAFT", {"text": {"type": "string"}}),
    schema("GMAIL-SEND-EMAIL", {"text": {"type": "string"}}),
    schema("GMAIL-ARCHIVE-EMAIL"), schema("GMAIL-DELETE-EMAIL"),
    schema("list_calendar_events"), schema("COMPOSIO_MULTI_EXECUTE_TOOL")]


class Transport:
    def __init__(self):
        self.authorizations, self.inventories, self.calls = [], [], []
        self.tools = deepcopy(TOOLS)

    def authorize(self, entry, **kwargs):
        self.authorizations.append((entry["id"], kwargs["options"]))
        return self.tools

    def inventory(self, entry):
        self.inventories.append(entry["id"])
        return {"tools": self.tools, "capabilities": {"tools": True}}

    def call_tool(self, entry, name, arguments):
        self.calls.append((entry["id"], name, deepcopy(arguments)))
        return {"success": True, "message": "Fixture service result", "data": {"emailAddress": "fixture@example.test", "id": "fixture-message"}}


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.registry = MCPRegistry(self.root / "mcp.json")
        self.workspace = PersonalWorkspaceStore(self.root / "workspace.sqlite3")
        self.profiles = AgentProfileStore(self.root / "profiles.json")
        self.transport = Transport()
        self.vault = CredentialVault(self.root / "vault", backend=MemoryStore())
        env = patch.dict(os.environ, {"JARVIS_PERSONAL_WORKSPACE_PATH": str(self.workspace.path),
            "JARVIS_MCP_CONFIG_PATH": str(self.registry.path), "JARVIS_AGENT_PROFILES_PATH": str(self.profiles.path)})
        env.start()
        self.addCleanup(env.stop)

    def command(self, operation, value, sid="gmail"):
        return perform_mcp_command(self.registry, MCPCommand(operation, sid, json.dumps(value)),
            transport=self.transport, vault=self.vault)

    def connect(self, sid="gmail", provider="pipedream"):
        revision = (self.workspace.bindings().get("gmail") or {}).get("revision", 0)
        return self.command("gmail_connect", {"provider": provider, "binding_revision": revision}, sid)

    def grant(self, *, names=None, automatic=True, sid="gmail", view=None):
        view = view or snapshot(self.registry, self.workspace, server_id=sid)
        value = {k: view[k] for k in ("binding_revision", "connection", "inventory")}
        value.update(selected=names if names is not None else [t["name"] for t in view["tools"]], automatic_reads=automatic)
        return self.command("gmail_grant", value, sid)

    def gateway(self):
        return MCPToolRegistry(FakeTools(), registry=self.registry, transport=self.transport,
                              runtime_gate=self.registry.runtime_enabled)


class OnboardingTests(Fixture):
    def test_first_snapshot_and_server_proposal_create_no_data(self):
        state = snapshot(self.registry, self.workspace)
        self.assertEqual(state["state"], "not_configured")
        self.assertEqual(managed_server_id(self.registry, self.workspace), "gmail")
        self.assertFalse(state["mailbox_identity_verified"])
        self.assertFalse(self.workspace.path.exists())
        self.assertFalse(self.registry.path.exists())

    def test_managed_connect_uses_oauth_and_inventory_without_mail_or_implicit_tool_access(self):
        result = self.connect()
        self.assertTrue(result["success"])
        self.assertEqual(self.transport.authorizations, [("gmail", {"scope": PIPEDREAM_SCOPE, "auth_method": "none", "callback_port": 8973})])
        self.assertEqual(self.transport.inventories, ["gmail"])
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.registry.get_server("gmail")["url"], PIPEDREAM_ENDPOINT)
        self.assertEqual(self.workspace.bindings()["gmail"]["server"], "gmail")
        self.assertEqual(result["gmail"]["state"], "tools_to_allow")
        self.assertFalse(result["gmail"]["mailbox_identity_verified"])
        self.assertEqual(self.registry.list_servers()[0]["allowed"], 0)
        self.assertFalse(self.registry.runtime_enabled())

    def test_full_gmail_tool_set_keeps_original_gateway_and_write_confirmation(self):
        self.connect()
        self.grant()
        self.registry.set_runtime_enabled(True)
        gateway = self.gateway()
        names = {s["function"]["name"] for s in gateway.ollama_tools()}
        self.assertIn("mcp__gmail__GMAIL_SEND_EMAIL", names)
        self.assertNotIn("mcp__gmail__list_calendar_events", names)
        self.assertNotIn("mcp__gmail__COMPOSIO_MULTI_EXECUTE_TOOL", names)
        self.assertFalse(gateway.requires_confirmation("mcp__gmail__GMAIL_SEARCH_EMAILS"))
        self.assertTrue(gateway.requires_confirmation("mcp__gmail__GMAIL_SEND_EMAIL"))
        self.assertTrue(gateway.execute("mcp__gmail__GMAIL_SEARCH_EMAILS", {"query": "is:unread"}).success)
        self.assertFalse(gateway.execute("mcp__gmail__GMAIL_SEND_EMAIL", {"text": "Fixture"}).success)
        self.assertEqual(len(self.transport.calls), 1)
        sent = gateway.execute("mcp__gmail__GMAIL_SEND_EMAIL", {"text": "Fixture"}, approved=True)
        self.assertTrue(sent.success)
        self.assertFalse(json.loads(sent.detail)["verified"])
        self.assertEqual(len(self.transport.calls), 2)

    def test_declining_reads_or_specific_tools_applies_only_to_reviewed_gmail_subset(self):
        self.connect()
        self.registry.allow_tool("gmail", "list_calendar_events", True)
        self.grant(names=["GMAIL-GET-PROFILE"], automatic=False)
        self.assertTrue(self.registry.is_allowed("gmail", "list_calendar_events"))
        self.assertTrue(self.registry.is_allowed("gmail", "GMAIL-GET-PROFILE"))
        self.assertFalse(self.registry.is_auto_read_allowed("gmail", "GMAIL-GET-PROFILE"))
        self.assertFalse(self.registry.is_allowed("gmail", "GMAIL-SEND-EMAIL"))
        self.assertTrue(self.registry.get_server("gmail")["tools"]["GMAIL-SEND-EMAIL"]["blocked"])

    def test_changed_tool_or_access_invalidates_an_open_review(self):
        self.connect()
        view = snapshot(self.registry, self.workspace)
        self.registry.allow_tool("gmail", "GMAIL-SEND-EMAIL", True)
        with self.assertRaisesRegex(ValueError, "inventory_changed"):
            self.grant(view=view)
        view = snapshot(self.registry, self.workspace)
        self.registry.discover("gmail", [*TOOLS, schema("GMAIL-FORWARD-EMAIL")])
        with self.assertRaisesRegex(ValueError, "inventory_changed"):
            self.grant(view=view)
        self.assertFalse(self.registry.is_allowed("gmail", "GMAIL-FORWARD-EMAIL"))

    def test_binding_change_prevents_old_service_setup_from_overwriting_the_operator(self):
        self.registry.add_http("chosen_mail", "https://fixture.example/mcp")
        self.workspace.bind_service("gmail", "chosen_mail", expected_revision=0)
        with self.assertRaisesRegex(ValueError, "binding_changed"):
            self.command("gmail_connect", {"provider": "pipedream", "binding_revision": 0})
        self.assertIsNone(self.registry.get_server("gmail"))
        self.assertEqual(self.workspace.bindings()["gmail"]["server"], "chosen_mail")
        self.assertEqual(self.transport.authorizations, [])

    def test_existing_server_keeps_endpoint_and_credentials_and_filters_other_services(self):
        self.registry.add_http("my_google", "https://fixture.example/mcp")
        self.registry.set_credential_source("my_google", "vault")
        self.connect("my_google", provider="existing")
        self.assertEqual(self.transport.authorizations, [])
        self.assertEqual(self.registry.get_server("my_google")["credential_source"], "vault")
        state = snapshot(self.registry, self.workspace)
        self.assertEqual(state["server"], "my_google")
        self.assertEqual(len(state["tools"]), 6)
        self.grant(sid="my_google")
        self.assertFalse(self.registry.is_allowed("my_google", "list_calendar_events"))

    def test_same_named_server_is_not_replaced_and_failed_registration_is_reused(self):
        self.registry.add_http("gmail", "https://existing.example/mcp")
        self.assertEqual(managed_server_id(self.registry, self.workspace), "gmail_pd")
        with self.assertRaisesRegex(ValueError, "server_conflict"):
            self.connect()
        self.registry.add_http("gmail_pd", PIPEDREAM_ENDPOINT)
        self.assertEqual(managed_server_id(self.registry, self.workspace), "gmail_pd")
        self.assertEqual(self.registry.get_server("gmail")["url"], "https://existing.example/mcp")

    def test_oauth_failure_keeps_service_disabled_and_does_not_bind_or_grant(self):
        self.transport.authorize = Mock(side_effect=RuntimeError("PRIVATE_PROVIDER_ERROR"))
        with self.assertRaises(RuntimeError):
            self.connect()
        self.assertFalse(self.registry.get_server("gmail")["enabled"])
        self.assertEqual(self.workspace.bindings(), {})
        self.assertEqual(managed_server_id(self.registry, self.workspace), "gmail")
        self.assertEqual(self.transport.calls, [])

    def test_cancellation_before_authorization_does_not_contact_the_provider(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaisesRegex(ValueError, "cancelled"):
            perform_mcp_command(self.registry, MCPCommand("gmail_connect", "gmail", json.dumps({
                "provider": "pipedream", "binding_revision": 0})), transport=self.transport, vault=self.vault, stop_event=stop)
        self.assertEqual(self.transport.authorizations, [])
        self.assertEqual(self.workspace.bindings(), {})

    def test_foreign_or_generic_inventory_does_not_claim_gmail_functions(self):
        self.transport.tools = [schema("COMPOSIO_MULTI_EXECUTE_TOOL"), schema("list_calendar_events")]
        result = self.connect()
        self.assertTrue(result["success"], "The transport is tested, independently of Gmail availability")
        self.assertEqual(result["gmail"]["state"], "no_gmail_tools")
        self.assertEqual(result["gmail"]["groups"]["send"]["discovered"], 0)
        with self.assertRaisesRegex(ValueError, "no_tools"):
            self.grant()

    def test_credential_change_invalidates_review_and_revokes_previous_capabilities(self):
        self.connect()
        self.grant()
        view = snapshot(self.registry, self.workspace)
        perform_mcp_command(self.registry, MCPCommand("store_bearer", "gmail", "OFFLINE_FIXTURE"), vault=self.vault)
        with self.assertRaisesRegex(ValueError, "connection_changed"):
            self.grant(view=view)
        self.assertFalse(self.registry.get_server("gmail")["enabled"])
        self.assertFalse(self.registry.is_allowed("gmail", "GMAIL-SEND-EMAIL"))

    def test_gmail_namespaces_do_not_allow_mutations_secrets_or_force_flags_as_reads(self):
        self.assertTrue(is_conservative_read_tool("GMAIL-SEARCH-EMAILS", {"type": "object"}))
        for name in ("GMAIL-SEND-EMAIL", "GMAIL-GET-ACCESS-TOKEN", "GMAIL-DELETE-EMAIL"):
            self.assertFalse(is_conservative_read_tool(name))
        self.assertFalse(is_conservative_read_tool("GMAIL-SEARCH-EMAILS", {"properties": {"force": {"type": "boolean"}}}))
        self.assertIsNone(tool_group("gmail_run_python", {"kind": "http", "url": PIPEDREAM_ENDPOINT}))

    def test_communications_uses_bound_gmail_reads_without_other_shared_server_tools(self):
        self.registry.add_http("my_google", PIPEDREAM_ENDPOINT)
        self.connect("my_google")
        self.grant(sid="my_google")
        self.registry.allow_tool("my_google", "list_calendar_events", True)
        self.registry.set_auto_readonly("my_google", True)
        self.registry.set_runtime_enabled(True)
        gateway = self.gateway()
        delegate = WorkspaceToolRegistry(gateway, mcp_tools=gateway, store=self.workspace)
        policy = {"role": "communications", "profile": self.profiles.get("communications"),
                  "profile_path": str(self.profiles.path), "tools": None}
        gate = WorkerToolGate(delegate, policy=policy, mcp_tools=gateway)
        self.assertIn("mcp__my_google__GMAIL_SEARCH_EMAILS", gate.allowed())
        self.assertNotIn("mcp__my_google__list_calendar_events", gate.allowed())
        self.assertNotIn("mcp__my_google__GMAIL_SEND_EMAIL", gate.allowed())
        with worker_scope(TeamBudget(), policy["profile"]):
            self.assertTrue(gate.execute("mcp__my_google__GMAIL_GET_PROFILE", {}).success)
        self.registry.allow_tool("my_google", "GMAIL-GET-PROFILE", False)
        self.assertNotIn("mcp__my_google__GMAIL_GET_PROFILE", gate.allowed())

    def test_groq_and_cerebras_receive_actual_gmail_schema_and_dispatch_read_once(self):
        self.connect()
        self.grant()
        self.registry.set_runtime_enabled(True)
        for agent_type in (GroqResponsesAgent, CerebrasResponsesAgent):
            with self.subTest(provider=agent_type.__name__):
                context = RequestContext(enabled=True)
                agent = agent_type(ContextToolRegistry(self.gateway(), context))
                agent.request_context, agent.api_key = context, "offline"
                client = Mock()
                payloads = []
                def model(**payload):
                    payloads.append(deepcopy(payload))
                    if len(payloads) == 1:
                        return response(name="request_tool_capabilities", arguments={"names": ["mcp__gmail__GMAIL_GET_PROFILE"]})
                    if len(payloads) == 2:
                        self.assertIn("mcp__gmail__GMAIL_GET_PROFILE", {s["function"]["name"] for s in payload["tools"] if "function" in s})
                        return response(name="mcp__gmail__GMAIL_GET_PROFILE")
                    return response("Le service indique le compte fixture@example.test.")
                client.chat.completions.create.side_effect = model
                agent._client = client
                before = len(self.transport.calls)
                with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
                    result = agent.run("Identifie le compte Gmail avec les outils autorisés, sans modifier la boîte.")
                self.assertIn("fixture@example.test", result.text)
                self.assertEqual(len(self.transport.calls) - before, 1)

    def test_control_queue_serializes_gmail_login_and_exposes_cancellation(self):
        inbox = MCPControlInbox()
        args = json.dumps({"provider": "pipedream", "binding_revision": 0})
        self.assertFalse(inbox.submit("gmail_connect", "gmail", '{"provider":"pipedream"}'))
        self.assertTrue(inbox.submit("gmail_connect", "gmail", args))
        self.assertFalse(inbox.submit("connect", "other"))
        self.assertFalse(inbox.submit("cancel_oauth", "other"))
        self.assertTrue(inbox.submit("cancel_oauth", "gmail"))
        self.assertTrue(inbox.stop_requested.is_set())
        inbox.finish_authorization()
        self.assertTrue(inbox.submit("connect", "other"))

    def test_reconnecting_and_granting_again_cannot_replay_an_uncertain_send(self):
        from jarvis_agent.mcp_sdk_transport import MCPUnavailable
        self.connect()
        self.grant()
        self.registry.set_runtime_enabled(True)
        gateway = self.gateway()
        self.transport.call_tool = Mock(side_effect=MCPUnavailable("fixture timeout", outcome_unknown=True))
        first = gateway.execute("mcp__gmail__GMAIL_SEND_EMAIL", {"text": "Fixture"}, approved=True)
        self.assertTrue(json.loads(first.detail)["outcome_unknown"])
        later = time.time() + 20
        activity = self.registry.activity()
        activity.clock = lambda: later
        with patch.object(self.registry, "activity", return_value=activity):
            self.connect()
            self.grant()
            again = gateway.execute("mcp__gmail__GMAIL_SEND_EMAIL", {"text": "Fixture"}, approved=True)
        self.assertFalse(again.success)
        self.assertEqual(self.transport.call_tool.call_count, 1)
        self.assertEqual(self.workspace.bindings()["gmail"]["server"], "gmail")

    def test_error_feedback_never_copies_remote_credentials_or_body(self):
        self.assertEqual(error_reason(RuntimeError("PRIVATE_BODY secret=fixture")), "transport_error")
        self.assertEqual(error_reason(ValueError("gmail_binding_changed")), "gmail_binding_changed")


class ProviderOAuthSDKTests(Fixture):
    def test_pipedream_metadata_pkce_dynamic_registration_and_vault_binding(self):
        requests = []
        urls = []
        issuer = "https://mcp.pipedream.com"
        metadata = {"issuer": issuer, "authorization_endpoint": issuer + "/api/oauth/authorize",
            "token_endpoint": issuer + "/api/oauth/token", "registration_endpoint": issuer + "/api/oauth/register",
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256", "plain"],
            "scopes_supported": PIPEDREAM_SCOPE.split()}
        class Flow:
            redirect_uri = "http://127.0.0.1:8973/oauth/fixture"
            options = {"scope": PIPEDREAM_SCOPE}
            async def redirect(self, url):
                urls.append(url)
            async def callback(self):
                state = parse_qs(urlsplit(urls[-1]).query)["state"][0]
                return AuthorizationCodeResult(code="OFFLINE_CODE", state=state, iss=issuer)
        def handler(request):
            requests.append(request)
            url = str(request.url)
            if url == PIPEDREAM_ENDPOINT:
                return httpx2.Response(200 if request.headers.get("Authorization") == "Bearer OFFLINE_ACCESS" else 401)
            if url == "https://mcp.pipedream.net/.well-known/oauth-protected-resource":
                return httpx2.Response(200, json={"resource": "https://mcp.pipedream.net",
                    "authorization_servers": [issuer], "scopes_supported": [], "resource_name": "Pipedream MCP Server"})
            if "oauth-protected-resource" in url:
                return httpx2.Response(404)
            if ".well-known/oauth-authorization-server" in url:
                return httpx2.Response(200, json=metadata)
            if url.endswith("/api/oauth/register"):
                return httpx2.Response(201, json={"client_id": "offline-client", "issuer": issuer,
                    "redirect_uris": [Flow.redirect_uri], "token_endpoint_auth_method": "none"})
            if url.endswith("/api/oauth/token"):
                self.assertIn(b"code_verifier=", request.content)
                return httpx2.Response(200, json={"access_token": "OFFLINE_ACCESS", "refresh_token": "OFFLINE_REFRESH",
                    "token_type": "Bearer", "expires_in": 3600})
            self.fail("Unexpected fixture URL: " + url)
        async def run():
            provider, storage = build_oauth_provider({"id": "gmail", "url": PIPEDREAM_ENDPOINT, "kind": "http"}, flow=Flow(), vault=self.vault)
            async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(handler)) as client:
                result = await client.get(PIPEDREAM_ENDPOINT)
                self.assertEqual(result.status_code, 200)
            storage.commit()
        asyncio.run(run())
        params = parse_qs(urlsplit(urls[0]).query)
        self.assertEqual(params["scope"], [PIPEDREAM_SCOPE])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(sum(str(r.url).endswith("/api/oauth/token") for r in requests), 1)
        self.assertIn("OFFLINE_REFRESH", self.vault.read("gmail", PIPEDREAM_ENDPOINT, "oauth_tokens"))
