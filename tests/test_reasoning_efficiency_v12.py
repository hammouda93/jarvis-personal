"""Expose useful schemas and bound repeated selection with no new information."""
from __future__ import annotations

import unittest

from jarvis_agent.request_context import RequestContext, ContextToolRegistry
from tests import test_mcp_autonomy_v12 as fixtures


def definition(name, *, required=None):
    parameters = {"type": "object", "properties": {}}
    if required is not None:
        parameters["required"] = required
    return {"type": "function", "function": {
        "name": name, "description": "Fixture", "parameters": parameters}}


class Catalog(fixtures.NativeStub):
    def ollama_tools(self):
        return [definition("open_application")]


class ContextAgent(fixtures.ScriptedAgent):
    def _chat(self, **kwargs):
        self.request_context.prepare_tools(self.tools.ollama_tools())
        return super()._chat(**kwargs)


class ReasoningEfficiencyTests(unittest.TestCase):
    def test_missing_required_keyword_means_parameter_free_for_identity_ranking(self):
        context = RequestContext(enabled=True)
        context.begin_turn("Inspecte mon dépôt orbitdata : commits et pull requests")
        chosen = context.prepare_tools([
            definition("mcp__orbitdata__list_commits", required=["owner"]),
            definition("mcp__orbitdata__list_pull_requests", required=["owner"]),
            definition("mcp__orbitdata__get_me"),
        ])
        names = [item["function"]["name"] for item in chosen]
        self.assertLess(names.index("mcp__orbitdata__get_me"),
                        names.index("mcp__orbitdata__list_commits"))

    def test_selection_reports_already_exposed_names_without_changing_permissions(self):
        context = RequestContext(enabled=True)
        context.prepare_tools([definition("open_application")])
        result = context.select_tools({"names": ["open_application"]})
        self.assertEqual(result["already_exposed"], ["open_application"])
        self.assertFalse(result["permissions_changed"])
        self.assertFalse(result["dispatched"])

    def test_repeated_schema_selection_stops_without_spending_the_whole_round_budget(self):
        context = RequestContext(enabled=True)
        native = Catalog()
        tools = ContextToolRegistry(native, context)
        agent = ContextAgent(tools, [fixtures.response(
            ("request_tool_capabilities", {"names": ["open_application"]}))
            for _ in range(9)])
        agent.request_context = context
        result = agent.run("Ouvre une application disponible.")
        self.assertEqual(result.pause_reason, "capability_selection_no_progress")
        self.assertEqual(agent.chat.call_count, 2)
        self.assertEqual(native.calls, [])
        self.assertIn("non certifiée", result.text)


if __name__ == "__main__":
    unittest.main()
