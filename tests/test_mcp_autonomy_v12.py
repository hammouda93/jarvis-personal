"""Offline end-to-end regressions for MCP pauses, batches and diagnostics."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import GroqResponsesAgent
from jarvis_agent.config import settings
from jarvis_agent.mcp_activity import MCPActivityStore, MCPBudgetExceeded
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.native_tools import AgentActionResult


class NativeStub:
    def __init__(self):
        self.calls = []

    def ollama_tools(self):
        return []

    def requires_confirmation(self, name):
        return False

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, arguments))
        return AgentActionResult(name, True, "Native action", "{}")


def response(*calls, text=""):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=text, tool_calls=[SimpleNamespace(
            id=f"fixture-{name}-{index}", function=SimpleNamespace(
                name=name, arguments=json.dumps(arguments)))
            for index, (name, arguments) in enumerate(calls)]))])


class ScriptedAgent(GroqResponsesAgent):
    def __init__(self, tools, responses):
        super().__init__(tools)
        self.api_key = "fixture-only"
        self.chat = Mock(side_effect=responses)

    def _chat(self, **kwargs):
        return self.chat(**kwargs)


class MCPAutonomyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.registry = MCPRegistry(Path(temp.name) / "servers.json")
        self.registry.add_http("orbitdata", "https://orbitdata.example/mcp")
        self.registry.set_enabled("orbitdata", True)
        self.registry.discover("orbitdata", [
            {"name": name, "input_schema": {"type": "object", "properties": {}}}
            for name in ("get_me", "list_records")
        ])
        self.registry.note_successful_discovery("orbitdata")
        self.registry.set_runtime_enabled(True)
        self.registry.set_auto_readonly("orbitdata", True)
        self.native = NativeStub()
        self.transport = Mock()
        self.adapter = MCPToolRegistry(self.native, registry=self.registry,
            transport=self.transport, runtime_gate=self.registry.runtime_enabled)

    def run_agent(self, responses):
        self.logs = []
        self.agent = ScriptedAgent(self.adapter, responses)
        with patch("jarvis_agent.agent_runtime.settings",
                   replace(settings, agent_max_tool_rounds=6)):
            return self.agent.run("Consulte le service orbitdata et résume les résultats.",
                                  log=self.logs.append)

    def test_old_unknown_stops_without_extra_model_round_and_closes_batch(self):
        old = self.registry.activity().reserve("orbitdata", "call",
            self.registry.get_server("orbitdata"), tool="list_records")
        self.registry.activity().finish(old, "unknown")
        result = self.run_agent([
            response(("mcp__orbitdata__list_records", {}),
                     ("open_application", {"name": "ShouldNeverOpen"})),
            response(text="Le modèle ne doit pas être rappelé."),
        ])
        self.assertEqual(result.pause_reason, "mcp_unknown_requires_review")
        self.assertEqual(self.agent.chat.call_count, 1)
        self.transport.call_tool.assert_not_called()
        self.assertEqual(self.native.calls, [])
        self.assertIn(f"Tentative précédente #{old}", result.text)
        calls = {c["id"] for m in self.agent._messages
                 for c in m.get("tool_calls", [])}
        replies = {m.get("tool_call_id") for m in self.agent._messages
                   if m.get("role") == "tool"}
        self.assertTrue(calls.issubset(replies))

    def test_new_unknown_preserves_successful_reads_and_never_runs_next_batch_action(self):
        self.transport.call_tool.side_effect = [
            {"success": True, "message": "PRIVATE-IDENTITY"},
            RuntimeError("Disconnected after dispatch"),
        ]
        result = self.run_agent([
            response(("mcp__orbitdata__get_me", {})),
            response(("mcp__orbitdata__list_records", {}),
                     ("open_application", {"name": "ShouldNeverOpen"})),
            response(text="Ne pas rappeler le modèle."),
        ])
        self.assertEqual(result.pause_reason, "mcp_outcome_unknown")
        self.assertEqual(self.agent.chat.call_count, 2)
        self.assertEqual(self.native.calls, [])
        self.assertIn("mcp__orbitdata__get_me", result.text)
        self.assertNotIn("PRIVATE-IDENTITY", result.text)
        self.assertIn("MCP Control Center", result.text)

    def test_mcp_arguments_and_output_are_not_copied_into_runtime_logs(self):
        self.transport.call_tool.return_value = {
            "success": True, "message": "PRIVATE-MCP-OUTPUT"}
        self.run_agent([
            response(("mcp__orbitdata__get_me", {"filter": "PRIVATE-ARGUMENT"})),
            response(text="Lecture terminée."),
        ])
        logs = "\n".join(self.logs)
        self.assertNotIn("PRIVATE-ARGUMENT", logs)
        self.assertNotIn("PRIVATE-MCP-OUTPUT", logs)
        self.assertIn("mcp__orbitdata__get_me", logs)

    def test_nested_transport_error_keeps_lock_and_exposes_only_safe_diagnostic(self):
        import httpx2
        from jarvis_agent.mcp_result import receipt_metadata
        error = httpx2.HTTPStatusError("PRIVATE_BODY", request=httpx2.Request("POST",
            "https://fixture.example/PRIVATE_URL"), response=httpx2.Response(503))
        self.transport.call_tool.side_effect = ExceptionGroup("PRIVATE_GROUP", [error])
        result = self.run_agent([response(("mcp__orbitdata__get_me", {}))])
        self.assertEqual(result.pause_reason, "mcp_outcome_unknown")
        diagnostic = json.loads(result.actions[0].detail)["diagnostic"]
        self.assertEqual(diagnostic, {"error_type": "HTTPStatusError", "http_status": 503})
        logs = " ".join(self.logs)
        self.assertIn('"http_status": 503', logs)
        self.assertNotIn("PRIVATE", logs + result.text + result.actions[0].detail)
        self.assertEqual(self.registry.activity().summary("orbitdata")["last_outcome"], "unknown")
        self.assertEqual(receipt_metadata({"diagnostic": {"error_type": [],
            "rpc_method": {"private": "PRIVATE"}, "rpc_code": True, "http_status": True},
            "mcp_output": "PRIVATE"}), {"content_omitted": True})

    def test_review_receipt_is_exact_beyond_twenty_unresolved_tools(self):
        now = [1000.0]
        activity = MCPActivityStore(self.registry.path.parent / "many.sqlite3",
                                    clock=lambda: now[0])
        entry = self.registry.get_server("orbitdata")
        for index in range(25):
            attempt = activity.reserve("orbitdata", "call", entry,
                                       tool=f"list_records_{index}")
            activity.finish(attempt, "unknown")
            now[0] += 16
            if index == 0:
                earliest = attempt
        with patch.object(self.registry, "activity", return_value=activity):
            self.registry.discover("orbitdata", [{"name": "list_records_0",
                "input_schema": {"type": "object", "properties": {}}}])
            self.registry.note_successful_discovery("orbitdata")
            self.registry.set_auto_readonly("orbitdata", True)
            result = self.adapter.execute("mcp__orbitdata__list_records_0", {})
        data = json.loads(result.detail)
        self.assertEqual(data["previous_attempt_id"], earliest)
        self.assertTrue(data["reviewable"])
        self.transport.call_tool.assert_not_called()

    def test_budget_refusal_has_specific_reason_and_wait_without_dispatch(self):
        first = self.registry.activity().reserve("orbitdata", "call",
            self.registry.get_server("orbitdata"), tool="list_records")
        self.registry.activity().finish(first, "failed")
        result = self.adapter.execute("mcp__orbitdata__get_me", {})
        data = json.loads(result.detail)
        self.assertEqual(data["guard"], "mcp_budget_unavailable")
        self.assertEqual(data["budget_reason"], "mcp_failure_cooldown")
        self.assertGreater(data["retry_after_seconds"], 0)
        self.assertLessEqual(data["retry_after_seconds"], 15)
        self.assertFalse(data["dispatched"])
        self.transport.call_tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()
