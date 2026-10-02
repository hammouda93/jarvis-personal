from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SecretRef:
    name: str
    namespace: str = "jarvis"

    @property
    def safe_id(self) -> str:
        return f"{self.namespace}:{self.name}"


class SecretProvider(Protocol):
    provider_id: str

    def get(self, ref: SecretRef) -> str | None:
        ...


class EnvironmentSecretProvider:
    """Allowlisted environment-backed secret provider.

    The provider never enumerates the environment and only resolves names
    explicitly mapped by Jarvis configuration.
    """

    provider_id = "environment"

    def __init__(self, mapping: dict[str, str]):
        self._mapping = {
            str(logical): str(env_name)
            for logical, env_name in dict(mapping).items()
            if str(logical) and str(env_name)
        }

    def get(self, ref: SecretRef) -> str | None:
        env_name = self._mapping.get(ref.safe_id)
        if env_name is None:
            env_name = self._mapping.get(ref.name)
        if env_name is None:
            return None
        value = os.getenv(env_name)
        if value is None:
            return None
        value = value.strip()
        return value or None


class WindowsCredentialSecretProvider:
    """Read one exact Jarvis credential target without enumerating the vault."""

    provider_id = "windows_credential_manager"

    def __init__(self, *, target_prefix: str = "Jarvis"):
        self.target_prefix = str(target_prefix or "Jarvis").strip("/")

    def target_name(self, ref: SecretRef) -> str:
        return (
            f"{self.target_prefix}/"
            f"{ref.namespace.strip('/')}/"
            f"{ref.name}"
        )

    def get(self, ref: SecretRef) -> str | None:
        try:
            import win32cred
        except ImportError:
            return None

        try:
            credential = win32cred.CredRead(
                self.target_name(ref),
                win32cred.CRED_TYPE_GENERIC,
            )
        except Exception:
            return None

        blob = credential.get("CredentialBlob")
        if blob is None:
            return None
        if isinstance(blob, bytes):
            for encoding in ("utf-16-le", "utf-8"):
                try:
                    value = blob.decode(encoding).strip("\x00").strip()
                    if value:
                        return value
                except UnicodeDecodeError:
                    continue
            return None
        value = str(blob).strip()
        return value or None


class CompositeSecretProvider:
    """Try providers in order without exposing which value was returned."""

    provider_id = "composite"

    def __init__(self, providers: list[SecretProvider]):
        self._providers = list(providers)

    def get(self, ref: SecretRef) -> str | None:
        for provider in self._providers:
            try:
                value = provider.get(ref)
            except Exception:
                continue
            if value:
                return value
        return None


def default_secret_provider() -> CompositeSecretProvider:
    """Current compatibility mapping.

    Existing environment variables remain authoritative. Future keychain
    providers can be inserted before this provider without changing agents.
    """
    env = EnvironmentSecretProvider(
        {
            "cerebras_api_key": "CEREBRAS_API_KEY",
            "cerebras_secondary_api_key": "CEREBRAS_SECONDARY_API_KEY",
            "groq_api_key": "GROQ_API_KEY",
            "openai_api_key": "OPENAI_API_KEY",
            "elevenlabs_api_key": "ELEVENLABS_API_KEY",
            "ms_football_bridge_token": "JARVIS_MS_FOOTBALL_BRIDGE_TOKEN",
        }
    )
    return CompositeSecretProvider([env])
