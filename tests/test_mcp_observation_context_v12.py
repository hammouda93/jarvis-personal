"""Large external observations remain explicitly partial and locally readable."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from jarvis_agent.mcp_sdk_transport import tool_reply_envelope
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.request_context import RequestContext, ContextToolRegistry


class NativeStub:
    def ollama_tools(self):
        return []

    def requires_confirmation(self, name):
        return False


class MCPObservationContextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        registry = MCPRegistry(Path(temp.name) / "servers.json")
        registry.add_http("orbitdata", "https://orbitdata.example/mcp")
        registry.set_enabled("orbitdata", True)
        registry.discover("orbitdata", [{"name": "list_records",
            "input_schema": {"type": "object", "properties": {}}}])
        registry.note_successful_discovery("orbitdata")
        registry.set_runtime_enabled(True)
        registry.set_auto_readonly("orbitdata", True)
        self.context = RequestContext(enabled=True)
        self.context.begin_turn("Lis les records dans orbitdata")
        self.transport = Mock()
        self.adapter = MCPToolRegistry(NativeStub(), registry=registry,
            transport=self.transport, runtime_gate=registry.runtime_enabled,
            observation_context=self.context)
        self.tools = ContextToolRegistry(self.adapter, self.context)

    def test_large_read_has_partial_preview_and_recoverable_full_evidence(self):
        body = "أهلا-é-" * 1000 + "FINAL-RECORD-OBSERVED"
        self.transport.call_tool.return_value = {"success": True, "message": body,
            "data": {"records": [{"id": index} for index in range(200)]}}
        result = self.tools.execute("mcp__orbitdata__list_records", {})
        detail = json.loads(result.detail)
        self.assertTrue(detail["output_truncated"])
        self.assertNotIn("FINAL-RECORD-OBSERVED", detail["mcp_output"])
        identity = detail["observation_evidence"]["evidence_id"]
        definitions = self.context.prepare_tools(self.adapter.ollama_tools())
        self.assertIn("read_observation_evidence", {
            x["function"]["name"] for x in definitions})
        pieces, offset = [], 0
        while True:
            read = self.tools.execute("read_observation_evidence", {
                "evidence_id": identity, "offset": offset, "limit": 1700})
            data = json.loads(read.detail)
            self.assertFalse(data["verified"])
            self.assertEqual(data["trust"], "external_unverified")
            pieces.append(data["content"])
            offset = data["next_offset"]
            if offset is None:
                break
        archived = json.loads("".join(pieces))
        self.assertEqual(archived["mcp_output"], body)
        self.assertEqual(len(archived["mcp_structured"]["records"]), 200)
        self.assertEqual(self.transport.call_tool.call_count, 1)

    def test_archive_is_bounded_session_only_and_reset_forgets_it(self):
        self.transport.call_tool.return_value = {"success": True, "message": "x" * 9000}
        identities = []
        for _ in range(20):
            detail = json.loads(self.tools.execute("mcp__orbitdata__list_records", {}).detail)
            identities.append(detail["observation_evidence"]["evidence_id"])
        self.assertLessEqual(len(self.context.external_evidence), 16)
        self.assertLessEqual(sum(len(x["content"]) for x in
                                 self.context.external_evidence.values()), 262144)
        self.assertNotIn(identities[0], self.context.external_evidence)
        self.context.reset()
        self.assertEqual(self.context.external_evidence, {})
        with self.assertRaisesRegex(ValueError, "evidence_unavailable"):
            self.context.read_evidence({"evidence_id": identities[-1]})

    def test_disabled_context_never_advertises_an_unreadable_archive(self):
        self.context.enabled = False
        self.transport.call_tool.return_value = {"success": True, "message": "x" * 5000}
        detail = json.loads(self.tools.execute("mcp__orbitdata__list_records", {}).detail)
        self.assertTrue(detail["output_truncated"])
        self.assertNotIn("observation_evidence", detail)
        self.assertEqual(self.context.external_evidence, {})

    def test_transport_preserves_long_text_and_announces_hard_limits(self):
        body = "source-text-" * 500
        reply = SimpleNamespace(content=[SimpleNamespace(text=body)],
            structured_content={"cursor": "next"}, is_error=False)
        envelope = tool_reply_envelope(reply)
        self.assertEqual(envelope["message"], body)
        self.assertFalse(envelope["output_truncated"])
        reply.content = [SimpleNamespace(text="x" * 100000)]
        envelope = tool_reply_envelope(reply)
        self.assertTrue(envelope["output_truncated"])
        self.assertLessEqual(len(envelope["message"]), 65536)

    def test_empty_structured_data_is_preserved(self):
        envelope = tool_reply_envelope(SimpleNamespace(
            content=[], structured_content={}, is_error=False))
        self.assertEqual(envelope["data"], {})

    def test_failed_optional_archive_never_relabels_a_completed_read_as_unknown(self):
        self.transport.call_tool.return_value = {"success": True, "message": "a" * 5000}
        self.context.archive_external_observation = Mock(side_effect=RuntimeError("unavailable"))
        result = self.tools.execute("mcp__orbitdata__list_records", {})
        detail = json.loads(result.detail)
        self.assertTrue(result.success)
        self.assertFalse(detail["outcome_unknown"])
        self.assertTrue(detail["output_truncated"])
        self.assertNotIn("observation_evidence", detail)
        self.assertEqual(self.transport.call_tool.call_count, 1)

    def test_model_receives_valid_json_and_the_archive_id_with_two_large_fields(self):
        from jarvis_agent.agent_runtime import GroqResponsesAgent
        self.transport.call_tool.return_value = {"success": True,
            "message": "a" * 4000, "data": {"value": "b" * 1400}}
        result = self.tools.execute("mcp__orbitdata__list_records", {})
        shown = json.loads(GroqResponsesAgent._compact_tool_content(result.name, result))
        detail = json.loads(shown["detail"])
        self.assertIn("evidence_id", detail["observation_evidence"])
        self.assertTrue(detail["output_truncated"])


if __name__ == "__main__":
    unittest.main()
