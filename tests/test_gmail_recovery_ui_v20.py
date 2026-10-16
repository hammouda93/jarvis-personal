"""Gmail journal shortcuts, real local receipts, no network or mailbox access."""
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from jarvis_agent.gmail_connection_panel import GmailConnectionPanel
from jarvis_agent.mcp_activity import MCPActivityStore, MCPBudgetExceeded
from jarvis_agent.mcp_control import MCPCommand, perform_mcp_command
from jarvis_agent.mcp_operator_panel import MCPConnectionsPanel
from tests.test_gmail_connector_v18 import Fixture


class GmailRecoveryUITests(Fixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.command("gmail_connect", {"provider": "pipedream", "binding_revision": 0, "access": "plugin"})
        self.clock = 10000.0
        self.activity = MCPActivityStore(self.registry.activity().path, clock=lambda: self.clock)
        self.activity_patch = patch.object(self.registry, "activity", return_value=self.activity)
        self.activity_patch.start()
        self.addCleanup(self.activity_patch.stop)

    def receipt(self, tool="GMAIL-SEARCH-EMAILS", *, outcome="unknown", server="gmail"):
        attempt = self.activity.reserve(server, "call", {}, tool=tool)
        if outcome != "reserved":
            self.activity.finish(attempt, outcome)
        self.clock += 16
        return attempt

    def panel(self):
        panel = GmailConnectionPanel(registry=self.registry, workspace=self.workspace)
        self.addCleanup(panel.close)
        return panel

    def parent_panel(self):
        panel = MCPConnectionsPanel()
        self.addCleanup(panel.close)
        # The parent and its Gmail child use separate registry instances in
        # production too. Both snapshots read the same persisted journal.
        panel.gmail_panel.registry = self.registry
        panel.apply_snapshot({"enabled": True, "servers": self.registry.list_servers()})
        return panel

    def test_initial_page_and_refresh_do_not_acknowledge_or_replay_unknown_read(self):
        attempt = self.receipt()
        before = self.registry.path.read_bytes()
        with patch("webbrowser.open") as browser:
            panel = self.panel()
            panel.refresh()
        browser.assert_not_called()
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(before, self.registry.path.read_bytes())
        self.assertFalse(panel.recovery_section.isHidden())
        self.assertIn("#" + str(attempt), panel.recovery_note.text())
        self.assertIn("incertain", panel.recovery_note.text())
        self.assertIn("ne relance rien", panel.recovery_note.text())
        self.assertTrue(panel.recovery_button.isEnabled())
        self.assertTrue(panel.test_button.isEnabled())
        self.assertTrue(panel.advanced_body.isHidden())
        with self.assertRaisesRegex(MCPBudgetExceeded, "mcp_unknown_requires_review"):
            self.activity.reserve("gmail", "call", {}, tool="GMAIL-SEARCH-EMAILS")

    def test_shortcut_focuses_exact_receipt_and_requires_confirmation_before_review(self):
        attempt = self.receipt()
        parent = self.parent_panel()
        emitted = []
        parent.requested.connect(lambda *args: emitted.append(args))
        with patch.object(QMessageBox, "question") as confirm:
            parent.gmail_panel.recovery_button.click()
        confirm.assert_not_called()
        self.assertEqual(emitted, [])
        self.assertIs(parent.tabs.currentWidget(), parent.services_page)
        self.assertEqual(parent.server_picker.currentData(), "gmail")
        self.assertEqual(parent.service_tabs.currentIndex(), 2)
        self.assertEqual(parent.outcome_picker.currentData(), attempt)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            parent.review_outcome_button.click()
        self.assertEqual(emitted, [])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            parent.review_outcome_button.click()
        self.assertEqual(emitted, [("review_outcome", "gmail", str(attempt))])
        result = perform_mcp_command(self.registry, MCPCommand(*emitted.pop()))
        parent.show_result(result)
        self.assertTrue(parent.gmail_panel.recovery_section.isHidden())
        self.assertIn("aucun appel", parent.gmail_panel.feedback.text())
        self.assertEqual(self.activity.summary("gmail")["last_outcome"], "unknown")
        self.assertEqual(self.transport.calls, [])

    def test_live_reservation_cannot_be_reviewed_before_safety_delay(self):
        attempt = self.receipt(outcome="reserved")
        panel = self.panel()
        requested = []
        panel.review_requested.connect(lambda *args: requested.append(args))
        self.assertFalse(panel.recovery_button.isEnabled())
        self.assertIn("Réponse en attente", panel.recovery_note.text())
        panel.open_recovery()
        self.assertEqual(requested, [])
        with self.assertRaisesRegex(ValueError, "mcp_call_may_still_be_running"):
            self.activity.review_outcome("gmail", attempt)
        self.clock += 45
        panel.refresh()
        self.assertTrue(panel.recovery_button.isEnabled())
        panel.recovery_button.click()
        self.assertEqual(requested, [("gmail", attempt)])
        self.assertEqual(self.activity.summary("gmail")["last_outcome"], "reserved")

    def test_stale_shortcut_does_not_silently_switch_to_next_unknown_call(self):
        first = self.receipt()
        second = self.receipt("GMAIL-SEND-EMAIL")
        panel = self.panel()
        self.assertIn("#" + str(first), panel.recovery_button.text())
        requested = []
        panel.review_requested.connect(lambda *args: requested.append(args))
        self.activity.review_outcome("gmail", first)
        panel.recovery_button.click()
        self.assertEqual(requested, [])
        self.assertIn("journal a changé", panel.feedback.text())
        self.assertIn("#" + str(second), panel.recovery_button.text())
        panel.recovery_button.click()
        self.assertEqual(requested, [("gmail", second)])
        self.assertEqual(self.activity.summary("gmail")["unresolved"][0]["attempt_id"], second)

    def test_other_apps_and_reviewed_receipts_do_not_appear_as_gmail_blockers(self):
        self.receipt("list_calendar_events")
        self.receipt(server="another")
        reviewed = self.receipt("GMAIL-SEND-EMAIL")
        self.activity.review_outcome("gmail", reviewed)
        panel = self.panel()
        self.assertTrue(panel.recovery_section.isHidden())
        self.assertFalse(panel.recovery_button.isEnabled())
        self.assertEqual(len(self.activity.summary("gmail")["unresolved"]), 1)

    def test_missing_journal_is_visible_and_never_presented_as_a_reviewable_receipt(self):
        with patch.object(self.activity, "summary", side_effect=OSError("fixture failure")):
            panel = self.panel()
            self.assertFalse(panel.recovery_section.isHidden())
            self.assertIn("Journal MCP indisponible", panel.recovery_note.text())
            requested, reviews = [], []
            panel.advanced_requested.connect(requested.append)
            panel.review_requested.connect(lambda *args: reviews.append(args))
            panel.recovery_button.click()
        self.assertEqual(requested, ["gmail"])
        self.assertEqual(reviews, [])
        self.assertEqual(self.transport.calls, [])

    def test_navigation_does_not_select_a_different_receipt_if_snapshot_is_stale(self):
        first = self.receipt()
        parent = self.parent_panel()
        self.activity.review_outcome("gmail", first)
        second = self.receipt("GMAIL-SEND-EMAIL")
        # Child refreshes immediately, while the parent has not received its
        # next worker snapshot yet. It must not select the older receipt.
        parent.gmail_panel.refresh()
        parent.gmail_panel.recovery_button.click()
        self.assertEqual(parent.outcome_picker.currentIndex(), 0)
        self.assertIn("actualisation", parent.gmail_panel.feedback.text())
        self.assertEqual(self.activity.summary("gmail")["unresolved"][0]["attempt_id"], second)

    def test_busy_connection_and_changed_binding_cannot_open_a_stale_review(self):
        self.receipt()
        panel = self.panel()
        requested = []
        panel.review_requested.connect(lambda *args: requested.append(args))
        panel.set_busy(True)
        self.assertFalse(panel.recovery_button.isEnabled())
        panel.open_recovery()
        self.assertEqual(requested, [])
        panel.set_busy(False)
        self.registry.add_http("second", "https://fixture.example.test/mcp")
        self.workspace.bind_service("gmail", "second", expected_revision=1)
        panel.open_recovery()
        self.assertEqual(requested, [])
        self.assertTrue(panel.recovery_section.isHidden())

    def test_narrow_page_exposes_recovery_without_horizontal_scroll(self):
        self.receipt()
        panel = self.panel()
        panel.resize(300, 500)
        panel.show()
        self.app.processEvents()
        panel.scroll.ensureWidgetVisible(panel.recovery_button)
        self.app.processEvents()
        self.assertEqual(panel.scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(panel.scroll.widget().width(), panel.scroll.viewport().width())
        self.assertLessEqual(panel.recovery_button.mapTo(panel.scroll.viewport(), panel.recovery_button.rect().bottomRight()).y(), panel.scroll.viewport().height())
        self.assertTrue(panel.advanced_body.isHidden())
