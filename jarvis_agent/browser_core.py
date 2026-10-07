"""Generic tab-scoped browser primitives for the user's Chrome profile.

No launching, copying profiles, desktop input, or retry of ambiguous mutations.
"""
from __future__ import annotations

import json
import socket
import struct
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit


MAX_MESSAGE = 900_000
OPERATIONS = frozenset({"list_tabs", "get_active_tab", "activate_tab", "navigate",
                        "observe_dom", "find", "click", "write", "select", "press", "back",
                        "forward", "close_tab", "download", "verify"})


def read_packet(stream):
    def exact(count):
        chunks = bytearray()
        while len(chunks) < count:
            chunk = stream.read(count - len(chunks))
            if not chunk:
                raise EOFError("bridge_disconnected")
            chunks.extend(chunk)
        return bytes(chunks)

    size = struct.unpack("=I", exact(4))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError("invalid_bridge_message_size")
    value = json.loads(exact(size).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("bridge_message_must_be_object")
    return value


def write_packet(stream, value):
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(body) <= MAX_MESSAGE:
        raise ValueError("bridge_message_too_large")
    stream.write(struct.pack("=I", len(body)) + body)
    stream.flush()


def http_url(value):
    parsed = urlsplit(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("only_http_https_without_credentials")
    return str(value)


class NativeBrowserTransport:
    def __init__(self, config_path, *, timeout_s=5.0):
        self.config_path = Path(config_path)
        self.timeout_s = timeout_s

    def request(self, operation, arguments):
        config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        request_id = uuid.uuid4().hex
        request = {"id": request_id, "version": 1, "token": config["token"],
                   "operation": operation, "arguments": arguments,
                   "deadline_ms": int((time.time() + self.timeout_s) * 1000)}
        connected = False
        dispatched = False
        try:
            with socket.create_connection(
                ("127.0.0.1", int(config["port"])),
                self.timeout_s,
            ) as sock:
                connected = True
                sock.settimeout(self.timeout_s + 0.5)
                with sock.makefile("rwb", buffering=0) as stream:
                    write_packet(stream, request)
                    dispatched = True
                    result = read_packet(stream)
        except OSError as exc:
            if not connected:
                raise RuntimeError("browser_bridge_unavailable") from exc
            if dispatched:
                raise RuntimeError(
                    "browser_outcome_unknown_do_not_retry"
                ) from exc
            raise RuntimeError("browser_bridge_transport_failed") from exc
        except (TimeoutError, EOFError) as exc:
            raise RuntimeError("browser_outcome_unknown_do_not_retry") from exc
        if result.get("id") != request_id:
            raise RuntimeError("bridge_response_id_mismatch")
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "browser_bridge_failed"))
        return result.get("result")


class BrowserCore:
    def __init__(self, transport):
        self.transport = transport

    def call(self, operation, **arguments):
        if operation not in OPERATIONS:
            raise ValueError("unknown_browser_primitive")
        if operation not in {"list_tabs", "get_active_tab", "navigate", "download"} and not (
                operation == "verify" and isinstance(arguments.get("download_id"),int)):
            if not isinstance(arguments.get("tab_id"), int) or isinstance(arguments.get("tab_id"), bool):
                raise ValueError("explicit_tab_id_required")
        if operation in {"navigate", "download"}:
            arguments["url"] = http_url(arguments.get("url", ""))
        if operation in {"click", "write", "select", "press"} and not arguments.get("ref"):
            raise ValueError("observed_ref_required")
        if operation == "verify":
            targeted_value = (
                isinstance(arguments.get("tab_id"), int)
                and bool(str(arguments.get("ref") or "").strip())
                and "expected_value" in arguments
            )
            generic_condition = any(
                k in arguments for k in ("text", "url", "title", "download_id")
            )
            if not (targeted_value or generic_condition):
                raise ValueError("explicit_postcondition_required")
        return self.transport.request(operation, arguments)

    def list_tabs(self): return self.call("list_tabs")
    def get_active_tab(self): return self.call("get_active_tab")
    def activate_tab(self, tab_id): return self.call("activate_tab", tab_id=tab_id)
    def navigate(self, url, *, tab_id=None):
        return self.call("navigate", url=url, **({"tab_id": tab_id} if tab_id is not None else {}))
    def observe_dom(self, tab_id): return self.call("observe_dom", tab_id=tab_id)
    def find(self, tab_id, text, *, type="", exact=True):
        return self.call("find", tab_id=tab_id, text=text, type=type, exact=exact)
    def click(self, tab_id, ref): return self.call("click", tab_id=tab_id, ref=ref)
    def write(self, tab_id, ref, text, *, mode="replace"):
        return self.call("write", tab_id=tab_id, ref=ref, text=text, mode=mode)
    def select(self, tab_id, ref, text):
        return self.call("select", tab_id=tab_id, ref=ref, text=text)
    def press(self, tab_id, ref, key): return self.call("press", tab_id=tab_id, ref=ref, key=key)
    def back(self, tab_id): return self.call("back", tab_id=tab_id)
    def forward(self, tab_id): return self.call("forward", tab_id=tab_id)
    def close_tab(self, tab_id): return self.call("close_tab", tab_id=tab_id)
    def download(self, url): return self.call("download", url=url)
    def verify(self, tab_id=None, **condition):
        return self.call("verify", **({"tab_id": tab_id} if tab_id is not None else {}), **condition)
