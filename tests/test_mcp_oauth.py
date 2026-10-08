from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthMetadata, OAuthToken
from mcp.client.auth.exceptions import OAuthFlowError

from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.mcp_credentials import CredentialUnavailable, CredentialVault
from jarvis_agent.mcp_oauth import (LoopbackOAuth, OAuthConsentRequired, VaultTokenStorage,
    build_oauth_provider, guarded_http_request, oauth_options, secure_oauth_url, until_cancelled)
from jarvis_agent.mcp_sdk_transport import OfficialMCPTransport
from jarvis_agent.mcp_server_registry import MCPRegistry


class MemoryStore:
    def __init__(self):
        self.items = {}
    def get(self, key):
        return self.items.get(key)
    def put(self, key, value):
        self.items[key] = value
    def delete(self, key):
        self.items.pop(key, None)


class FakeFlow:
    redirect_uri = "http://127.0.0.1:9999/oauth/fixture"
    def __init__(self, wrong_state=False):
        self.urls = []
        self.wrong_state = wrong_state
    async def redirect(self, url):
        self.urls.append(url)
    async def callback(self):
        state = parse_qs(urlsplit(self.urls[-1]).query)["state"][0]
        return AuthorizationCodeResult(code="FIXTURE_CODE", state="BAD" if self.wrong_state else state,
                                       iss="https://auth.example")


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = CredentialVault(self.root / "secrets", backend=MemoryStore())
        self.entry = {"id": "service", "url": "https://service.example/mcp", "kind": "http"}
        self.metadata = OAuthMetadata(issuer="https://auth.example", authorization_endpoint="https://auth.example/authorize",
            token_endpoint="https://auth.example/token", registration_endpoint="https://auth.example/register",
            response_types_supported=["code"], code_challenge_methods_supported=["S256"])
        self.client = OAuthClientInformationFull(client_id="fixture_client", issuer="https://auth.example",
            redirect_uris=[FakeFlow.redirect_uri], token_endpoint_auth_method="none")

    def seed(self, *, expires=-1):
        self.vault.write("service", self.entry["url"], json.dumps({"metadata": self.metadata.model_dump(mode="json"),
            "auth_server_url": "https://auth.example"}), "oauth_metadata")
        self.vault.write("service", self.entry["url"], self.client.model_dump_json(), "oauth_client")
        self.vault.write("service", self.entry["url"], json.dumps({"tokens": {"access_token": "OLD_FIXTURE",
            "refresh_token": "REFRESH_FIXTURE", "expires_in": 5}, "expires_at": expires}), "oauth_tokens")

    def test_restart_expiry_is_absolute_not_restarted_ttl(self):
        self.seed(expires=110)
        storage = VaultTokenStorage(self.entry, vault=self.vault, clock=lambda: 200)
        self.assertEqual(asyncio.run(storage.get_tokens()).expires_in, 0)
        provider, _ = build_oauth_provider(self.entry, vault=self.vault)
        asyncio.run(provider._initialize())
        self.assertFalse(provider.context.is_token_valid())
        self.assertEqual(str(provider.context.oauth_metadata.token_endpoint), "https://auth.example/token")

    def test_missing_consent_never_requests_network_or_opens_browser(self):
        async def run():
            provider, _ = build_oauth_provider(self.entry, vault=self.vault)
            async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(lambda r: self.fail("no request allowed"))) as client:
                await client.get(self.entry["url"])
        with self.assertRaisesRegex(OAuthConsentRequired, "consent_required"), patch("webbrowser.open") as browser:
            asyncio.run(run())
        browser.assert_not_called()

    def test_cached_refresh_uses_discovered_token_endpoint_without_browser_or_discovery(self):
        self.seed()
        requests = []
        def handler(request):
            requests.append(request)
            if str(request.url) == "https://auth.example/token":
                self.assertIn(b"grant_type=refresh_token", request.content)
                return httpx2.Response(200, json={"access_token": "NEW_FIXTURE", "expires_in": 3600, "token_type": "Bearer"})
            self.assertEqual(str(request.url), self.entry["url"])
            self.assertEqual(request.headers["Authorization"], "Bearer NEW_FIXTURE")
            return httpx2.Response(200)
        async def run():
            provider, _ = build_oauth_provider(self.entry, vault=self.vault)
            async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(handler)) as client:
                self.assertEqual((await client.get(self.entry["url"])).status_code, 200)
        with patch("webbrowser.open") as browser:
            asyncio.run(run())
        browser.assert_not_called()
        self.assertEqual(len(requests), 2)
        stored = json.loads(self.vault.read("service", self.entry["url"], "oauth_tokens"))
        self.assertEqual(stored["tokens"]["refresh_token"], "REFRESH_FIXTURE")

    def test_401_or_scope_upgrade_blocks_implicit_registration_and_reauthorization(self):
        for status, header in ((401, 'Bearer resource_metadata="https://service.example/prm"'),
                               (403, 'Bearer error="insufficient_scope", scope="new_scope"')):
            self.seed(expires=99999999999)
            requests = []
            async def run():
                provider, _ = build_oauth_provider(self.entry, vault=self.vault)
                def handler(r):
                    requests.append(r)
                    return httpx2.Response(status, headers={"WWW-Authenticate": header})
                async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(handler)) as client:
                    await client.get(self.entry["url"])
            with self.subTest(status=status), self.assertRaises(OAuthConsentRequired):
                asyncio.run(run())
            self.assertEqual(len(requests), 1)

    def exercise_sdk_flow(self, wrong_state=False, registered=False):
        flow = FakeFlow(wrong_state)
        if registered:
            flow.options = {"client_id": "PRESET_CLIENT", "client_secret": "PRESET_SECRET",
                            "auth_method": "client_secret_basic", "scope": "read_only"}
        requests = []
        def handler(request):
            requests.append(request)
            url = str(request.url)
            if url == self.entry["url"]:
                return httpx2.Response(200 if request.headers.get("Authorization") == "Bearer ACCESS_FIXTURE" else 401,
                    headers={"WWW-Authenticate": 'Bearer resource_metadata="https://service.example/prm"'})
            if "prm" in url or "oauth-protected-resource" in url:
                return httpx2.Response(200, json={"resource": self.entry["url"], "authorization_servers": ["https://auth.example"]})
            if ".well-known" in url:
                return httpx2.Response(200, json=self.metadata.model_dump(mode="json"))
            if url == "https://auth.example/register":
                self.assertFalse(registered, "registered client must not issue DCR")
                return httpx2.Response(201, json=self.client.model_dump(mode="json"))
            if url == "https://auth.example/token":
                self.assertIn(b"code_verifier=", request.content)
                if registered:
                    self.assertTrue(request.headers["Authorization"].startswith("Basic "))
                return httpx2.Response(200, json={"access_token": "ACCESS_FIXTURE", "refresh_token": "REFRESH_FIXTURE",
                    "token_type": "Bearer", "expires_in": 3600})
            self.fail("unexpected request: " + url)
        async def run():
            provider, storage = build_oauth_provider(self.entry, flow=flow, vault=self.vault)
            async with httpx2.AsyncClient(auth=provider, transport=httpx2.MockTransport(handler)) as client:
                response = await client.get(self.entry["url"])
                self.assertEqual(response.status_code, 200)
            storage.commit()
        asyncio.run(run())
        return flow, requests

    def test_official_sdk_pkce_state_registration_and_vault_contract(self):
        flow, requests = self.exercise_sdk_flow()
        query = parse_qs(urlsplit(flow.urls[0]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertGreaterEqual(len(query["code_challenge"][0]), 43)
        self.assertNotIn("code_verifier", query)
        self.assertNotIn("FIXTURE_CODE", repr(self.vault._backend.items))
        self.assertIn("ACCESS_FIXTURE", self.vault.read("service", self.entry["url"], "oauth_tokens"))
        self.assertEqual(len([r for r in requests if str(r.url).endswith("/token")]), 1)

    def test_preregistered_client_and_explicit_minimal_scope_use_sdk_without_dcr(self):
        self.metadata = self.metadata.model_copy(update={"registration_endpoint": None})
        flow, requests = self.exercise_sdk_flow(registered=True)
        self.assertEqual(parse_qs(urlsplit(flow.urls[0]).query)["scope"], ["read_only"])
        self.assertFalse(any(str(r.url).endswith("/register") for r in requests))
        stored = json.loads(self.vault.read("service", self.entry["url"], "oauth_client"))
        self.assertEqual(stored["client_id"], "PRESET_CLIENT")
        self.assertEqual(stored["issuer"], "https://auth.example")

    def test_oauth_options_validate_secrets_methods_and_callback_ports(self):
        for packet in ({"client_secret": "secret"}, {"callback_port": True}, {"callback_port": 80},
                       {"client_id": "id", "auth_method": "client_secret_post"}, {"scope": "a\nb"}, {"unknown": "value"}):
            with self.assertRaises(ValueError):
                oauth_options(json.dumps(packet))
        command = MCPCommand("oauth_authorize", "service", '{"client_secret":"PRIVATE"}')
        self.assertNotIn("PRIVATE", repr(command))

    def test_wrong_sdk_state_fails_without_token_exchange_or_storage_and_redacts_logs(self):
        with self.assertLogs("mcp.client.auth.oauth2", level=logging.ERROR) as logs:
            with self.assertRaises(OAuthFlowError):
                self.exercise_sdk_flow(wrong_state=True)
        self.assertNotIn("State parameter mismatch", repr(logs.output))
        self.assertEqual(self.vault._backend.items, {})

    def test_issuer_binding_mismatch_is_blocked_before_refresh(self):
        self.seed()
        wrong = self.client.model_copy(update={"issuer": "https://other.example"})
        self.vault.write("service", self.entry["url"], wrong.model_dump_json(), "oauth_client")
        provider, _ = build_oauth_provider(self.entry, vault=self.vault)
        with self.assertRaisesRegex(OAuthConsentRequired, "issuer_binding"):
            asyncio.run(provider._initialize())

    def test_uncommitted_authorization_never_persists_client_secrets(self):
        provider, storage = build_oauth_provider(self.entry, flow=FakeFlow(), vault=self.vault)
        provider.context.oauth_metadata = self.metadata
        provider.context.auth_server_url = "https://auth.example"
        asyncio.run(storage.set_client_info(self.client))
        self.assertEqual(self.vault._backend.items, {})
        with self.assertRaisesRegex(OAuthConsentRequired, "incomplete"):
            storage.commit()

    def test_unsafe_urls_and_redirect_relocation_refused(self):
        for url in ("http://auth.example/login", "http://127.0.0.1/token", "javascript:alert(1)",
                    "https://user:password@auth.example/token", "https://auth.example/token#fragment"):
            self.assertFalse(secure_oauth_url(url, self.entry["url"]))
        async def run():
            await guarded_http_request(httpx2.Request("GET", self.entry["url"]), self.entry["url"])
            with self.assertRaises(OAuthConsentRequired):
                await guarded_http_request(httpx2.Request("GET", "https://service.example/other"), self.entry["url"])
        asyncio.run(run())

    def test_out_of_band_cancel_does_not_queue_or_clear_stop(self):
        inbox = MCPControlInbox()
        self.assertTrue(inbox.submit("oauth_authorize", "service"))
        self.assertFalse(inbox.submit("cancel_oauth", "other"))
        self.assertFalse(inbox.submit("enable", "service"))
        self.assertTrue(inbox.submit("cancel_oauth", "service"))
        self.assertTrue(inbox.stop_requested.is_set())
        self.assertEqual(inbox.pop_nowait().operation, "oauth_authorize")
        self.assertIsNone(inbox.pop_nowait())
        inbox.finish_authorization()
        self.assertTrue(inbox.submit("enable", "service"))

    def test_cancel_and_deadline_close_async_work(self):
        closed = []
        async def wait():
            try:
                await asyncio.sleep(60)
            finally:
                closed.append(True)
        async def run():
            event = threading.Event()
            loop = asyncio.get_running_loop()
            loop.call_later(.01, event.set)
            with self.assertRaisesRegex(OAuthConsentRequired, "cancelled"):
                await until_cancelled(wait(), event)
            with self.assertRaisesRegex(OAuthConsentRequired, "timeout"):
                await until_cancelled(wait(), None, timeout=.01)
        asyncio.run(run())
        self.assertEqual(closed, [True, True])

    def test_loopback_callback_rejects_wrong_state_host_and_duplicate_code(self):
        async def run():
            async with LoopbackOAuth(self.entry["url"], opener=lambda u: True) as flow:
                await flow.redirect("https://auth.example/authorize?state=abcdefghijklmnopqrstuv")
                async with httpx2.AsyncClient(trust_env=False) as client:
                    for query, host in (("state=wrong&code=x", None),
                                        ("state=abcdefghijklmnopqrstuv&code=x&code=y", None),
                                        ("state=abcdefghijklmnopqrstuv&code=x", "evil.example")):
                        reply = await client.get(flow.redirect_uri + "?" + query, headers={"Host": host} if host else {})
                        self.assertEqual(reply.status_code, 400)
                        self.assertFalse(flow.result.done())
                    reply = await client.get(flow.redirect_uri + "?state=abcdefghijklmnopqrstuv&code=FIXTURE_CODE")
                    self.assertEqual(reply.status_code, 200)
                    self.assertNotIn("FIXTURE_CODE", reply.text)
                result = await flow.callback()
                self.assertEqual(result.code, "FIXTURE_CODE")
                port = urlsplit(flow.redirect_uri).port
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
        asyncio.run(run())

    def test_operator_oauth_never_enables_server_or_grants_tools(self):
        registry = MCPRegistry(self.root / "mcp.json")
        registry.add_http("service", self.entry["url"])
        registry.set_enabled("service", True)
        class Transport:
            def authorize(inner, entry, **kwargs):
                return [{"name": "read", "input_schema": {"type": "object"}}]
        result = perform_mcp_command(registry, MCPCommand("oauth_authorize", "service"),
                                    transport=Transport(), vault=self.vault)
        self.assertTrue(result["success"])
        self.assertEqual(registry.get_server("service")["credential_source"], "oauth")
        self.assertFalse(registry.get_server("service")["enabled"])
        self.assertEqual(registry.exposed_tools(), [])

    def test_oversized_escaped_credential_fails_before_os_write(self):
        with self.assertRaisesRegex(ValueError, "too_large"):
            self.vault.write("service", self.entry["url"], "\\" * 60000)
        self.assertEqual(self.vault._backend.items, {})

    def test_real_sdk_mcp_session_uses_owned_http_client_and_cached_oauth(self):
        self.seed(expires=99999999999)
        calls = []
        actual_client = httpx2.AsyncClient
        def handler(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer OLD_FIXTURE")
            if request.method == "GET":
                return httpx2.Response(405)
            if request.method == "DELETE":
                return httpx2.Response(200)
            packet = json.loads(request.content)
            method = packet.get("method")
            calls.append(method)
            if method == "initialize":
                result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fixture", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
            else:
                return httpx2.Response(202)
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result},
                                   headers={"Mcp-Session-Id": "fixture_session"})
        def factory(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            self.assertEqual(kwargs["headers"], {})
            return actual_client(transport=httpx2.MockTransport(handler), **kwargs)
        async def run():
            return await asyncio.wait_for(OfficialMCPTransport._with_session({**self.entry, "credential_source": "oauth"},
                "discover", vault=self.vault), 3)
        with patch("httpx2.AsyncClient", side_effect=factory):
            tools = asyncio.run(run())
        self.assertEqual(tools[0]["name"], "read")
        self.assertEqual(calls.count("tools/list"), 1)
