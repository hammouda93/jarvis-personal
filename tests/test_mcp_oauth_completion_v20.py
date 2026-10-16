"""Full SDK OAuth callback + MCP session, using only fixtures and loopback HTTP."""
import json
import io
import logging
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener

import httpx2

from jarvis_agent.gmail_connection import PIPEDREAM_ENDPOINT, PIPEDREAM_SCOPE
from jarvis_agent.mcp_credentials import CredentialVault
from jarvis_agent.mcp_sdk_transport import OfficialMCPTransport
from jarvis_agent.mcp_oauth import OAuthConnectionFailure, connection_diagnostic
from tests.test_mcp_oauth import MemoryStore


class OAuthCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = CredentialVault(Path(self.temp.name) / "vault", backend=MemoryStore())
        self.issuer = "https://mcp.pipedream.com"
        self.entry = {"id": "gmail", "url": PIPEDREAM_ENDPOINT, "kind": "http"}
        self.requests, self.callback_results = [], []
        self.redirect_uri = None
        self.registered_scope = ""
        self.token_error = None
        self.sdk_output = io.StringIO()
        handler = logging.StreamHandler(self.sdk_output)
        handler.setLevel(logging.ERROR)
        logger = logging.getLogger("mcp")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

    def handler(self, request):
        self.requests.append(request)
        url = str(request.url)
        if url == "https://mcp.pipedream.net/.well-known/oauth-protected-resource":
            return httpx2.Response(200, json={"resource": "https://mcp.pipedream.net",
                "authorization_servers": [self.issuer], "scopes_supported": []})
        if "oauth-protected-resource" in url:
            return httpx2.Response(404)
        if ".well-known/oauth-authorization-server" in url:
            return httpx2.Response(200, json={"issuer": self.issuer,
                "authorization_endpoint": self.issuer + "/api/oauth/authorize",
                "token_endpoint": self.issuer + "/api/oauth/token",
                "registration_endpoint": self.issuer + "/api/oauth/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["none"],
                "code_challenge_methods_supported": ["S256", "plain"],
                "scopes_supported": PIPEDREAM_SCOPE.split()})
        if url == self.issuer + "/api/oauth/register":
            data = json.loads(request.content)
            self.redirect_uri = data["redirect_uris"][0]
            self.registered_scope = data.get("scope", "")
            return httpx2.Response(201, json={**data, "client_id": "fixture-client"})
        if url == self.issuer + "/api/oauth/token":
            if self.token_error is not None:
                return httpx2.Response(400, json={"error": self.token_error, "error_description": "PRIVATE_FIXTURE"})
            if set(self.registered_scope.split()) != set(PIPEDREAM_SCOPE.split()):
                return httpx2.Response(400, json={"error": "invalid_scope", "error_description": "PRIVATE_FIXTURE"})
            return httpx2.Response(200, json={"access_token": "FIXTURE_ACCESS", "refresh_token": "FIXTURE_REFRESH",
                "token_type": "bearer", "expires_in": 3600, "scope": PIPEDREAM_SCOPE})
        self.assertEqual(url, PIPEDREAM_ENDPOINT)
        if request.headers.get("Authorization") != "Bearer FIXTURE_ACCESS":
            return httpx2.Response(401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
        if request.method == "GET":
            return httpx2.Response(405)
        if request.method == "DELETE":
            return httpx2.Response(204)
        data = json.loads(request.content)
        if data["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"}}
        elif data["method"] == "tools/list":
            result = {"tools": [{"name": "GMAIL-GET-CURRENT-USER", "inputSchema": {"type": "object"}}]}
        else:
            return httpx2.Response(202)
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "result": result},
            headers={"Mcp-Session-Id": "fixture-session"})

    def browser(self, url):
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["redirect_uri"], [self.redirect_uri])
        target = self.redirect_uri + "?" + urlencode({"code": "FIXTURE_CODE", "state": params["state"][0]})
        # This is the same HTTP callback shape as the live return: no `iss`.
        with build_opener(ProxyHandler({})).open(target, timeout=3) as response:
            self.callback_results.append((response.status, response.read().decode()))
        return True

    def test_pipedream_authorization_callback_session_and_vault_as_one_flow(self):
        actual_client = httpx2.AsyncClient
        def factory(**kwargs):
            return actual_client(transport=httpx2.MockTransport(self.handler), **kwargs)
        progress = []
        with patch("httpx2.AsyncClient", side_effect=factory):
            tools = OfficialMCPTransport().authorize(self.entry, vault=self.vault,
                opener=self.browser, options={"scope": PIPEDREAM_SCOPE}, progress=progress.append)
        self.assertEqual([t["name"] for t in tools], ["GMAIL-GET-CURRENT-USER"])
        self.assertEqual([r[0] for r in self.callback_results], [200])
        self.assertIn("FIXTURE_REFRESH", self.vault.read("gmail", PIPEDREAM_ENDPOINT, "oauth_tokens"))
        self.assertEqual(sum(str(r.url).endswith("/api/oauth/token") for r in self.requests), 1)
        self.assertFalse(any(json.loads(r.content).get("method") == "tools/call" for r in self.requests
            if str(r.url) == PIPEDREAM_ENDPOINT and r.content))

        self.assertEqual(set(self.registered_scope.split()), set(PIPEDREAM_SCOPE.split()))
        stages = [p["stage"] for p in progress]
        self.assertIn("callback_received", stages)
        self.assertLess(stages.index("callback_received"), stages.index("token_exchange"))
        self.assertEqual(stages[-1], "authorized")
        self.assertNotIn("FIXTURE_CODE", str(progress))
        self.assertNotIn("connected", self.callback_results[0][1].lower())

    def test_token_failure_reports_only_status_stage_and_known_code_without_storing_tokens(self):
        self.token_error = "invalid_grant"
        actual_client = httpx2.AsyncClient
        with patch("httpx2.AsyncClient", side_effect=lambda **kw:
            actual_client(transport=httpx2.MockTransport(self.handler), **kw)):
            with self.assertRaises(OAuthConnectionFailure) as caught:
                OfficialMCPTransport().authorize(self.entry, vault=self.vault,
                    opener=self.browser, options={"scope": PIPEDREAM_SCOPE})
        self.assertNotIn("PRIVATE", self.sdk_output.getvalue())
        details = connection_diagnostic(caught.exception)
        self.assertEqual(details["stage"], "token_exchange")
        self.assertEqual(details["http_status"], 400)
        self.assertEqual(details["oauth_error"], "invalid_grant")
        self.assertEqual(caught.exception.reason, "oauth_token_exchange_failed")
        self.assertNotIn("PRIVATE", json.dumps(details))
        self.assertNotIn("FIXTURE_CODE", json.dumps(details))
        self.assertIsNone(self.vault.read("gmail", PIPEDREAM_ENDPOINT, "oauth_tokens"))
        self.assertEqual(self.callback_results[0][0], 200)

    def test_cancellation_is_named_and_does_not_open_browser_or_write_credentials(self):
        stop = threading.Event()
        stop.set()
        with patch("webbrowser.open") as browser, self.assertRaises(OAuthConnectionFailure) as caught:
            OfficialMCPTransport().authorize(self.entry, stop_event=stop, vault=self.vault)
        browser.assert_not_called()
        self.assertEqual(caught.exception.reason, "oauth_cancelled")
        self.assertIsNone(self.vault.read("gmail", PIPEDREAM_ENDPOINT, "oauth_tokens"))

    def test_interactive_budget_is_ten_minutes_without_extending_tool_timeouts(self):
        actual_client = httpx2.AsyncClient
        budgets = []
        async def check(coroutine, stop_event, *, timeout):
            budgets.append(timeout)
            return await coroutine
        with patch("httpx2.AsyncClient", side_effect=lambda **kw:
            actual_client(transport=httpx2.MockTransport(self.handler), **kw)), patch(
            "jarvis_agent.mcp_oauth.until_cancelled", side_effect=check):
            OfficialMCPTransport().authorize(self.entry, vault=self.vault,
                opener=self.browser, options={"scope": PIPEDREAM_SCOPE})
        self.assertEqual(budgets, [600])

    def test_unrecognized_oauth_error_and_remote_body_are_not_public_diagnostics(self):
        self.token_error = "PRIVATE_SECRET_IN_ERROR_FIELD"
        actual_client = httpx2.AsyncClient
        with patch("httpx2.AsyncClient", side_effect=lambda **kw:
            actual_client(transport=httpx2.MockTransport(self.handler), **kw)):
            with self.assertRaises(OAuthConnectionFailure) as caught:
                OfficialMCPTransport().authorize(self.entry, vault=self.vault,
                    opener=self.browser, options={"scope": PIPEDREAM_SCOPE})
        self.assertNotIn("PRIVATE", self.sdk_output.getvalue())
        details = connection_diagnostic(caught.exception)
        self.assertEqual(details["stage"], "token_exchange")
        self.assertEqual(details["http_status"], 400)
        self.assertNotIn("oauth_error", details)
        self.assertNotIn("PRIVATE", json.dumps(details))
