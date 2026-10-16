"""Desktop Google OAuth: PKCE, loopback, offline access, no SaaS intermediary.

Design references: gogcli's offline/PKCE flow and Hermes' Google setup. This is
Jarvis-owned implementation; no upstream shell commands or token files run.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import secrets
import time
import webbrowser
from urllib.parse import parse_qs, urlencode, urlsplit

from .google_workspace_catalog import GOOGLE_TARGET, IDENTITY_SCOPES, SCOPES
from .mcp_oauth import OAuthConsentRequired, until_cancelled

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = GOOGLE_TARGET + "/oauth2/v2/userinfo"


class GoogleConnectionError(ValueError):
    """Only application-owned reason codes; no provider body or URL."""


def parse_client(raw):
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("installed"), dict):
            raise ValueError
        client = data["installed"]
        cid, secret = client["client_id"], client.get("client_secret", "")
        if (not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9._-]{10,1000}\.apps\.googleusercontent\.com", cid)
                or not isinstance(secret, str) or len(secret) > 2000 or any(c in secret for c in "\r\n\x00")
                or client.get("auth_uri", AUTH_URL) not in {AUTH_URL, "https://accounts.google.com/o/oauth2/auth"}
                or client.get("token_uri", TOKEN_URL) != TOKEN_URL):
            raise ValueError
        return {"client_id": cid, "client_secret": secret,
                "project_id": str(client.get("project_id") or "")[:200]}
    except (ValueError, TypeError, KeyError):
        raise GoogleConnectionError("google_desktop_client_required") from None


def token_record(data, requested_scopes, *, refresh_token="", clock=time.time):
    if not isinstance(data, dict):
        raise GoogleConnectionError("google_token_invalid")
    access = data.get("access_token")
    refresh = data.get("refresh_token") or refresh_token
    expires = data.get("expires_in")
    scope = data.get("scope")
    scopes = scope.split() if isinstance(scope, str) else list(requested_scopes) if scope is None else []
    if (not isinstance(access, str) or not 1 <= len(access) <= 12000
            or any(c in access for c in "\r\n\x00")
            or str(data.get("token_type", "Bearer")).lower() != "bearer"
            or type(expires) not in (int, float) or not math.isfinite(expires) or not 0 < expires <= 86400
            or not isinstance(refresh, str) or not 1 <= len(refresh) <= 12000
            or any(c in refresh for c in "\r\n\x00") or len(scopes) > 30):
        raise GoogleConnectionError("google_token_invalid")
    return {"access_token": access, "refresh_token": refresh,
            "expires_at": clock() + expires, "scopes": sorted(set(scopes) & set(requested_scopes))}


def account_record(profile):
    if not isinstance(profile, dict):
        raise GoogleConnectionError("google_identity_unverified")
    email, subject = profile.get("email"), profile.get("id")
    if (profile.get("verified_email") is not True or not isinstance(email, str)
            or not re.fullmatch(r"[^\s@]{1,200}@[^\s@]{1,120}", email)
            or not isinstance(subject, str) or not 1 <= len(subject) <= 200):
        raise GoogleConnectionError("google_identity_unverified")
    return {"email": email, "subject_hash": hashlib.sha256(subject.encode()).hexdigest()}


async def json_response(response):
    """Never reflect remote error bodies (which can contain credentials)."""
    response.raise_for_status()
    if len(response.content) > 100000:
        raise GoogleConnectionError("google_response_too_large")
    data = response.json()
    if not isinstance(data, dict):
        raise GoogleConnectionError("google_response_invalid")
    return data


class GoogleDesktopOAuth:
    def __init__(self, *, opener=None, transport=None, clock=time.time):
        self.opener = opener or webbrowser.open
        self.transport, self.clock = transport, clock
        self.redirect_uri = ""

    def authorize(self, client, *, stop_event=None, progress=None, timeout=600):
        return asyncio.run(until_cancelled(self._authorize(client, progress), stop_event, timeout=timeout))

    async def _authorize(self, client, progress):
        import httpx2  # Optional connector dependency; never needed to boot Qt.
        def stage(value):
            if progress:
                progress({"stage": value})
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        callback = asyncio.get_running_loop().create_future()

        async def handle(reader, writer):
            status, message = "400 Bad Request", "Retour invalide. Reviens dans Jarvis."
            try:
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
                if len(header) > 8192:
                    raise ValueError
                lines = header.decode("ascii").split("\r\n")
                method, target, version = lines[0].split(" ")
                hosts = [line.split(":", 1)[1].strip() for line in lines[1:]
                         if line.split(":", 1)[0].lower() == "host"]
                host = hosts[0] if len(hosts) == 1 else ""
                parsed = urlsplit(target)
                query = parse_qs(parsed.query, keep_blank_values=True)
                if (method != "GET" or version not in {"HTTP/1.0", "HTTP/1.1"} or parsed.path != "/" or parsed.netloc or parsed.scheme
                        or host != urlsplit(self.redirect_uri).netloc or callback.done()
                        or len(query.get("state", [])) != 1
                        or not secrets.compare_digest(query["state"][0], state)):
                    raise ValueError
                if "error" in query:
                    callback.set_exception(OAuthConsentRequired("google_consent_denied"))
                    status, message = "200 OK", "Autorisation refusée. Reviens dans Jarvis."
                elif len(query.get("code", [])) == 1 and 1 <= len(query["code"][0]) <= 6000:
                    callback.set_result(query["code"][0])
                    status, message = "200 OK", "Autorisation reçue. Reviens dans Jarvis pour vérifier la fin de connexion."
            except (ValueError, TypeError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                pass
            finally:
                body = message.encode("utf-8")
                writer.write(("HTTP/1.1 " + status + "\r\nContent-Type: text/plain; charset=utf-8\r\n"
                    "Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n"
                    "Content-Security-Policy: default-src 'none'\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)) + "\r\n\r\n").encode() + body)
                try:
                    await writer.drain()
                except (OSError, ConnectionError):
                    pass
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ConnectionError):
                    pass

        listener = await asyncio.start_server(handle, "127.0.0.1", 0, limit=8192)
        port = listener.sockets[0].getsockname()[1]
        self.redirect_uri = f"http://127.0.0.1:{port}/"
        requested = sorted(IDENTITY_SCOPES | set(SCOPES.values()))
        try:
            url = AUTH_URL + "?" + urlencode({"client_id": client["client_id"], "redirect_uri": self.redirect_uri,
                "response_type": "code", "scope": " ".join(requested), "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256",
                "access_type": "offline", "prompt": "consent select_account"})
            stage("waiting_consent")
            if not await asyncio.to_thread(self.opener, url):
                raise GoogleConnectionError("google_browser_open_failed")
            code = await callback
            stage("callback_received")
            async with httpx2.AsyncClient(transport=self.transport, trust_env=False,
                    follow_redirects=False, timeout=httpx2.Timeout(30, connect=10)) as http:
                stage("token_exchange")
                response = await http.post(TOKEN_URL, data={"client_id": client["client_id"],
                    "client_secret": client.get("client_secret", ""), "code": code,
                    "code_verifier": verifier, "redirect_uri": self.redirect_uri, "grant_type": "authorization_code"})
                token = token_record(await json_response(response), requested, clock=self.clock)
                stage("session_initializing")
                response = await http.get(USERINFO_URL, headers={"Authorization": "Bearer " + token["access_token"]})
                account = account_record(await json_response(response))
            return {"version": 1, "client": client, "token": token, "account": account}
        finally:
            listener.close()
            await listener.wait_closed()
            if not callback.done():
                callback.cancel()
