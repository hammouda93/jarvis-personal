"""Chrome-launched Native Messaging host, authenticated local TCP ingress.

One Chrome profile owns one port. A second host fails instead of silently
switching profiles. Pending actions are never replayed after disconnect.
"""
from __future__ import annotations

import argparse
import hmac
import json
import queue
import socket
import sys
import threading
import time
from pathlib import Path

from .browser_core import OPERATIONS, read_packet, write_packet


class NativeHost:
    def __init__(self, config, input_stream, output_stream):
        self.config, self.input, self.output = config, input_stream, output_stream
        self.pending = {}
        self.lock = threading.Lock()
        self.writer_lock = threading.Lock()
        self.alive = threading.Event()
        self.alive.set()
        self.ready = threading.Event()

    def read_extension(self):
        try:
            while self.alive.is_set():
                response = read_packet(self.input)
                if response.get("hello") == 1:
                    self.ready.set()
                    continue
                with self.lock:
                    waiter = self.pending.get(str(response.get("id", "")))
                if waiter:
                    waiter.put(response)
        except (EOFError, ValueError, OSError):
            self.alive.clear()
            with self.lock:
                for request_id, waiter in self.pending.items():
                    waiter.put({"id": request_id, "ok": False,
                                "error": "browser_disconnected_outcome_unknown"})

    def dispatch(self, request):
        request_id = str(request.get("id", ""))
        failure = lambda error: {"id": request_id, "ok": False, "error": error}
        if not hmac.compare_digest(str(request.get("token", "")), self.config["token"]):
            return failure("bridge_authentication_failed")
        if request.get("version") != 1 or request.get("operation") not in OPERATIONS:
            return failure("unsupported_bridge_operation")
        if not self.ready.is_set() or not self.alive.is_set():
            return failure("browser_bridge_not_connected")
        try:
            remaining = min(10.0, (int(request["deadline_ms"]) / 1000) - time.time())
        except (KeyError, TypeError, ValueError):
            return failure("invalid_deadline")
        if remaining <= 0 or not request_id or len(request_id) > 80:
            return failure("expired_or_invalid_request")
        waiter = queue.Queue()
        with self.lock:
            if request_id in self.pending or len(self.pending) >= 16:
                return failure("duplicate_or_busy_request")
            self.pending[request_id] = waiter
        try:
            envelope = {key: request[key] for key in ("id", "version", "operation", "arguments", "deadline_ms")}
            with self.writer_lock:
                write_packet(self.output, envelope)
            return waiter.get(timeout=remaining)
        except queue.Empty:
            return failure("browser_outcome_unknown_do_not_retry")
        except (OSError, ValueError, KeyError):
            return failure("bridge_protocol_failed")
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def handle_client(self, connection):
        with connection:
            connection.settimeout(12)
            try:
                with connection.makefile("rwb", buffering=0) as stream:
                    request = read_packet(stream)
                    write_packet(stream, self.dispatch(request))
            except (EOFError, ValueError, OSError):
                pass

    def serve(self):
        threading.Thread(target=self.read_extension, daemon=True).start()
        with socket.socket() as listener:
            if sys.platform == "win32":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", int(self.config["port"])))
            listener.listen(8)
            listener.settimeout(0.5)
            while self.alive.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                # Serialize clients to bound work and preserve action order.
                self.handle_client(connection)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    options, origins = parser.parse_known_args()
    config = json.loads(Path(options.config).read_text(encoding="utf-8-sig"))
    expected = "chrome-extension://" + config["extension_id"] + "/"
    if not origins or origins[0] != expected:
        raise SystemExit("untrusted_native_messaging_origin")
    if sys.platform == "win32":
        import msvcrt
        import os
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    # stdout is exclusively length-framed protocol, diagnostics go to stderr.
    try:
        NativeHost(config, sys.stdin.buffer, sys.stdout.buffer).serve()
    except OSError as exc:
        print(f"Native browser bridge: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
