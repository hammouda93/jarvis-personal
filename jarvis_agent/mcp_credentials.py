"""MCP credentials protected by user-scoped DPAPI or Linux Secret Service.

No plaintext fallback and no application encryption key on disk. This protects
at-rest data, not against arbitrary code already running as the same OS user.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class CredentialUnavailable(RuntimeError):
    pass


class _DPAPIStore:
    def __init__(self, root: Path):
        self.root = root

    def get(self, key: str) -> str | None:
        path = self.root / (key + ".dpapi")
        if not path.exists():
            return None
        import win32crypt
        payload = path.read_bytes()
        if len(payload) > 100000:
            raise CredentialUnavailable("credential_blob_too_large")
        return win32crypt.CryptUnprotectData(payload, key.encode("ascii"), None, None, 1)[1].decode("utf-8")

    def put(self, key: str, value: str) -> None:
        import win32crypt
        payload = win32crypt.CryptProtectData(value.encode("utf-8"), "Jarvis MCP", key.encode("ascii"), None, None, 1)
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".credential_", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, self.root / (key + ".dpapi"))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        (self.root / (key + ".dpapi")).unlink(missing_ok=True)


class _SecretServiceStore:
    def __init__(self):
        # Instantiate only the official OS backend, not a configured plaintext
        # third-party keyring or a plugin supplied by an external MCP server.
        from keyring.backends.SecretService import Keyring
        self.backend = Keyring()
        if self.backend.priority <= 0:
            raise CredentialUnavailable("secure_os_keyring_unavailable")

    def get(self, key: str) -> str | None:
        return self.backend.get_password("JarvisPersonal.MCP", key)

    def put(self, key: str, value: str) -> None:
        self.backend.set_password("JarvisPersonal.MCP", key, value)

    def delete(self, key: str) -> None:
        if self.get(key) is not None:
            self.backend.delete_password("JarvisPersonal.MCP", key)


class CredentialVault:
    PURPOSES = ("bearer", "api_key", "oauth_tokens", "oauth_client", "oauth_metadata")

    def __init__(self, root: str | Path | None = None, *, backend=None):
        if root is None:
            from .mcp_server_registry import config_path
            root = config_path().parent / "mcp_credentials"
        self.root = Path(root)
        self._backend = backend

    def _store(self):
        if self._backend is None:
            self._backend = _DPAPIStore(self.root) if os.name == "nt" else _SecretServiceStore()
        return self._backend

    def _key(self, server: str, purpose: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,35}", server) or purpose not in self.PURPOSES:
            raise ValueError("invalid_credential_scope")
        namespace = str(self.root.resolve()) + "\n" + server + "\n" + purpose
        return hashlib.sha256(namespace.encode("utf-8")).hexdigest()

    @staticmethod
    def _binding(target: str) -> str:
        return hashlib.sha256(str(target).encode("utf-8")).hexdigest()

    def read(self, server: str, target: str, purpose: str = "bearer") -> str | None:
        key = self._key(server, purpose)
        try:
            raw = self._store().get(key)
            if raw is None:
                return None
            if len(raw) > 70000:
                raise CredentialUnavailable("credential_blob_too_large")
            data = json.loads(raw)
            if data["binding"] != self._binding(target):
                raise CredentialUnavailable("credential_target_changed")
            if not isinstance(data["value"], str):
                raise CredentialUnavailable("credential_value_invalid")
            return data["value"]
        except CredentialUnavailable:
            raise
        except Exception as exc:
            raise CredentialUnavailable("secure_credential_read_failed") from exc

    def write(self, server: str, target: str, value: str, purpose: str = "bearer") -> None:
        key = self._key(server, purpose)
        if not isinstance(value, str) or not 1 <= len(value) <= 64000:
            raise ValueError("invalid_credential_value")
        raw = json.dumps({"binding": self._binding(target), "value": value}, ensure_ascii=False)
        if len(raw.encode("utf-8")) > 70000:
            raise ValueError("credential_blob_too_large")
        try:
            self._store().put(key, raw)
        except Exception as exc:
            raise CredentialUnavailable("secure_credential_write_failed") from exc

    def forget(self, server: str) -> None:
        keys = [self._key(server, purpose) for purpose in self.PURPOSES]
        try:
            for key in keys:
                self._store().delete(key)
        except Exception as exc:
            raise CredentialUnavailable("secure_credential_delete_failed") from exc
