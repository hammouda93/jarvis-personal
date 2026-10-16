"""Explicit MCP OAuth using the official SDK, with OS-protected storage.

The model never opens a browser, registers a client or upgrades OAuth scopes.
No personal account is contacted until an operator requests authorization.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .mcp_credentials import CredentialUnavailable, CredentialVault


class OAuthConsentRequired(RuntimeError):
    pass


OAUTH_STAGES = frozenset({"preparing", "metadata", "registration", "waiting_consent", "callback_received",
    "token_exchange", "session_initializing", "tools_discovery", "credentials_storage", "authorized", "service_inventory"})
OAUTH_REASONS = frozenset({"oauth_cancelled", "oauth_timeout", "oauth_token_exchange_failed", "oauth_connection_failed"})
OAUTH_ERRORS = frozenset({"invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
    "unsupported_grant_type", "invalid_scope", "access_denied", "server_error", "temporarily_unavailable"})
_ERROR_TYPES = frozenset({"OAuthFlowError", "OAuthTokenError", "OAuthRegistrationError", "ValidationError",
    "TimeoutError", "ReadTimeout", "ConnectTimeout", "ConnectError", "HTTPStatusError", "MCPError",
    "CredentialUnavailable", "OAuthConsentRequired", "MCPUnavailable", "OSError", "RuntimeError", "ValueError"})


def connection_diagnostic(exc, *, stage="preparing"):
    """Only local enums/status numbers. Never retain exception text or URLs."""
    pending, seen, details = [exc], set(), {}
    while pending and len(seen) < 32:
        item = pending.pop(0)
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, OAuthConnectionFailure):
            return dict(item.diagnostic)
        if isinstance(item, OAuthConsentRequired) and str(item) in {"oauth_cancelled", "oauth_timeout"}:
            details["reason"] = str(item)
        name = type(item).__name__
        if name in _ERROR_TYPES and "error_type" not in details:
            details["error_type"] = name
        status = getattr(getattr(item, "response", None), "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            details["http_status"] = status
        pending.extend(getattr(item, "exceptions", ()))
        if item.__cause__ is not None:
            pending.append(item.__cause__)
    details["stage"] = stage if stage in OAUTH_STAGES else "preparing"
    return details


class OAuthConnectionFailure(RuntimeError):
    def __init__(self, reason, diagnostic):
        self.reason = reason if reason in OAUTH_REASONS else "oauth_connection_failed"
        self.diagnostic = {"reason": self.reason,
            "stage": diagnostic.get("stage") if diagnostic.get("stage") in OAUTH_STAGES else "preparing"}
        if diagnostic.get("error_type") in _ERROR_TYPES:
            self.diagnostic["error_type"] = diagnostic["error_type"]
        status = diagnostic.get("http_status")
        if type(status) is int and 100 <= status <= 599:
            self.diagnostic["http_status"] = status
        if diagnostic.get("oauth_error") in OAUTH_ERRORS:
            self.diagnostic["oauth_error"] = diagnostic["oauth_error"]
        super().__init__(self.reason)


def oauth_options(value: str = "") -> dict:
    packet = json.loads(value) if value else {}
    keys = {"client_id", "client_secret", "scope", "callback_port", "auth_method"}
    if not isinstance(packet, dict) or set(packet) - keys:
        raise ValueError("invalid_oauth_options")
    for key, maximum in (("client_id", 1200), ("client_secret", 6000), ("scope", 600)):
        text = packet.get(key, "")
        if not isinstance(text, str) or len(text) > maximum or any(c in text for c in "\r\n\x00"):
            raise ValueError("invalid_oauth_options")
    method = packet.get("auth_method", "client_secret_basic" if packet.get("client_secret") else "none")
    port = packet.get("callback_port", 8973)
    if (method not in {"none", "client_secret_basic", "client_secret_post"}
            or isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535
            or packet.get("client_secret") and not packet.get("client_id")
            or method != "none" and not packet.get("client_secret")
            or len(packet.get("scope", "").split()) > 12):
        raise ValueError("invalid_oauth_options")
    return {**packet, "auth_method": method, "callback_port": port}


def secure_oauth_url(url: str, server_url: str) -> bool:
    try:
        parsed, server = urlsplit(str(url)), urlsplit(server_url)
        return bool(parsed.hostname and not parsed.username and not parsed.password
                    and not parsed.fragment and not any(c in str(url) for c in "\r\n\x00")
                    and (parsed.scheme == "https" or (
                        parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                        and server.hostname in {"localhost", "127.0.0.1", "::1"})))
    except ValueError:
        return False


class VaultTokenStorage:
    """SDK TokenStorage; absolute expiry survives process restarts."""

    def __init__(self, entry: dict, *, vault=None, staging=False, clock=time.time):
        self.server = str(entry["id"])
        self.url = str(entry["url"])
        self.vault = vault or CredentialVault(entry.get("_credential_root"))
        self.staging = staging
        self.clock = clock
        self.context = None
        self.pending = {}

    def _read(self, purpose):
        if self.staging:
            return self.pending.get(purpose)
        raw = self.vault.read(self.server, self.url, purpose)
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, TypeError) as exc:
            raise CredentialUnavailable("invalid_oauth_storage") from exc

    def _write(self, purpose, data):
        raw = json.dumps(data, ensure_ascii=False)
        if self.staging:
            if len(raw.encode("utf-8")) > 60000:
                raise CredentialUnavailable("oauth_record_too_large")
            self.pending[purpose] = data
        else:
            self.vault.write(self.server, self.url, raw, purpose)

    def _save_metadata(self):
        meta = getattr(self.context, "oauth_metadata", None)
        if meta is None or not meta.token_endpoint or not meta.issuer:
            raise OAuthConsentRequired("oauth_metadata_required")
        for url in (meta.issuer, meta.token_endpoint, meta.authorization_endpoint, meta.registration_endpoint):
            if url and not secure_oauth_url(str(url), self.url):
                raise OAuthConsentRequired("unsafe_oauth_endpoint")
        self._write("oauth_metadata", {"metadata": meta.model_dump(mode="json"),
                    "auth_server_url": str(self.context.auth_server_url or meta.issuer)})

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        record = self._read("oauth_tokens")
        if record is None:
            return None
        try:
            token = OAuthToken.model_validate(record["tokens"])
            expires = record.get("expires_at")
            if expires is not None:
                token.expires_in = max(0, int(float(expires) - self.clock()))
            if any(c in token.access_token for c in "\r\n\x00"):
                raise ValueError
            return token
        except (ValueError, TypeError, KeyError) as exc:
            raise CredentialUnavailable("invalid_oauth_tokens") from exc

    async def set_tokens(self, tokens):
        self._save_metadata()
        if any(c in tokens.access_token for c in "\r\n\x00"):
            raise OAuthConsentRequired("invalid_oauth_token")
        # Some providers rotate refresh tokens; others omit them on refresh.
        old = self._read("oauth_tokens")
        if not tokens.refresh_token and old:
            tokens = tokens.model_copy(update={"refresh_token": old.get("tokens", {}).get("refresh_token")})
        self._write("oauth_tokens", {"tokens": tokens.model_dump(mode="json"),
                    "expires_at": self.clock() + tokens.expires_in if tokens.expires_in is not None else None})

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        record = self._read("oauth_client")
        return OAuthClientInformationFull.model_validate(record) if record is not None else None

    async def set_client_info(self, client_info):
        self._save_metadata()
        self._write("oauth_client", client_info.model_dump(mode="json"))

    def commit(self):
        if not self.staging or not all(p in self.pending for p in ("oauth_metadata", "oauth_client", "oauth_tokens")):
            raise OAuthConsentRequired("oauth_authorization_incomplete")
        for purpose in ("oauth_metadata", "oauth_client", "oauth_tokens"):
            self.vault.write(self.server, self.url, json.dumps(self.pending[purpose]), purpose)
        self.pending.clear()


class LoopbackOAuth:
    """Bounded callback listener. Authorization codes never enter logs or Qt."""

    def __init__(self, server_url: str, *, opener=None, options=None, progress=None):
        self.server_url = server_url
        self.opener = opener or webbrowser.open
        self.options = oauth_options(json.dumps(options or {}))
        self.path = "/oauth/callback" if self.options.get("client_id") else "/oauth/" + secrets.token_urlsafe(24)
        self.expected_state = ""
        self.server = None
        self.result = None
        self.progress = progress
        self.stage = "preparing"

    def set_stage(self, stage):
        if stage in OAUTH_STAGES and stage != self.stage:
            self.stage = stage
            if self.progress is not None:
                self.progress({"stage": stage})

    async def __aenter__(self):
        self.result = asyncio.get_running_loop().create_future()
        port = self.options["callback_port"] if self.options.get("client_id") else 0
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", port, limit=8192)
        port = self.server.sockets[0].getsockname()[1]
        self.redirect_uri = f"http://127.0.0.1:{port}{self.path}"
        return self

    async def __aexit__(self, *args):
        self.server.close()
        await self.server.wait_closed()
        if not self.result.done():
            self.result.cancel()

    async def redirect(self, url):
        if not secure_oauth_url(url, self.server_url):
            raise OAuthConsentRequired("unsafe_oauth_authorization_url")
        values = parse_qs(urlsplit(url).query)
        state = values.get("state", [])
        if len(state) != 1 or not 16 <= len(state[0]) <= 200:
            raise OAuthConsentRequired("invalid_oauth_state")
        self.expected_state = state[0]
        if not await asyncio.to_thread(self.opener, url):
            raise OAuthConsentRequired("oauth_browser_unavailable")

    async def callback(self):
        return await self.result

    async def _handle(self, reader, writer):
        status = "400 Bad Request"
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            lines = raw.decode("ascii").split("\r\n")
            method, target, version = lines[0].split(" ")
            parsed = urlsplit(target)
            headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            host = next((v.strip() for k, v in headers.items() if k.lower() == "host"), "")
            values = parse_qs(parsed.query, max_num_fields=8)
            state, code = values.get("state", []), values.get("code", [])
            issuer = values.get("iss", [])
            valid = (method == "GET" and version == "HTTP/1.1"
                     and not parsed.scheme and not parsed.netloc and parsed.path == self.path
                     and host == urlsplit(self.redirect_uri).netloc and not self.result.done()
                     and len(state) == 1 and len(code) == 1 and len(issuer) <= 1
                     and self.expected_state and secrets.compare_digest(state[0], self.expected_state)
                     and 1 <= len(code[0]) <= 2000 and not values.get("error"))
            if valid:
                from mcp.shared.auth import AuthorizationCodeResult
                self.result.set_result(AuthorizationCodeResult(code=code[0], state=state[0], iss=issuer[0] if issuer else None))
                self.set_stage("callback_received")
                status = "200 OK"
        except (ValueError, UnicodeError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            body = (b"Jarvis: authorization response received. Return to Jarvis to check the connection result."
                    if status == "200 OK" else
                    b"Jarvis: authorization response rejected. Start a new connection from Jarvis.")
            writer.write((f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\n"
                          f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode("ascii") + body)
            try:
                await writer.drain()
            except ConnectionError:
                pass
            writer.close()
            await writer.wait_closed()


def build_oauth_provider(entry: dict, *, flow=None, vault=None):
    """Small SDK extension: cold expiry, metadata and non-interactive gates.

    These are adaptation principles from Hermes, not a copied agent runtime.
    """
    from mcp.client.auth.oauth2 import OAuthClientProvider
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata
    import anyio
    import httpx2

    class PrivateOAuthLogs(logging.Filter):
        def filter(self, record):
            record.msg = "MCP OAuth SDK diagnostic (sensitive details suppressed)"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            return True

    logger = logging.getLogger("mcp.client.auth.oauth2")
    if not any(getattr(item, "_jarvis_private_oauth", False) for item in logger.filters):
        private = PrivateOAuthLogs()
        private._jarvis_private_oauth = True
        logger.addFilter(private)

    storage = VaultTokenStorage(entry, vault=vault, staging=flow is not None)
    uri = flow.redirect_uri if flow else "http://127.0.0.1/oauth/unused"
    options = getattr(flow, "options", {}) if flow else {}

    class JarvisOAuthProvider(OAuthClientProvider):
        def _expected_issuer(self):
            issuer = super()._expected_issuer()
            # Explicitly supplied client credentials are bound only after
            # discovery identifies the issuer; never guess a DCR endpoint.
            if options.get("client_id") and self.context.client_info is None:
                self.context.client_info = OAuthClientInformationFull(client_id=options["client_id"],
                    client_secret=options.get("client_secret") or None, issuer=issuer,
                    redirect_uris=[uri], token_endpoint_auth_method=options["auth_method"])
            return issuer

        async def _perform_authorization(self):
            if flow is None:
                raise OAuthConsentRequired("oauth_operator_consent_required")
            if options.get("scope"):
                self.context.client_metadata.scope = options["scope"].strip()
            if options.get("client_id"):
                await storage.set_client_info(self.context.client_info)
            if hasattr(flow, "set_stage"):
                flow.set_stage("waiting_consent")
            return await super()._perform_authorization()

        async def _exchange_token_authorization_code(self, auth_code, code_verifier):
            if hasattr(flow, "set_stage"):
                flow.set_stage("token_exchange")
            return await super()._exchange_token_authorization_code(auth_code, code_verifier)

        async def _handle_token_response(self, response):
            try:
                await super()._handle_token_response(response)
            except Exception as exc:
                diagnostic = connection_diagnostic(exc, stage="token_exchange")
                diagnostic["http_status"] = response.status_code
                try:
                    data = response.json()
                    code = data.get("error") if isinstance(data, dict) else None
                    if isinstance(code, str) and code in OAUTH_ERRORS:
                        diagnostic["oauth_error"] = code
                except (ValueError, TypeError, httpx2.ResponseNotRead):
                    pass
                raise OAuthConnectionFailure("oauth_token_exchange_failed", diagnostic) from None
            if hasattr(flow, "set_stage"):
                flow.set_stage("session_initializing")

        async def _initialize(self):
            await super()._initialize()
            saved = storage._read("oauth_metadata")
            if saved:
                self.context.oauth_metadata = OAuthMetadata.model_validate(saved["metadata"])
                self.context.auth_server_url = saved["auth_server_url"]
                meta = self.context.oauth_metadata
                client = self.context.client_info
                if (client is not None and str(client.issuer or "") != str(meta.issuer)
                        or not all(secure_oauth_url(str(url), storage.url) for url in
                                   (meta.issuer, meta.token_endpoint, meta.authorization_endpoint) if url)):
                    raise OAuthConsentRequired("oauth_issuer_binding_invalid")
            tokens = self.context.current_tokens
            if tokens is not None and tokens.expires_in is not None:
                self.context.token_expiry_time = storage.clock() + tokens.expires_in if tokens.expires_in > 0 else storage.clock() - 1
            if flow is None and (tokens is None or self.context.client_info is None or saved is None):
                raise OAuthConsentRequired("oauth_operator_consent_required")

        async def async_auth_flow(self, request):
            # Preserve the SDK's bidirectional generator; do not discard HTTP
            # responses via a plain async-for wrapper. No new DCR/scope grant
            # is allowed while a model call is using existing credentials.
            inner = super().async_auth_flow(request)
            try:
                outgoing = await inner.__anext__()
                while True:
                    if not secure_oauth_url(str(outgoing.url), storage.url):
                        raise OAuthConsentRequired("unsafe_oauth_endpoint")
                    if flow is None and outgoing.url != httpx2.URL(storage.url):
                        meta = self.context.oauth_metadata
                        if (not meta or outgoing.url != httpx2.URL(str(meta.token_endpoint))
                                or outgoing.method != "POST" or b"grant_type=refresh_token" not in outgoing.content):
                            raise OAuthConsentRequired("oauth_operator_consent_required")
                    if flow is not None and outgoing.method == "POST" and self.context.client_info is None:
                        meta = self.context.oauth_metadata
                        if meta is not None and not meta.registration_endpoint:
                            raise OAuthConsentRequired("oauth_preregistered_client_required")
                        if meta is not None and outgoing.url == httpx2.URL(str(meta.registration_endpoint)):
                            # Apply the operator's requested scopes to DCR as well
                            # as consent. PRM may advertise an empty scope list.
                            if options.get("scope"):
                                from mcp.client.auth.utils import create_client_registration_request
                                self.context.client_metadata.scope = options["scope"].strip()
                                outgoing = create_client_registration_request(meta, self.context.client_metadata,
                                    self.context.get_authorization_base_url(storage.url))
                            if hasattr(flow, "set_stage"):
                                flow.set_stage("registration")
                    elif flow is not None and outgoing.method == "GET" and hasattr(flow, "set_stage"):
                        flow.set_stage("metadata")
                    incoming = yield outgoing
                    outgoing = await inner.asend(incoming)
            except StopAsyncIteration:
                return
            finally:
                await inner.aclose()

    provider = JarvisOAuthProvider(server_url=storage.url,
        client_metadata=OAuthClientMetadata(redirect_uris=[uri], client_name="Jarvis Personal",
            token_endpoint_auth_method="none", grant_types=["authorization_code", "refresh_token"]),
        storage=storage, redirect_handler=flow.redirect if flow else None,
        callback_handler=flow.callback if flow else None)
    # Short-lived HTTP streaming requests can resume in another SDK task.
    provider.context.lock = anyio.Semaphore(1, max_value=1)
    storage.context = provider.context
    return provider, storage


async def guarded_http_request(request, server_url: str, *, oauth=False):
    """SDK redirects must not relocate bearer credentials or bypass TLS policy."""
    import httpx2
    if not secure_oauth_url(str(request.url), server_url):
        raise OAuthConsentRequired("unsafe_mcp_http_endpoint")
    if not oauth and request.url != httpx2.URL(server_url):
        raise OAuthConsentRequired("mcp_redirect_requires_reconfiguration")


async def until_cancelled(coroutine, stop_event, *, timeout=150):
    task = asyncio.create_task(coroutine)
    end = asyncio.get_running_loop().time() + timeout
    try:
        while not task.done():
            if stop_event is not None and stop_event.is_set():
                raise OAuthConsentRequired("oauth_cancelled")
            if asyncio.get_running_loop().time() >= end:
                raise OAuthConsentRequired("oauth_timeout")
            await asyncio.wait({task}, timeout=0.1)
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
