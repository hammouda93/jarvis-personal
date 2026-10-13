"""The connected MCP capability must match the permissions the model sees.

No real account, internet traffic, GUI event or credential is used.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.mission_read_recovery import (
    needs_authorized_read_retry, is_neutral_identifier_clarification,
)
from jarvis_agent.request_context import RequestContext


class StubNative:
    def ollama_tools(self):
        return [{"type": "function", "function": {
            "name": "open_url", "description": "Open an observed URL",
            "parameters": {"type": "object", "properties": {}}}}]

    def requires_confirmation(self, name):
        return False


class ConnectedMCPIntentTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.registry = MCPRegistry(Path(root.name) / "mcp.json")
        self.registry.add_http("fixture", "https://fixture.example/mcp")
        self.registry.set_enabled("fixture", True)
        self.registry.discover("fixture", [
            {"name": "get_me", "description": "Read authenticated account",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "list_commits", "description": "Read repository commits",
             "input_schema": {"type": "object", "properties": {}}},
            {"name": "create_issue", "description": "Create external issue",
             "input_schema": {"type": "object", "properties": {}}},
        ])
        self.registry.note_successful_discovery("fixture")
        self.registry.set_runtime_enabled(True)
        self.registry.set_auto_readonly("fixture", True)
        self.adapter = MCPToolRegistry(
            StubNative(), registry=self.registry, transport=Mock(),
            runtime_gate=self.registry.runtime_enabled,
        )

    def test_descriptions_agree_with_actual_read_grant(self):
        defs = self.adapter.ollama_tools()
        aliases = {d["function"]["name"]: d["function"]["description"] for d in defs}
        self.assertIn("mcp__fixture__get_me", aliases)
        self.assertIn("mcp__fixture__list_commits", aliases)
        self.assertIn("préautorisée", aliases["mcp__fixture__get_me"])
        self.assertIn("préautorisée", aliases["mcp__fixture__list_commits"])
        self.assertNotIn("obligatoire pour chaque", aliases["mcp__fixture__get_me"])
        self.assertNotIn("mcp__fixture__create_issue", aliases)
        self.assertFalse(self.adapter.requires_confirmation("mcp__fixture__get_me"))
        self.assertTrue(self.adapter.requires_confirmation("mcp__fixture__create_issue"))

    def test_bounded_repair_only_after_unnecessary_read_permission_request(self):
        ctx = RequestContext(enabled=True)
        ctx.begin_turn("Inspecte les commits de mon dépôt connecté")
        exposed = ctx.prepare_tools(self.adapter.ollama_tools())
        self.assertTrue(needs_authorized_read_retry(
            "Inspecte les commits de mon dépôt connecté",
            "J'ai besoin de votre autorisation et du owner.",
            exposed, self.adapter.requires_confirmation,
        ))
        self.assertFalse(needs_authorized_read_retry(
            "Inspecte les commits de mon dépôt connecté",
            "Voici les commits observés et vérifiés.",
            exposed, self.adapter.requires_confirmation,
        ))
        self.assertFalse(needs_authorized_read_retry(
            "Supprime le dépôt",
            "J'ai besoin de votre autorisation.",
            exposed, self.adapter.requires_confirmation,
        ))
        self.assertFalse(needs_authorized_read_retry(
            "Inspecte les commits",
            "J'ai besoin de votre autorisation.",
            [{"type": "function", "function": {"name": "open_url"}}],
            self.adapter.requires_confirmation,
        ))

    def test_revoke_prevents_model_repair_and_updates_capability_text(self):
        self.registry.set_auto_readonly("fixture", False)
        defs = self.adapter.ollama_tools()
        self.assertEqual([d["function"]["name"] for d in defs], ["open_url"])
        self.assertFalse(needs_authorized_read_retry(
            "Consulte les commits", "J'ai besoin de votre autorisation.",
            defs, self.adapter.requires_confirmation,
        ))

    def test_identifier_fallback_is_data_only(self):
        self.assertTrue(is_neutral_identifier_clarification(
            "hammouda93 Jarvis-personal", status="waiting_external"))
        for value in ("oui", "continue", "delete repo", "approve payment",
                      "owner:secret", "https://example.org", "send-message now",
                      "please do this", "ghp_secrettoken123"):
            # A token-looking string must never enter a durable goal.
            with self.subTest(value=value):
                self.assertFalse(is_neutral_identifier_clarification(
                    value, status="waiting_external"))
        self.assertFalse(is_neutral_identifier_clarification(
            "hammouda93 jarvis-personal", status="blocked", manual_review=True))
        self.assertFalse(is_neutral_identifier_clarification(
            "hammouda93 jarvis-personal", status="waiting_external",
            pending_confirmation={"tool": "delete_repo"}))


if __name__ == "__main__":
    unittest.main()
