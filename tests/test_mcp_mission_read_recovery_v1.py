"""Generic MCP recovery/coverage tests; zero external calls and no credentials."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from jarvis_agent.mcp_mission_read_recovery import (
    discovery_followup, unqueried_requested_lists,
)


def action(name, *, success=False, detail=""):
    return SimpleNamespace(name=name, success=success, detail=detail)


def tool(name):
    return {"type": "function", "function": {
        "name": name, "description": "Authorized generic synthetic tool",
        "parameters": {"type": "object", "properties": {}},
    }}


class MCPReadRecoveryTests(unittest.TestCase):
    def test_failed_placeholder_recovers_approved_same_service_reads(self):
        refusal = action("mcp__orbitdata__list_records", detail=json.dumps({
            "guard": "unresolved_argument_template", "argument_paths": ["account"],
            "dispatched": False, "outcome_unknown": False,
            "discovery_candidates": [
                "mcp__orbitdata__get_me",
                "mcp__other__get_me",
                "mcp__orbitdata__get_profile",
                "mcp__orbitdata__get_me",
                "mcp__orbitdata__update_records",
            ],
        }))
        result = discovery_followup([refusal])
        self.assertEqual(result["approved_read_candidates"], [
            "mcp__orbitdata__get_me", "mcp__orbitdata__get_profile",
        ])
        # Candidate strings alone never cause a tool execution.
        self.assertNotIn("owner", result)
        self.assertNotIn("verified", result)

    def test_no_repair_on_dispatched_or_unknown_or_untyped_failure(self):
        for detail in (
            {"guard": "unresolved_argument_template", "dispatched": True,
             "outcome_unknown": False, "discovery_candidates": ["mcp__svc__get_me"]},
            {"guard": "unresolved_argument_template", "dispatched": False,
             "outcome_unknown": True, "discovery_candidates": ["mcp__svc__get_me"]},
            {"guard": "unrelated", "dispatched": False,
             "outcome_unknown": False, "discovery_candidates": ["mcp__svc__get_me"]},
            {"guard": "unresolved_argument_template", "dispatched": False,
             "outcome_unknown": False, "discovery_candidates": []},
        ):
            with self.subTest(detail=detail):
                self.assertIsNone(discovery_followup([
                    action("mcp__svc__list_records", detail=json.dumps(detail))
                ]))

    def test_successful_identity_read_does_not_verify_other_inventories(self):
        goal = ("Inspect my orbitdata commits and pull requests, "
                "verify their current statuses and summarize.")
        catalog = [
            tool("mcp__orbitdata__get_me"),
            tool("mcp__orbitdata__get_commit"),
            tool("mcp__orbitdata__list_commits"),
            tool("mcp__orbitdata__list_pull_requests"),
            tool("mcp__orbitdata__list_branches"),
            tool("mcp__elsewhere__list_pull_requests"),
        ]
        acts = [
            action("mcp__orbitdata__get_me", success=True),
            action("mcp__orbitdata__get_commit", success=True),
        ]
        self.assertEqual(unqueried_requested_lists(goal, acts, catalog), (
            "mcp__orbitdata__list_commits",
            "mcp__orbitdata__list_pull_requests",
        ))
        acts += [
            action("mcp__orbitdata__list_commits", success=True),
            action("mcp__orbitdata__list_pull_requests", success=True),
        ]
        self.assertEqual(unqueried_requested_lists(goal, acts, catalog), ())

    def test_failed_list_is_not_successful_coverage(self):
        goal = "List all invoices in billingapi"
        catalog = [tool("mcp__billingapi__list_invoices")]
        reads = [action("mcp__billingapi__list_invoices", success=False)]
        self.assertEqual(unqueried_requested_lists(goal, reads, catalog),
                         ("mcp__billingapi__list_invoices",))

    def test_unrequested_lists_and_unconnected_services_are_ignored(self):
        goal = "Find the account owner and report their username"
        catalog = [tool("mcp__orbitdata__list_invoices")]
        self.assertEqual(unqueried_requested_lists(
            goal, [action("mcp__orbitdata__get_me", success=True)], catalog
        ), ())
        self.assertEqual(unqueried_requested_lists(goal, [], catalog), ())


if __name__ == "__main__":
    unittest.main()
