"""Configurable extra brains appended after the proven Cerebras -> Cerebras -> Groq chain.

This module does not alter the agent, its tools, permissions, or conversation history.
Only optional providers in an explicitly user-managed local configuration are loaded.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .secret_provider import SecretRef, WindowsCredentialSecretProvider

_PRESETS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai": "https://api.openai.com/v1",
    "grok": "https://api.x.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "custom": "",
}
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_ENV_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]{0,100}$")
_MAX_FILE_SIZE = 65536


class BrainConfigError(ValueError):
    """Invalid local brain configuration, never an API response."""


@dataclass(frozen=True)
class ExtraBrain:
    id: str
    provider: str
    model: str
    base_url: str
    enabled: bool = True
    api_key_env: str = ""
    max_estimated_tokens: int = 12000
    completion_tokens: int = 512
    timeout_s: float = 15.0
    completion_parameter: str = "max_completion_tokens"

    @classmethod
    def parse(cls, raw: object) -> "ExtraBrain":
        if not isinstance(raw, dict):
            raise BrainConfigError("brain_entry_must_be_object")
        key = str(raw.get("id") or "").strip().lower()
        provider = str(raw.get("provider") or "").strip().lower()
        model = str(raw.get("model") or "").strip()
        base_url = str(raw.get("base_url") or _PRESETS.get(provider, "")).strip().rstrip("/")
        api_key_env = str(raw.get("api_key_env") or "").strip()
        if not _IDENTIFIER.fullmatch(key):
            raise BrainConfigError("invalid_brain_id")
        if provider not in _PRESETS:
            raise BrainConfigError("unknown_provider_preset")
        if not model or len(model) > 200:
            raise BrainConfigError("invalid_model_id")
        parsed = urlparse(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise BrainConfigError("brain_endpoint_must_be_https")
        if api_key_env and not _ENV_IDENTIFIER.fullmatch(api_key_env):
            raise BrainConfigError("invalid_api_key_env")
        # Keys can only be provided through Windows Credential Manager or the
        # explicitly referenced environment variable, never through JSON.
        if any(k in raw for k in ("api_key", "token", "password", "secret")):
            raise BrainConfigError("plaintext_secrets_forbidden")
        try:
            budget = int(raw.get("max_estimated_tokens", 12000))
            completion = int(raw.get("completion_tokens", 512))
            timeout = float(raw.get("timeout_s", 15.0))
        except (TypeError, ValueError) as exc:
            raise BrainConfigError("invalid_brain_budget") from exc
        if not 1000 <= budget <= 200000 or not 128 <= completion <= 4096 or not 2 <= timeout <= 120:
            raise BrainConfigError("brain_budget_out_of_range")
        parameter = str(raw.get("completion_parameter") or "max_completion_tokens")
        if parameter not in {"max_completion_tokens", "max_tokens"}:
            raise BrainConfigError("invalid_completion_parameter")
        if type(raw.get("enabled", True)) is not bool:
            raise BrainConfigError("enabled_must_be_boolean")
        return cls(
            id=key, provider=provider, model=model, base_url=base_url,
            enabled=raw.get("enabled", True), api_key_env=api_key_env,
            max_estimated_tokens=budget, completion_tokens=completion,
            timeout_s=timeout, completion_parameter=parameter,
        )

    def public_dict(self) -> dict:
        """Configuration without any resolved secret."""
        return dict(
            id=self.id, provider=self.provider, model=self.model,
            base_url=self.base_url, enabled=self.enabled,
            api_key_env=self.api_key_env, max_estimated_tokens=self.max_estimated_tokens,
            completion_tokens=self.completion_tokens, timeout_s=self.timeout_s,
            completion_parameter=self.completion_parameter,
        )


def brain_config_path() -> Path:
    custom = (os.getenv("JARVIS_BRAIN_CONFIG_PATH") or "").strip()
    if custom:
        return Path(custom).expanduser()
    root = Path(os.getenv("LOCALAPPDATA") or (Path.home() / ".config"))
    return root / "JarvisPersonal" / "brains.json"


def load_extra_brains(path: Path | None = None) -> tuple[ExtraBrain, ...]:
    filename = path if path is not None else brain_config_path()
    if not filename.is_file():
        return ()
    if filename.stat().st_size > _MAX_FILE_SIZE:
        raise BrainConfigError("brain_config_too_large")
    try:
        payload = json.loads(filename.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError) as exc:
        raise BrainConfigError("brain_config_unreadable") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("brains"), list):
        raise BrainConfigError("brain_config_invalid_schema")
    if len(payload["brains"]) > 20:
        raise BrainConfigError("too_many_brains")
    brains = tuple(ExtraBrain.parse(item) for item in payload["brains"])
    if len({brain.id for brain in brains}) != len(brains):
        raise BrainConfigError("duplicate_brain_id")
    return brains


def save_extra_brains(brains: tuple[ExtraBrain, ...] | list[ExtraBrain], path: Path | None = None) -> None:
    """Atomic local update. Never saves API keys."""
    filename = path if path is not None else brain_config_path()
    if len(brains) > 20 or len({b.id for b in brains}) != len(brains):
        raise BrainConfigError("duplicate_or_too_many_brains")
    filename.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": 1, "brains": [b.public_dict() for b in brains]},
                         indent=2, ensure_ascii=False) + "\n"
    tmp = filename.with_suffix(filename.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as output:
            output.write(payload)
        os.replace(tmp, filename)
    finally:
        if tmp.exists():
            tmp.unlink()


def resolved_api_key(brain: ExtraBrain) -> str | None:
    """A secret is never returned to a logger or written to brains.json."""
    value = WindowsCredentialSecretProvider().get(SecretRef(brain.id, namespace="brain"))
    if not value and brain.api_key_env:
        value = (os.getenv(brain.api_key_env) or "").strip()
    return value or None


def store_api_key(brain_id: str, secret: str) -> None:
    """Use Windows Credential Manager, without recording the key in arguments/logs."""
    if not _IDENTIFIER.fullmatch(brain_id):
        raise BrainConfigError("invalid_brain_id")
    if not secret or len(secret) > 2500:
        raise BrainConfigError("invalid_secret_length")
    if os.name != "nt":
        raise BrainConfigError("windows_credential_manager_required")
    import win32cred
    target = WindowsCredentialSecretProvider().target_name(SecretRef(brain_id, namespace="brain"))
    win32cred.CredWrite({
        "Type": win32cred.CRED_TYPE_GENERIC,
        "TargetName": target,
        # pywin32 requires a Python Unicode string; it converts to UTF-16.
        # Pre-encoding this as bytes raises TypeError before CredWrite.
        "CredentialBlob": secret,
        "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        "UserName": "JarvisPersonal",
    }, 0)


def delete_api_key(brain_id: str) -> None:
    if not _IDENTIFIER.fullmatch(brain_id):
        raise BrainConfigError("invalid_brain_id")
    if os.name != "nt":
        raise BrainConfigError("windows_credential_manager_required")
    import win32cred
    target = WindowsCredentialSecretProvider().target_name(SecretRef(brain_id, namespace="brain"))
    try:
        win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception:
        pass
