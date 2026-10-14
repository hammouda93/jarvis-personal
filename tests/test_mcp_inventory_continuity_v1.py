"""Regression for bounded MCP identity -> inventory mission continuity.

Pure synthetic inputs, no accounts, network, credentials, UI, memory or skills.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

from jarvis_agent.request_context import RequestContext
from jarvis_agent.llm_mission_planner import parse_model_plan, MissionPlanError
from jarvis_agent.mcp_mission_read_recovery import (
    prior_identity_observation, unqueried_requested_lists,
)


def tool(name, *, requires=()):
    return {"type": "function", "function": {
        "name": name, "description": "Fixture authorized capability",
        "parameters": {"type": "object", "properties": {},
                       "required": list(requires)},
    }}


def action(name, success=True, *, dispatched=True, unknown=False, trust="external_unverified"):
    segments = name.split("__", 2)
    return SimpleNamespace(
        name=name, success=success,
        detail=json.dumps({
            "dispatched": dispatched, "outcome_unknown": unknown,
            "source": {
                "server_id": segments[1] if len(segments) == 3 else "",
                "tool_name": segments[2] if len(segments) == 3 else "",
                "trust": trust,
            },
        }),
    )


class ProgressiveContextTests(unittest.TestCase):
    def setUp(self):
        self.ctx = RequestContext(enabled=True)
        self.ctx.begin_turn("Review commits and pull requests in orbitdata")
        self.tools = [
            tool("mcp__orbitdata__list_commits", requires=("account",)),
            tool("mcp__orbitdata__list_pull_requests", requires=("account",)),
            tool("mcp__orbitdata__get_me"),
            tool("mcp__other__get_me"),
            tool("mcp__orbitdata__delete_repository"),
        ]

    def names(self):
        return {definition["function"]["name"]
                for definition in self.ctx.prepare_tools(self.tools)
                if definition.get("type") == "function"}

    def test_identity_selection_keeps_initial_authorized_read_inventory(self):
        initial = self.names()
        self.assertIn("mcp__orbitdata__list_commits", initial)
        self.assertIn("mcp__orbitdata__list_pull_requests", initial)
        self.ctx.select_tools({"names": "mcp__orbitdata__get_me"})
        later = self.names()
        self.assertTrue(initial.intersection({
            "mcp__orbitdata__list_commits",
            "mcp__orbitdata__list_pull_requests",
        }).issubset(later))
        self.assertIn("mcp__orbitdata__get_me", later)
        self.assertNotIn("mcp__orbitdata__delete_repository", later)

    def test_selection_is_additive_and_turn_scoped(self):
        self.names()
        self.ctx.select_tools({"names": ["mcp__orbitdata__get_me"]})
        self.ctx.select_tools({"names": ["mcp__orbitdata__list_commits"]})
        selected = self.names()
        self.assertIn("mcp__orbitdata__get_me", selected)
        self.assertIn("mcp__orbitdata__list_commits", selected)
        self.ctx.begin_turn("Hello")
        self.assertIsNone(self.ctx.selected)
        self.assertEqual(self.ctx.initial_mcp, set())


class IdentityObservationTests(unittest.TestCase):
    def test_repeat_identity_read_is_recognized_without_recalling_remote(self):
        reads = [action("mcp__orbitdata__get_me")]
        self.assertTrue(prior_identity_observation(
            reads, "mcp__orbitdata__get_me", {}
        ))
        self.assertFalse(prior_identity_observation(
            reads, "mcp__other__get_me", {}
        ))
        self.assertFalse(prior_identity_observation(
            reads, "mcp__orbitdata__get_me", {"account": "other"}
        ))
        self.assertFalse(prior_identity_observation(
            reads, "mcp__orbitdata__list_commits", {}
        ))

    def test_ambiguous_and_failed_outcomes_do_not_count_as_prior_identity(self):
        for prior in (
            action("mcp__orbitdata__get_me", success=False),
            action("mcp__orbitdata__get_me", unknown=True),
            action("mcp__orbitdata__get_me", dispatched=False),
            action("mcp__orbitdata__get_me", trust="untrusted_model_claim"),
        ):
            with self.subTest(prior=prior.detail):
                self.assertFalse(prior_identity_observation(
                    [prior], "mcp__orbitdata__get_me", {}
                ))

    def test_identity_does_not_substitute_commit_pr_or_ci_inventories(self):
        reads = [action("mcp__orbitdata__get_me")]
        definitions = [
            tool("mcp__orbitdata__get_me"),
            tool("mcp__orbitdata__list_commits"),
            tool("mcp__orbitdata__list_pull_requests"),
            tool("mcp__orbitdata__list_workflow_runs"),
            tool("mcp__other__list_workflow_runs"),
        ]
        goal = ("Analyse commits and pull requests of my orbitdata "
                "repository, vérifie la CI.")
        missing = unqueried_requested_lists(goal, reads, definitions)
        self.assertEqual(missing, (
            "mcp__orbitdata__list_commits",
            "mcp__orbitdata__list_pull_requests",
            "mcp__orbitdata__list_workflow_runs",
        ))


class OptionalPlanningMetadataTests(unittest.TestCase):
    def plan(self):
        return {
            "steps": [
                {"id": "inspect", "intent": "Inspecter la source réelle",
                 "depends_on": [], "required_evidence": ["source_observed"]},
            ],
            "unresolved": [],
        }

    def test_malformed_optional_binding_does_not_discard_steps_or_supply_proof(self):
        raw = self.plan()
        raw["fact_bindings"] = [
            {"requirement": "source_observed", "fact": "remote_owner"},
            {"requirement": "source_observed", "fact": "file_path",
             "permissions": "grant"},
            {"requirement": "not_a_proof", "fact": "file_path"},
        ]
        plan = parse_model_plan(json.dumps(raw), goal="Inspecter ma source")
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.metadata["fact_bindings"], [])
        self.assertTrue(plan.metadata["optional_bindings_ignored"])
        self.assertFalse(plan.metadata["authoritative"])
        self.assertFalse(plan.metadata["evidence_registered"])
        self.assertFalse(plan.metadata["automatic_execution"])

    def test_valid_optional_binding_is_preserved(self):
        raw = self.plan()
        raw["fact_bindings"] = [
            {"requirement": "source_observed", "fact": "file_path"}
        ]
        plan = parse_model_plan(json.dumps(raw), goal="Inspecter mon fichier")
        self.assertEqual(plan.metadata["fact_bindings"], raw["fact_bindings"])

    def test_core_plan_still_rejects_untrusted_extra_fields(self):
        raw = self.plan()
        raw["permissions_granted"] = True
        with self.assertRaises(MissionPlanError):
            parse_model_plan(json.dumps(raw), goal="Inspecter")


if __name__ == "__main__":
    unittest.main()
