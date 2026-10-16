"""Actual brains/gateway contracts with simulated brains and Google HTTP."""
from copy import deepcopy
import os
from unittest.mock import Mock, patch

import httpx2

from jarvis_agent.agent_profiles import AgentProfileStore
from jarvis_agent.agent_runtime import CerebrasResponsesAgent, GroqResponsesAgent
from jarvis_agent.google_workspace_api import GoogleWorkspaceAPI
from jarvis_agent.mcp_tool_registry import MCPToolRegistry
from jarvis_agent.orchestra_workers import WorkerToolGate
from jarvis_agent.request_context import ContextToolRegistry, RequestContext
from jarvis_agent.workspace_tools import WorkspaceToolRegistry
from tests.google_workspace_fixtures import GoogleFixture, PROFILE
from tests.test_agent_runtime import FakeTools
from tests.test_daily_use_regressions_v15 import response


class GoogleAgentTests(GoogleFixture):
    def test_groq_and_cerebras_select_native_schema_and_dispatch_real_adapter_once(self):
        entry = self.seed_account()
        calls = []
        def google(request):
            calls.append(request)
            return httpx2.Response(200, json={"emailAddress": PROFILE["email"]})
        api = GoogleWorkspaceAPI(vault=self.vault, transport=httpx2.MockTransport(google))
        alias = "mcp__" + entry["id"] + "__gmail_get_profile"
        for brain in (GroqResponsesAgent, CerebrasResponsesAgent):
            with self.subTest(brain=brain.__name__):
                context = RequestContext(enabled=True)
                gateway = MCPToolRegistry(FakeTools(), registry=self.registry, transport=api, runtime_gate=self.registry.runtime_enabled)
                agent = brain(ContextToolRegistry(gateway, context))
                agent.request_context, agent.api_key = context, "offline"
                payloads = []
                def model(**payload):
                    payloads.append(deepcopy(payload))
                    if len(payloads) == 1:
                        return response(name="request_tool_capabilities", arguments={"names": [alias]})
                    if len(payloads) == 2:
                        names = {s["function"]["name"] for s in payload["tools"] if "function" in s}
                        self.assertIn(alias, names)
                        return response(name=alias)
                    return response("Le service indique owner@example.test.")
                agent._client = Mock()
                agent._client.chat.completions.create.side_effect = model
                before = len(calls)
                with patch.dict(os.environ, {"JARVIS_EFFICIENT_CONTEXT_ENABLED": "1"}):
                    result = agent.run("Vérifie le compte Gmail réellement connecté, sans modifier ma boîte.")
                self.assertIn(PROFILE["email"], result.text)
                self.assertEqual(len(calls) - before, 1)
                self.assertEqual(calls[-1].method, "GET")

    def test_specialists_receive_bound_google_reads_only_and_profiles_still_narrow(self):
        entry = self.seed_account()
        profiles = AgentProfileStore(self.root / "profiles.json")
        gateway = MCPToolRegistry(FakeTools(), registry=self.registry, transport=GoogleWorkspaceAPI())
        delegate = WorkspaceToolRegistry(gateway, mcp_tools=gateway, store=self.workspace)
        prefix = "mcp__" + entry["id"] + "__"
        mapping = {"communications": ("gmail_get_profile", "get_document"),
            "documents": ("get_document", "get_sheet_values"), "data": ("get_sheet_values", "gmail_get_profile"),
            "automations": ("list_calendar_events", "gmail_get_profile")}
        with patch.dict(os.environ, {"JARVIS_PERSONAL_WORKSPACE_PATH": str(self.workspace.path)}):
            for role, (yes, no) in mapping.items():
                gate = WorkerToolGate(delegate, policy={"role": role, "profile": profiles.get(role),
                    "profile_path": str(profiles.path), "tools": None}, mcp_tools=gateway)
                with self.subTest(role=role):
                    allowed = gate.allowed()
                    self.assertIn(prefix + yes, allowed)
                    self.assertNotIn(prefix + no, allowed)
                    self.assertNotIn(prefix + "gmail_send_message", allowed)
                    self.assertNotIn(prefix + "create_calendar_event", allowed)
            self.registry.allow_tool(entry["id"], "list_calendar_events", False)
            gate = WorkerToolGate(delegate, policy={"role": "automations", "profile": profiles.get("automations"),
                "profile_path": str(profiles.path), "tools": None}, mcp_tools=gateway)
            self.assertNotIn(prefix + "list_calendar_events", gate.allowed())
