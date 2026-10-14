"""Regression: first MCP dispatch must not crash on shadowed read helper.

Pure runtime contract: Python marks imports inside a function as local
bindings across the *entire* function, even before the import executes.
The first MCP call previously hit UnboundLocalError while checking the
cached identity observation (before dispatch, before any tool result).
"""
from __future__ import annotations

import unittest

from jarvis_agent.agent_runtime import GroqResponsesAgent
from jarvis_agent.mcp_mission_read_recovery import prior_identity_observation


class MCPIdentityLocalScopeRegressionTests(unittest.TestCase):
    def test_identity_guard_is_module_binding_not_uninitialized_run_local(self):
        code = GroqResponsesAgent.run.__code__
        self.assertIn("prior_identity_observation", code.co_names)
        self.assertNotIn("prior_identity_observation", code.co_varnames)
        self.assertTrue(callable(prior_identity_observation))

    def test_unobserved_identity_does_not_block_first_mcp_call(self):
        self.assertFalse(prior_identity_observation(
            [], "mcp__fixture__get_me", {}
        ))


if __name__ == "__main__":
    unittest.main()
