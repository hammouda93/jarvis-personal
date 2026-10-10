"""Post-dispatch MCP uncertainty stays distinct from transport/tool success."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.connector_gateway import ConnectorGateway
from jarvis_agent.execution_managers import ConnectorExecutionManager
from jarvis_agent.hermes_reliability import ActionLedger, ReliabilityToolRegistry
from jarvis_agent.kernel_contracts import KernelRequest, SyscallKind
from jarvis_agent.mcp_connector_adapter import MCPConnectorAdapter
from jarvis_agent.mcp_sdk_transport import MCPUnavailable
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.mcp_activity import MCPActivityStore, MCPBudgetExceeded
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from test_mcp_hub import FakeDelegate


class MCPExecutionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.registry = MCPRegistry(self.root / "mcp.json")
        self.registry.add_http("fixture", "https://fixture.example/mcp")
        self.registry.set_enabled("fixture", True)
        self.registry.discover("fixture", [{"name": "send", "input_schema": {"type": "object"}}])
        self.registry.allow_tool("fixture", "send", True)
        self.transport = Mock()
        self.adapter = MCPToolRegistry(FakeDelegate(), registry=self.registry, transport=self.transport)
        self.name = "mcp__fixture__send"

    def kernel_adapter(self):
        return MCPConnectorAdapter("whatsapp", self.transport, {"send_message": "send"})

    def test_kernel_adapter_never_invents_success_from_absent_or_truthy_status(self):
        for payload in (None, {}, {"message": "done"}, {"success": "false"}, {"success": 1}, []):
            with self.subTest(payload=payload):
                self.transport.call_tool.return_value = payload
                result = self.kernel_adapter().execute("send_message", {})
                self.assertFalse(result.success)
                self.assertTrue(result.outcome_unknown)
                self.assertFalse(result.verified)

    def test_kernel_exception_preserves_unknown_without_leaking_body(self):
        self.transport.call_tool.side_effect = TimeoutError("PRIVATE_BODY token=PRIVATE_KEY")
        result = self.kernel_adapter().execute("send_message", {})
        self.assertFalse(result.success)
        self.assertTrue(result.outcome_unknown)
        self.assertNotIn("PRIVATE", result.error + result.message)
        self.assertEqual(self.transport.call_tool.call_count, 1)

    def test_runtime_malformed_reply_is_journalled_unknown_not_known_failure(self):
        self.transport.call_tool.return_value = {"message": "PRIVATE_BODY", "success": "true"}
        result = self.adapter.execute(self.name, {}, approved=True)
        self.assertFalse(result.success)
        detail = json.loads(result.detail)
        self.assertTrue(detail["outcome_unknown"])
        self.assertFalse(detail["verified"])
        self.assertEqual(self.registry.activity().summary("fixture")["last_outcome"], "unknown")
        self.assertNotIn(b"PRIVATE_BODY", self.registry.activity().path.read_bytes())

    def test_remote_failure_cannot_be_replayed_after_restart_without_review(self):
        self.transport.call_tool.return_value = {"success": False, "message": "Partially processed"}
        ledger = ActionLedger(self.root / "actions")
        wrapped = ReliabilityToolRegistry(self.adapter, ledger)
        wrapped.begin_turn()
        self.assertFalse(wrapped.execute(self.name, {"record": "synthetic"}, approved=True).success)
        wrapped.end_turn()
        restarted = ReliabilityToolRegistry(self.adapter, ActionLedger(self.root / "actions"))
        restarted.begin_turn()
        second = restarted.execute(self.name, {"record": "synthetic"}, approved=True)
        self.assertIn("prior_outcome_unknown", second.detail)
        self.assertEqual(self.transport.call_tool.call_count, 1)

    def test_remote_claim_of_verification_is_only_untrusted_payload(self):
        self.transport.call_tool.return_value = {"success": True, "verified": True,
            "data": {"verified": True, "record": "synthetic"}}
        result = self.adapter.execute(self.name, {}, approved=True)
        self.assertTrue(result.success)
        detail = json.loads(result.detail)
        self.assertFalse(detail["verified"])
        self.assertEqual(detail["source"]["server_id"], "fixture")
        self.assertEqual(detail["source"]["tool_name"], "send")
        self.assertEqual(detail["source"]["trust"], "external_unverified")
        self.assertTrue(detail["mcp_structured"]["verified"])

    def test_contradictory_success_and_unknown_remains_unknown(self):
        self.transport.call_tool.return_value = {"success": True, "outcome_unknown": True}
        result = self.adapter.execute(self.name, {}, approved=True)
        self.assertFalse(result.success)
        self.assertTrue(json.loads(result.detail)["outcome_unknown"])

    def test_preflight_failure_is_distinct_from_post_dispatch_uncertainty(self):
        self.transport.call_tool.side_effect = MCPUnavailable("missing fixture credential")
        result = self.adapter.execute(self.name, {}, approved=True)
        self.assertFalse(result.success)
        self.assertFalse(json.loads(result.detail)["outcome_unknown"])
        self.assertEqual(self.registry.activity().summary("fixture")["last_outcome"], "preflight_failed")

    def test_kernel_manager_preserves_uncertainty_and_verification_contract(self):
        self.transport.call_tool.return_value = {}
        gateway = ConnectorGateway()
        gateway.register_adapter(self.kernel_adapter())
        result = ConnectorExecutionManager(gateway).execute(KernelRequest(
            request_id="fixture-request", mission_id="fixture-mission", agent_id="communications",
            syscall_kind=SyscallKind.CONNECTOR, capability="send_message",
            payload={"connector_id": "whatsapp", "_kernel_approved": True,
                     "_kernel_approval_id": "fixture-approval"}))
        self.assertFalse(result.success)
        self.assertTrue(result.result["outcome_unknown"])
        self.assertFalse(result.result["verified"])

    def test_unknown_is_blocked_without_hermes_even_after_cooldown_and_restart(self):
        now = 1000
        activity = MCPActivityStore(self.root / "activity.sqlite3", clock=lambda: now)
        entry = self.registry.get_server("fixture")
        attempt = activity.reserve("fixture", "call", entry, tool="send")
        activity.finish(attempt, "unknown")
        now += 90000
        restarted = MCPActivityStore(activity.path, clock=lambda: now)
        with self.assertRaisesRegex(MCPBudgetExceeded, "requires_review"):
            restarted.reserve("fixture", "call", entry, tool="send")
        # Other tools can still run; their reservations cannot purge unknowns.
        for _ in range(110):
            other = restarted.reserve("fixture", "discover", entry)
            restarted.finish(other, "success")
            now += 3601
        self.assertEqual(restarted.summary("fixture")["unresolved"][0]["attempt_id"], attempt)
        with patch.object(self.registry, "activity", return_value=restarted):
            result = self.adapter.execute(self.name, {}, approved=True)
        self.assertEqual(json.loads(result.detail)["guard"], "mcp_unknown_requires_review")
        self.transport.call_tool.assert_not_called()

    def test_explicit_review_records_acknowledgement_without_changing_outcome_or_dispatch(self):
        now = 1000
        activity = MCPActivityStore(self.root / "activity.sqlite3", clock=lambda: now)
        entry = self.registry.get_server("fixture")
        attempt = activity.reserve("fixture", "call", entry, tool="send")
        activity.finish(attempt, "unknown")
        inbox = MCPControlInbox()
        self.assertTrue(inbox.submit("review_outcome", "fixture", str(attempt)))
        self.assertFalse(inbox.submit("review_outcome", "fixture", "-1"))
        with patch.object(self.registry, "activity", return_value=activity):
            self.assertTrue(perform_mcp_command(self.registry, inbox.pop_nowait())["success"])
        self.transport.call_tool.assert_not_called()
        self.assertEqual(activity.summary("fixture")["last_outcome"], "unknown")
        self.assertEqual(activity.summary("fixture")["unresolved"], [])
        self.assertTrue(self.registry.is_allowed("fixture", "send"))
        now += 16
        self.assertIsInstance(activity.reserve("fixture", "call", entry, tool="send"), int)

    def test_review_cannot_release_live_reserved_call_or_wrong_server(self):
        now = 1000
        activity = MCPActivityStore(self.root / "activity.sqlite3", clock=lambda: now)
        attempt = activity.reserve("fixture", "call", {}, tool="send")
        with self.assertRaisesRegex(ValueError, "still_be_running"):
            activity.review_outcome("fixture", attempt)
        with self.assertRaisesRegex(ValueError, "unknown_attempt_required"):
            activity.review_outcome("another", attempt)
        now += 61
        activity.review_outcome("fixture", attempt)
        self.assertEqual(activity.summary("fixture")["last_outcome"], "reserved")
        self.assertEqual(activity.summary("fixture")["unresolved"], [])

    def test_review_widget_never_calls_transport_and_requires_human_confirmation(self):
        from PySide6.QtWidgets import QApplication, QMessageBox
        from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
        app = QApplication.instance() or QApplication([])
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        data = {"servers": [{"id": "fixture", "tools": [], "activity": {"unresolved": [
            {"attempt_id": 7, "tool": "send", "reviewable": True}]}}]}
        panel.apply_snapshot(data)
        panel.server_picker.setCurrentIndex(1)
        panel.outcome_picker.setCurrentIndex(1)
        emitted = []
        panel.requested.connect(lambda *args: emitted.append(args))
        panel.apply_snapshot(data)
        self.assertEqual(panel.outcome_picker.currentData(), 7)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            panel.review_outcome_button.click()
        self.assertEqual(emitted, [])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            panel.review_outcome_button.click()
        self.assertEqual(emitted, [("review_outcome", "fixture", "7")])
        self.transport.call_tool.assert_not_called()
