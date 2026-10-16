"""Live Gmail regression shapes with local grants and fixture providers only."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock, patch

from jarvis_agent.agent_runtime import CerebrasResponsesAgent, GroqResponsesAgent
from jarvis_agent.agent_runtime import _feedback_only_operational_turn
from jarvis_agent.config import settings
from jarvis_agent.request_context import ContextToolRegistry, RequestContext
from tests.test_daily_use_regressions_v15 import response
from tests.test_gmail_connector_v18 import Fixture, schema


class GmailLiveReadTests(Fixture):
    def test_unread_count_is_a_request_and_operational_feedback_stays_feedback(self):
        for goal in ("Vérifie mon compte Gmail et indique le nombre de messages non lus.",
                     "Liste mes messages non lus sans rien modifier.",
                     "Peux-tu vérifier les emails non lus ?"):
            self.assertFalse(_feedback_only_operational_turn(goal))
        self.assertTrue(_feedback_only_operational_turn("Non, tu n’as pas recherché le bon contact."))

    def test_verification_request_recovers_preauthorized_read_hidden_by_schema_budget(self):
        tool = schema("GMAIL-GET-CURRENT-USER", {"hint": {
            "type": "string", "description": "Fixture schema detail. " * 340}})
        self.transport.tools = [tool]
        self.command("gmail_connect", {"provider": "pipedream", "binding_revision": 0, "access": "plugin"})
        for agent_type in (CerebrasResponsesAgent, GroqResponsesAgent):
            with self.subTest(provider=agent_type.__name__):
                context = RequestContext(enabled=True)
                gateway = self.gateway()
                agent = agent_type(ContextToolRegistry(gateway, context))
                agent.request_context, agent.api_key = context, "offline-fixture"
                client, requests, logs = Mock(), [], []
                alias = "mcp__gmail__GMAIL_GET_CURRENT_USER"
                def model(**request):
                    requests.append(deepcopy(request))
                    names = {t["function"]["name"] for t in request["tools"] if "function" in t}
                    if len(requests) == 1:
                        self.assertNotIn(alias, names)
                        self.assertIn("request_tool_capabilities", names)
                        return response("J’ai besoin de votre autorisation explicite pour lire Gmail.")
                    if len(requests) == 2:
                        self.assertIn("AUTHORIZED_MCP_READ_AVAILABLE", str(request["messages"]))
                        return response(name="request_tool_capabilities", arguments={"names": [alias]})
                    if len(requests) == 3:
                        self.assertIn(alias, names)
                        return response(name=alias)
                    return response("Le compte de test a répondu à la lecture demandée.")
                client.chat.completions.create.side_effect = model
                agent._client = client
                before = len(self.transport.calls)
                with patch("jarvis_agent.agent_runtime.settings", replace(settings, agent_max_tool_rounds=6)):
                    result = agent.run("Vérifie mon compte Gmail et indique le nombre de messages non lus, sans rien modifier.", log=logs.append)
                self.assertEqual(len(self.transport.calls), before + 1)
                self.assertEqual([a.name for a in result.actions], ["request_tool_capabilities", alias])
                self.assertEqual(len(requests), 4)
                self.assertIn("repair=preauthorized_mcp_read_no_tool", " ".join(logs))
                self.assertFalse(gateway.requires_confirmation(alias))
