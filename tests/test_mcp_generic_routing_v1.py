"""Regression: MCP-capable mission starts with bounded permitted tools; never guesses URLs."""
from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from jarvis_agent.navigation_url_guard import unresolved_url_template
from jarvis_agent.request_context import RequestContext
from jarvis_agent.foundation_tools import FoundationToolAdapter


def schema(name, *, description="read-only tool", size=0):
    return {"type": "function", "function": {
        "name": name, "description": description + "a" * size,
        "parameters": {"type": "object", "properties": {}},
    }}


def names(definitions):
    return {x.get("function", {}).get("name") for x in definitions}


class GenericMCPMissionRoutingTests(unittest.TestCase):
    def test_real_registry_policy_is_the_only_source_of_mcp_schemas(self):
        from pathlib import Path
        import tempfile
        from jarvis_agent.mcp_server_registry import MCPRegistry
        from jarvis_agent.mcp_tool_registry import MCPToolRegistry

        with tempfile.TemporaryDirectory() as temp:
            registry = MCPRegistry(Path(temp) / "servers.json")
            registry.add_http("fixture", "https://fixture.example/mcp")
            registry.set_enabled("fixture", True)
            registry.discover("fixture", [{"name": "read_items",
                "description": "Read fixture", "input_schema": {"type": "object"}}])
            delegate = Mock()
            delegate.ollama_tools.return_value = [schema("open_url")]
            tools = MCPToolRegistry(delegate, registry=registry,
                                    runtime_gate=registry.runtime_enabled)
            registry.set_runtime_enabled(True)
            self.assertNotIn("mcp__fixture__read_items", names(
                RequestContext(enabled=True).prepare_tools(tools.ollama_tools())))
            registry.allow_tool("fixture", "read_items", True)
            self.assertIn("mcp__fixture__read_items", names(
                RequestContext(enabled=True).prepare_tools(tools.ollama_tools())))
            registry.set_runtime_enabled(False)
            self.assertNotIn("mcp__fixture__read_items", names(
                RequestContext(enabled=True).prepare_tools(tools.ollama_tools())))
            registry.set_runtime_enabled(True)
            registry.set_enabled("fixture", False)
            self.assertNotIn("mcp__fixture__read_items", names(
                RequestContext(enabled=True).prepare_tools(tools.ollama_tools())))

    def test_user_goal_ranks_commit_and_pull_request_reads_before_unrelated_tools(self):
        context = RequestContext(enabled=True)
        context.begin_turn("Inspecte les commits et pull requests de mon dépôt")
        unrelated = [schema(f"mcp__fixture__get_issue_{i}") for i in range(14)]
        related = [schema("mcp__fixture__list_commits"),
                   schema("mcp__fixture__pull_request_read")]
        chosen = context.prepare_tools(unrelated + related, browser_mode=True)
        self.assertIn("mcp__fixture__list_commits", names(chosen))
        self.assertIn("mcp__fixture__pull_request_read", names(chosen))
        self.assertLessEqual(len(names(chosen) & names(unrelated + related)), 6)

    def test_already_authorized_mcp_visible_on_first_mission_turn(self):
        context = RequestContext(enabled=True)
        definitions = [schema("browser_list_tabs"), schema("open_url"),
                       schema("mcp__fixture__list_commits"),
                       schema("mcp__fixture__pull_request_read")]
        chosen = context.prepare_tools(definitions, browser_mode=True)
        self.assertIn("mcp__fixture__list_commits", names(chosen))
        self.assertIn("mcp__fixture__pull_request_read", names(chosen))
        self.assertIn("browser_list_tabs", names(chosen))
        # This is selection of EXISTING schemas only, not permission or dispatch.
        self.assertIn("request_tool_capabilities", names(chosen))

    def test_large_mcp_inventories_have_bounded_initial_prompt(self):
        schemas = [schema(f"mcp__fixture__read_{i}") for i in range(20)]
        context = RequestContext(enabled=True)
        initial = context.prepare_tools(schemas, browser_mode=False)
        self.assertEqual(len(names(initial) & names(schemas)), 6)
        self.assertEqual(len(context.available & names(schemas)), 20)
        context.select_tools({"names": ["mcp__fixture__read_18"]})
        selected = context.prepare_tools(schemas, browser_mode=False)
        self.assertIn("mcp__fixture__read_18", names(selected))
        self.assertNotIn("mcp__fixture__read_1", names(selected))

    def test_large_mcp_schema_not_forced_into_model_context(self):
        context = RequestContext(enabled=True)
        big = schema("mcp__fixture__gigantic", size=6600)
        selected = context.prepare_tools(
            [big, schema("mcp__fixture__read_small")])
        self.assertNotIn("mcp__fixture__gigantic", names(selected))
        self.assertIn("mcp__fixture__read_small", names(selected))
        self.assertIn("mcp__fixture__gigantic", context.available)

    def test_only_scoped_capabilities_can_be_exposed(self):
        context = RequestContext(enabled=True)
        selected = context.prepare_tools(
            [schema("browser_list_tabs"), schema("open_url")], browser_mode=True)
        self.assertNotIn("mcp__fixture__read", context.available)
        self.assertNotIn("mcp__fixture__read", names(selected))
        with self.assertRaises(ValueError):
            context.select_tools({"names": ["mcp__fixture__read"]})

    def test_documentation_placeholders_are_rejected(self):
        for url in (
            "https://github.com/your-username/jarvis-personal",
            "https://example.org/{owner}/repo",
            "https://example.org/%7Busername%7D/project",
            "https://git.example.net/<organization>/project",
            "https://host.example.org/insert-repo/docs",
        ):
            with self.subTest(url=url):
                self.assertTrue(unresolved_url_template(url))
        for url in (
            "https://github.com/hammouda93/jarvis-personal",
            "https://github.com/example-project/project",
            "https://example.org/users/me?sort=asc",
            "https://example.org/articles/your-account-settings",
        ):
            with self.subTest(url=url):
                self.assertFalse(unresolved_url_template(url))

    def test_placeholder_is_pre_dispatch_rejection_in_foundation_browser(self):
        delegate = Mock()
        browser = Mock()
        adapter = FoundationToolAdapter(delegate, browser=browser)
        for name, args in (
            ("open_url", {"url": "https://github.com/your-username/jarvis-personal"}),
            ("browser_navigate", {"tab_id": 11, "url": "https://host.tld/{repo}"}),
            ("browser_download", {"url": "https://host.tld/<filename>"}),
        ):
            with self.subTest(name=name):
                result = adapter.execute(name, args)
                self.assertFalse(result.success)
                detail = json.loads(result.detail)
                self.assertEqual(detail["guard"], "unresolved_url_template")
                self.assertIs(detail["dispatched"], False)
                self.assertIs(detail["outcome_unknown"], False)
        browser.navigate.assert_not_called()
        browser.call.assert_not_called()
        delegate.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
