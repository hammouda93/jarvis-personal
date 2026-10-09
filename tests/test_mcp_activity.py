from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLineEdit
from jarvis_agent.mcp_activity import MCPActivityStore, MCPBudgetExceeded, quota_limits
from jarvis_agent.mcp_control import MCPCommand, MCPControlInbox, perform_mcp_command
from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
from jarvis_agent.mcp_server_registry import MCPRegistry
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.native_tools import AgentActionResult


class Delegate:
    def ollama_tools(self):
        return []
    def execute(self, name, arguments, approved=False):
        return AgentActionResult(name, True, "native unchanged")


class Transport:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail
    def call_tool(self, *args):
        self.calls += 1
        if self.fail:
            raise TimeoutError("PRIVATE_DATA")
        return {"success": True, "message": "fixture"}


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "activity.sqlite3"
        self.now = 36000.0
        self.store = MCPActivityStore(self.path, clock=lambda: self.now)
        self.entry = {"quotas": {"sessions_per_hour": 3, "calls_per_hour": 2}}

    def test_read_only_empty_snapshot_creates_no_files(self):
        self.assertEqual(self.store.summary("fixture")["calls_used"], 0)
        self.assertFalse(self.path.exists())

    def test_reservation_is_atomic_and_persists_after_restart(self):
        first = self.store.reserve("fixture", "call", self.entry, tool="read")
        self.store.finish(first, "success")
        restart = MCPActivityStore(self.path, clock=lambda: self.now)
        second = restart.reserve("fixture", "call", self.entry)
        with self.assertRaises(MCPBudgetExceeded):
            restart.reserve("fixture", "call", self.entry)
        restart.finish(second, "success")
        self.assertEqual(restart.summary("fixture")["calls_used"], 2)

    def test_fixed_utc_hour_boundary_and_server_isolation(self):
        for _ in range(3):
            attempt = self.store.reserve("fixture", "discover", self.entry)
            self.store.finish(attempt, "success")
        with self.assertRaises(MCPBudgetExceeded):
            self.store.reserve("fixture", "discover", self.entry)
        self.store.reserve("other", "discover", self.entry)
        self.now += 3600
        self.store.reserve("fixture", "discover", self.entry)
        self.assertEqual(self.store.summary("fixture")["sessions_used"], 1)

    def test_concurrent_stores_cannot_overspend(self):
        def reserve(_):
            try:
                MCPActivityStore(self.path, clock=lambda: self.now).reserve("fixture", "call", self.entry)
                return True
            except MCPBudgetExceeded:
                return False
        # Create the schema once, then exercise independent DB transactions.
        self.store.reserve("other", "discover", self.entry)
        with ThreadPoolExecutor(max_workers=8) as executor:
            outcomes = list(executor.map(reserve, range(8)))
        self.assertEqual(outcomes.count(True), 2)

    def test_failed_attempts_count_and_cooldown_prevents_reconnect_storm(self):
        attempt = self.store.reserve("fixture", "call", self.entry)
        self.store.finish(attempt, "unknown")
        with self.assertRaisesRegex(MCPBudgetExceeded, "cooldown"):
            self.store.reserve("fixture", "discover", self.entry)
        self.now += 16
        self.store.reserve("fixture", "call", self.entry)
        self.assertEqual(self.store.summary("fixture")["calls_used"], 2)

    def test_unknown_attempt_after_crash_is_not_success_or_free(self):
        self.store.reserve("fixture", "call", self.entry)
        restart = MCPActivityStore(self.path, clock=lambda: self.now)
        snapshot = restart.summary("fixture")
        self.assertEqual(snapshot["last_outcome"], "reserved")
        self.assertEqual(snapshot["sessions_used"], 1)

    def test_minimal_history_never_contains_arguments_or_exception_messages(self):
        attempt = self.store.reserve("fixture", "call", self.entry, tool="send")
        self.store.finish(attempt, "unknown")
        summary = self.store.summary("fixture")
        self.assertEqual(set(summary["recent"][0]), {"operation", "tool", "utc", "outcome"})
        with self.assertRaises(ValueError):
            self.store.finish(attempt, "PRIVATE_DATA")

    def test_finished_attempt_cannot_be_rewritten(self):
        attempt = self.store.reserve("fixture", "call", self.entry)
        self.store.finish(attempt, "unknown")
        with self.assertRaises(ValueError):
            self.store.finish(attempt, "success")
        self.assertEqual(self.store.summary("fixture")["last_outcome"], "unknown")

    def test_invalid_quota_types_and_caps_fail_closed(self):
        for value in (True, 0, -1, "1", 201):
            with self.assertRaises(ValueError):
                quota_limits({"quotas": {"sessions_per_hour": value}})
        with self.assertRaises(ValueError):
            self.store.reserve("../unsafe", "call", {})

    def configured(self):
        registry = MCPRegistry(self.root / "mcp.json")
        registry.add_http("fixture", "https://fixture.example/mcp")
        registry.set_enabled("fixture", True)
        registry.discover("fixture", [{"name": "send", "input_schema": {"type": "object"}}])
        registry.allow_tool("fixture", "send", True)
        return registry

    def test_model_quota_blocks_before_transport_without_affecting_native_tools(self):
        registry = self.configured()
        registry.set_quotas("fixture", {"sessions_per_hour": 5, "calls_per_hour": 1})
        transport = Transport()
        adapter = MCPToolRegistry(Delegate(), registry=registry, transport=transport)
        self.assertTrue(adapter.execute("mcp__fixture__send", {"body": "PRIVATE_DATA"}, approved=True).success)
        result = adapter.execute("mcp__fixture__send", {}, approved=True)
        self.assertFalse(result.success)
        self.assertEqual(json.loads(result.detail)["guard"], "mcp_budget_unavailable")
        self.assertEqual(transport.calls, 1)
        self.assertTrue(adapter.execute("open_application", {}).success)
        self.assertNotIn(b"PRIVATE_DATA", registry.activity().path.read_bytes())

    def test_unavailable_journal_blocks_remote_call_not_existing_native_path(self):
        registry = self.configured()
        registry.activity().path.write_bytes(b"corrupted")
        transport = Transport()
        adapter = MCPToolRegistry(Delegate(), registry=registry, transport=transport)
        self.assertFalse(adapter.execute("mcp__fixture__send", {}, approved=True).success)
        self.assertEqual(transport.calls, 0)
        self.assertTrue(adapter.execute("open_application", {}).success)
        self.assertTrue(registry.list_servers()[0]["activity"]["unavailable"])

    def test_worker_quota_settings_are_bounded_and_do_not_connect(self):
        registry = self.configured()
        inbox = MCPControlInbox()
        packet = json.dumps({"sessions_per_hour": 2, "calls_per_hour": 1})
        self.assertTrue(inbox.submit("set_quotas", "fixture", packet))
        perform_mcp_command(registry, inbox.pop_nowait())
        self.assertEqual(registry.list_servers()[0]["quotas"]["calls_per_hour"], 1)
        self.assertFalse(registry.activity().path.exists())
        self.assertFalse(inbox.submit("set_quotas", "fixture", '{"sessions_per_hour":true,"calls_per_hour":1}'))

    def test_discovery_failure_history_is_sanitized_and_not_live_connection(self):
        registry = self.configured()
        class FailedTransport:
            def discover(self, entry):
                raise RuntimeError("PRIVATE_DATA")
        with self.assertRaises(RuntimeError):
            perform_mcp_command(registry, MCPCommand("discover", "fixture"), transport=FailedTransport())
        snapshot = registry.list_servers()[0]
        self.assertFalse(snapshot["connected_now"])
        self.assertEqual(snapshot["activity"]["last_outcome"], "failed")
        self.assertNotIn("PRIVATE_DATA", repr(snapshot))


class MCPPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bearer_field_is_password_only_cleared_after_submission_not_snapshot(self):
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        data = {"enabled": True, "servers": [{"id": "service", "kind": "http", "tools": []}]}
        panel.apply_snapshot(data)
        panel.server_picker.setCurrentIndex(1)
        emitted = []
        panel.requested.connect(lambda *args: emitted.append(args))
        panel.bearer.setText("PRIVATE_FIXTURE")
        panel.apply_snapshot(data)
        self.assertEqual(panel.bearer.text(), "PRIVATE_FIXTURE")
        self.assertEqual(panel.bearer.echoMode(), QLineEdit.Password)
        panel.store_credential_button.click()
        self.assertEqual(panel.bearer.text(), "")
        self.assertEqual(emitted, [("store_bearer", "service", "PRIVATE_FIXTURE")])

    def test_oauth_only_emits_worker_command_with_cancel_accessible(self):
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        panel.apply_snapshot({"servers": [{"id": "service", "kind": "http", "tools": []}]})
        panel.server_picker.setCurrentIndex(1)
        emitted = []
        panel.requested.connect(lambda *args: emitted.append(args))
        with patch("webbrowser.open") as browser:
            panel.oauth_button.click()
        browser.assert_not_called()
        self.assertFalse(panel.server_picker.isEnabled())
        self.assertTrue(panel.cancel_oauth_button.isEnabled())
        panel.cancel_oauth_button.click()
        self.assertEqual(emitted, [("oauth_authorize", "service", '{"scope": ""}'), ("cancel_oauth", "service", "")])
        panel.show_result({"success": False, "operation": "oauth_authorize", "reason": "fixture failure"})
        self.assertTrue(panel.server_picker.isEnabled())
        self.assertFalse(panel.cancel_oauth_button.isEnabled())

    def test_quota_edits_survive_live_snapshots_until_saved(self):
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        data = {"servers": [{"id": "service", "kind": "http", "tools": [],
                             "quotas": {"sessions_per_hour": 10, "calls_per_hour": 5}}]}
        panel.apply_snapshot(data)
        panel.server_picker.setCurrentIndex(1)
        panel.call_quota.setValue(3)
        panel.apply_snapshot(data)
        self.assertEqual(panel.call_quota.value(), 3)
        panel.show_result({"operation": "set_quotas", "success": True})
        panel.apply_snapshot(data)
        self.assertEqual(panel.call_quota.value(), 5)
