"""Real loopback callback, fake Google HTTP and protected fixture vault."""
import asyncio
import base64
from contextlib import contextmanager
import hashlib
import json
import socket
import threading
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2

from jarvis_agent.google_workspace_catalog import IDENTITY_SCOPES, SCOPES
from jarvis_agent.google_workspace_oauth import (AUTH_URL, TOKEN_URL, USERINFO_URL, GoogleConnectionError,
    GoogleDesktopOAuth, account_record, parse_client, token_record)
from jarvis_agent.mcp_oauth import OAuthConsentRequired
from tests.google_workspace_fixtures import CLIENT, PROFILE, GoogleFixture


class GoogleOAuthTests(GoogleFixture):
    @contextmanager
    def closed_loopback(self):
        """Check the real listener, independently of OS closed-port errors.

        Windows may time out a connection to a closed port where Linux
        refuses it immediately. Neither outcome is the listener contract.
        The callback requests themselves still use actual loopback TCP.
        """
        listeners = []
        start_server = asyncio.start_server
        async def capture(*args, **kwargs):
            listener = await start_server(*args, **kwargs)
            listeners.append(listener)
            return listener
        with patch("jarvis_agent.google_workspace_oauth.asyncio.start_server", side_effect=capture):
            yield
        self.assertEqual(len(listeners), 1)
        self.assertFalse(listeners[0].is_serving())
        self.assertFalse(listeners[0].sockets)

    def test_desktop_client_validation_does_not_accept_web_service_account_or_external_endpoints(self):
        self.assertEqual(parse_client(json.dumps({"installed": CLIENT})), CLIENT)
        for value in ([], None, {"installed": []}, {"web": CLIENT}, {"type": "service_account"},
                {"installed": {**CLIENT, "token_uri": "https://evil.test/token"}},
                {"installed": {**CLIENT, "auth_uri": "https://evil.test/auth"}},
                {"installed": {**CLIENT, "client_secret": "x\r\ny"}}):
            with self.subTest(value=type(value)), self.assertRaisesRegex(GoogleConnectionError, "desktop_client"):
                parse_client(json.dumps(value))

    def test_invalid_tokens_and_identity_fail_closed(self):
        valid = {"access_token": "ACCESS", "refresh_token": "REFRESH", "expires_in": 3600}
        record = token_record(valid, SCOPES.values(), clock=lambda: 100)
        self.assertEqual(record["expires_at"], 3700)
        self.assertEqual(set(record["scopes"]), set(SCOPES.values()))
        for change in ({"access_token": "x\ny"}, {"refresh_token": ""}, {"expires_in": True},
                {"expires_in": float("nan")}, {"expires_in": 0}, {"token_type": "other"}):
            with self.subTest(change=change), self.assertRaises(GoogleConnectionError):
                token_record({**valid, **change}, SCOPES.values())
        for profile in ([], {}, {**PROFILE, "verified_email": False}, {**PROFILE, "email": "bad\nx@example.test"}):
            with self.assertRaises(GoogleConnectionError):
                account_record(profile)

    def exercise_flow(self, *, deny=False, wrong_first=False, duplicate_host=False):
        requests, urls, stages, callback_status = [], [], [], []
        def opener(url):
            urls.append(url)
            query = parse_qs(urlsplit(url).query)
            redirect = query["redirect_uri"][0]
            values = {"state": query["state"][0], **({"error": "access_denied"} if deny else {"code": "PRIVATE_CODE_FIXTURE"})}
            with httpx2.Client(trust_env=False, timeout=3) as browser:
                if wrong_first:
                    callback_status.append(browser.get(redirect, params={**values, "state": "wrong"}).status_code)
                if duplicate_host:
                    host = urlsplit(redirect).netloc
                    with socket.create_connection(("127.0.0.1", urlsplit(redirect).port), timeout=3) as raw:
                        raw.sendall(("GET /?" + urlencode(values) + " HTTP/1.1\r\nHost: " + host + "\r\nhost: " + host + "\r\n\r\n").encode())
                        callback_status.append(int(raw.recv(1000).split(b" ")[1]))
                response = browser.get(redirect, params=values)
                callback_status.append(response.status_code)
                self.assertNotIn("PRIVATE_CODE_FIXTURE", response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
            return True
        def handler(request):
            requests.append(request)
            if str(request.url) == TOKEN_URL:
                packet = parse_qs(request.content.decode())
                query = parse_qs(urlsplit(urls[0]).query)
                verifier = packet["code_verifier"][0]
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                self.assertEqual(challenge, query["code_challenge"][0])
                self.assertEqual(packet["redirect_uri"], query["redirect_uri"])
                self.assertEqual(packet["code"], ["PRIVATE_CODE_FIXTURE"])
                return httpx2.Response(200, json={"access_token": "ACCESS_FIXTURE", "refresh_token": "REFRESH_FIXTURE", "expires_in": 3600})
            self.assertEqual(str(request.url), USERINFO_URL)
            self.assertEqual(request.headers["Authorization"], "Bearer ACCESS_FIXTURE")
            return httpx2.Response(200, json=PROFILE)
        flow = GoogleDesktopOAuth(opener=opener, transport=httpx2.MockTransport(handler), clock=lambda: 100)
        if deny:
            with self.closed_loopback(), self.assertRaisesRegex(OAuthConsentRequired, "google_consent_denied"):
                flow.authorize(CLIENT, timeout=5)
            self.assertEqual(requests, [])
            return
        with self.closed_loopback():
            result = flow.authorize(CLIENT, progress=lambda p: stages.append(p["stage"]), timeout=5)
        query = parse_qs(urlsplit(urls[0]).query)
        self.assertTrue(urls[0].startswith(AUTH_URL + "?"))
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(set(query["scope"][0].split()), IDENTITY_SCOPES | set(SCOPES.values()))
        self.assertIn("select_account", query["prompt"][0])
        self.assertEqual(result["account"], account_record(PROFILE))
        self.assertEqual(result["token"]["expires_at"], 3700)
        self.assertEqual(len(requests), 2)  # No Gmail/Drive/Calendar read.
        self.assertEqual(callback_status[-1], 200)
        if wrong_first or duplicate_host:
            self.assertIn(400, callback_status)
        self.assertIn("callback_received", stages)
        self.assertFalse(self.registry.path.exists())

    def test_pkce_offline_google_only_and_no_business_data_read(self):
        self.exercise_flow()

    def test_wrong_state_does_not_consume_callback(self):
        self.exercise_flow(wrong_first=True)

    def test_duplicate_host_rejected_before_valid_callback(self):
        self.exercise_flow(duplicate_host=True)

    def test_consent_denial_makes_no_token_request(self):
        self.exercise_flow(deny=True)

    def test_browser_failure_does_not_wait_for_timeout(self):
        with self.closed_loopback(), self.assertRaisesRegex(GoogleConnectionError, "browser_open_failed"):
            GoogleDesktopOAuth(opener=lambda _: False).authorize(CLIENT, timeout=1)

    def test_cancel_closes_loopback_without_storage_or_token_request(self):
        stop = threading.Event()
        def opener(_):
            stop.set()
            return True
        transport = httpx2.MockTransport(lambda _: self.fail("no Google request after cancel"))
        with self.closed_loopback(), self.assertRaisesRegex(OAuthConsentRequired, "oauth_cancelled"):
            GoogleDesktopOAuth(opener=opener, transport=transport).authorize(CLIENT, stop_event=stop, timeout=2)

    def test_returned_scopes_cannot_add_unrequested_authority(self):
        data = {"access_token": "A", "refresh_token": "R", "expires_in": 60, "scope": SCOPES["gmail"] + " https://evil.test/admin"}
        self.assertEqual(token_record(data, SCOPES.values())["scopes"], [SCOPES["gmail"]])
