from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jarvis_agent.mcp_credentials import CredentialUnavailable, CredentialVault, _DPAPIStore
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_sdk_transport import MCPUnavailable, OfficialMCPTransport


class FakeStore:
    def __init__(self):
        self.items = {}
    def get(self, key):
        return self.items.get(key)
    def put(self, key, value):
        self.items[key] = value
    def delete(self, key):
        self.items.pop(key, None)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = FakeStore()
        self.vault = CredentialVault(self.root / "secrets", backend=self.store)
        self.registry = MCPRegistry(self.root / "mcp.json")
        self.registry.add_http("service", "https://service.example/mcp")

    def test_secrets_are_scoped_to_server_endpoint_and_purpose(self):
        self.vault.write("service", "https://service.example/mcp", "fixture")
        self.assertEqual(self.vault.read("service", "https://service.example/mcp"), "fixture")
        self.assertIsNone(self.vault.read("other", "https://service.example/mcp"))
        self.assertIsNone(self.vault.read("service", "https://service.example/mcp", "oauth_tokens"))
        with self.assertRaisesRegex(CredentialUnavailable, "target_changed"):
            self.vault.read("service", "https://different.example/mcp")

    def test_namespace_and_keys_never_contain_secret_values(self):
        self.vault.write("service", "target", "PRIVATE")
        self.assertNotIn("PRIVATE", repr(list(self.store.items)))
        self.assertIsNone(CredentialVault(self.root / "other", backend=self.store).read("service", "target"))

    def test_invalid_scopes_and_huge_values_are_refused(self):
        for server, purpose in (("../unsafe", "bearer"), ("service", "other")):
            with self.assertRaises(ValueError):
                self.vault.write(server, "target", "secret", purpose)
        for value in ("", "x" * 64001, 123):
            with self.assertRaises(ValueError):
                self.vault.write("service", "target", value)

    def test_backend_failure_never_creates_plaintext_fallback(self):
        with patch.object(self.store, "put", side_effect=RuntimeError("PRIVATE_VALUE")):
            with self.assertRaisesRegex(CredentialUnavailable, "secure_credential_write_failed"):
                self.vault.write("service", "target", "PRIVATE_VALUE")
        self.assertFalse((self.root / "secrets").exists())

    def test_corrupted_storage_returns_sanitized_failure(self):
        self.store.items[self.vault._key("service", "bearer")] = "not json PRIVATE"
        with self.assertRaisesRegex(CredentialUnavailable, "read_failed") as error:
            self.vault.read("service", "target")
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_worker_stores_only_source_metadata_not_secret_in_config_or_result(self):
        result = perform_mcp_command(self.registry, MCPCommand("store_bearer", "service", "PRIVATE"), vault=self.vault)
        self.assertTrue(result["success"])
        self.assertEqual(self.registry.get_server("service")["credential_source"], "vault")
        self.assertNotIn("PRIVATE", self.registry.path.read_text())
        self.assertNotIn("PRIVATE", repr(result))

    def test_forgotten_credentials_disable_server_and_do_not_restore_env_auth(self):
        perform_mcp_command(self.registry, MCPCommand("store_bearer", "service", "PRIVATE"), vault=self.vault)
        self.registry.set_enabled("service", True)
        perform_mcp_command(self.registry, MCPCommand("forget_credentials", "service"), vault=self.vault)
        entry = self.registry.get_server("service")
        self.assertFalse(entry["enabled"])
        self.assertEqual(entry["credential_source"], "none")
        self.assertEqual(self.store.items, {})

    def test_deleting_a_server_also_clears_its_vault_records(self):
        perform_mcp_command(self.registry, MCPCommand("store_bearer", "service", "PRIVATE"), vault=self.vault)
        perform_mcp_command(self.registry, MCPCommand("remove_server", "service"), vault=self.vault)
        self.assertIsNone(self.registry.get_server("service"))
        self.assertEqual(self.store.items, {})

    def test_revoke_failure_still_closes_model_permissions(self):
        self.registry.set_credential_source("service", "vault")
        self.registry.set_enabled("service", True)
        with patch.object(self.vault, "forget", side_effect=CredentialUnavailable("locked")):
            with self.assertRaises(CredentialUnavailable):
                perform_mcp_command(self.registry, MCPCommand("forget_credentials", "service"), vault=self.vault)
        self.assertFalse(self.registry.get_server("service")["enabled"])
        self.assertEqual(self.registry.exposed_tools(), [])

    def test_inbox_refuses_header_injection_and_never_prints_token(self):
        inbox = MCPControlInbox()
        self.assertFalse(inbox.submit("store_bearer", "service", "a\r\nX: b"))
        self.assertTrue(inbox.submit("store_bearer", "service", "PRIVATE"))
        self.assertEqual(inbox.pop_nowait().operation, "store_bearer")

    def test_missing_vault_credential_is_not_an_uncertain_remote_call(self):
        self.registry.set_credential_source("service", "vault")
        with patch("jarvis_agent.mcp_credentials.CredentialVault.read", return_value=None):
            with self.assertRaises(MCPUnavailable) as error:
                asyncio.run(OfficialMCPTransport._with_session({"id": "service", **self.registry.get_server("service")}, "call", tool="send"))
        self.assertFalse(error.exception.outcome_unknown)

    def test_transport_timeout_remains_an_uncertain_call_without_retry(self):
        with patch.object(OfficialMCPTransport, "_with_session", side_effect=TimeoutError) as session:
            with self.assertRaises(MCPUnavailable) as error:
                OfficialMCPTransport().invoke({}, "call")
        self.assertTrue(error.exception.outcome_unknown)
        self.assertEqual(session.call_count, 1)


@unittest.skipUnless(os.name == "nt", "DPAPI is a Windows-only cryptographic contract, not desktop acceptance")
class WindowsDPAPITests(unittest.TestCase):
    def test_real_dpapi_roundtrip_in_temporary_store_without_personal_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vault = CredentialVault(root, backend=_DPAPIStore(root))
            vault.write("fixture", "https://fixture.example/mcp", "TEST_FIXTURE_NOT_A_REAL_TOKEN")
            self.assertEqual(vault.read("fixture", "https://fixture.example/mcp"), "TEST_FIXTURE_NOT_A_REAL_TOKEN")
            encrypted = next(root.glob("*.dpapi")).read_bytes()
            self.assertNotIn(b"TEST_FIXTURE_NOT_A_REAL_TOKEN", encrypted)
            self.assertNotIn(b"https://fixture.example/mcp", encrypted)
            vault.forget("fixture")
            self.assertEqual(list(root.glob("*.dpapi")), [])
