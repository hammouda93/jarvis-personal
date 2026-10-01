import copy
import unittest
from types import SimpleNamespace

from jarvis_agent.agent_runtime import (
    OllamaToolAgent,
    OpenAIResponsesAgent,
    GroqResponsesAgent,
    CerebrasResponsesAgent,
    _looks_like_action_promise,
    _looks_like_unnecessary_followup,
    _is_explicit_memory_write_request,
    _looks_like_memory_permission_prompt,
    _query_matches_recent_user_context,
    _looks_mostly_english,
    _visible_text,
)
from jarvis_agent.native_tools import AgentActionResult


class FakeTools:
    def __init__(self):
        self.calls = []

    def ollama_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "open_application",
                    "description": "open app",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "remember_information",
                    "description": "remember persistent information",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "tags": {"type": "string"},
                        },
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "recall_information",
                    "description": "recall persistent information",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "reset_conversation_context",
                    "description": "reset temporary conversation context",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "msf_capabilities",
                    "description": "discover MS Football",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "msf_count_records",
                    "description": "count MS Football records",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string"},
                            "filters": {"type": "object"},
                        },
                        "required": ["model"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def openai_tools(self):
        result = []
        for item in self.ollama_tools():
            fn = item["function"]
            result.append(
                {
                    "type": "function",
                    "name": fn["name"],
                    "description": fn["description"],
                    "parameters": fn["parameters"],
                    "strict": True,
                }
            )
        return result

    def requires_confirmation(self, name):
        return name == "msf_commit_mutation"

    def execute(self, name, arguments, *, approved=False):
        self.calls.append((name, arguments))
        return AgentActionResult(
            name=name,
            success=True,
            message="ok",
            detail=str(arguments),
        )


class FakeOllamaAgent(OllamaToolAgent):
    def __init__(self, tools, responses):
        super().__init__(tools)
        self.responses = list(responses)
        self.payloads = []

    def _post(self, payload):
        self.payloads.append(payload)
        return self.responses.pop(0)


class FakeOpenAIAgent(OpenAIResponsesAgent):
    def __init__(self, tools, responses):
        super().__init__(tools)
        self.responses = list(responses)
        self.payloads = []
        self.api_key = "test"

    def _post(self, payload):
        self.payloads.append(payload)
        return self.responses.pop(0)


class FakeGroqAgent(GroqResponsesAgent):
    def __init__(self, tools, responses):
        super().__init__(tools)
        self.responses = list(responses)
        self.payloads = []
        self.api_key = "test"

    @staticmethod
    def _response_from_dict(data):
        content = ""
        tool_calls = []
        for item in data.get("output") or []:
            if item.get("type") == "function_call":
                tool_calls.append(
                    SimpleNamespace(
                        id=item.get("call_id") or item.get("id"),
                        function=SimpleNamespace(
                            name=item.get("name"),
                            arguments=item.get("arguments") or "{}",
                        ),
                    )
                )
            elif item.get("type") == "message":
                parts = [
                    part.get("text", "")
                    for part in item.get("content") or []
                    if part.get("type") == "output_text"
                ]
                content = "\n".join(part for part in parts if part)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=content,
                        tool_calls=tool_calls,
                    )
                )
            ]
        )

    def _chat(
        self,
        *,
        tool_choice="auto",
        ms_football_only=False,
        msf_tool_names=None,
    ):
        self.payloads.append(
            {
                "messages": copy.deepcopy(self._messages),
                "tools": copy.deepcopy(
                    self._tool_definitions(
                        ms_football_only=ms_football_only,
                        msf_tool_names=msf_tool_names,
                    )
                ),
                "tool_choice": tool_choice,
                "ms_football_only": ms_football_only,
                "msf_tool_names": set(msf_tool_names or ()),
            }
        )
        return self._response_from_dict(self.responses.pop(0))


class AgentRuntimeTests(unittest.TestCase):

    def test_groq_blocks_persistent_recall_for_current_session_question(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Nous parlons du projet Atlas Nova.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_recall_current",
                            "name": "recall_information",
                            "arguments": "{\"query\":\"Atlas Nova\"}",
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Le projet s'appelle Atlas Nova.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        agent.run("Nous parlons du projet Atlas Nova.")
        result = agent.run(
            "Quel est le nom du projet dont on parle maintenant ?"
        )

        self.assertNotIn(
            ("recall_information", {"query": "Atlas Nova"}),
            tools.calls,
        )
        self.assertEqual(len(result.actions), 1)
        self.assertFalse(result.actions[0].success)
        self.assertEqual(
            result.actions[0].detail,
            "persistent_recall_blocked_current_context",
        )

    def test_groq_hides_persistent_memory_write_tool_on_ordinary_turn(self):
        agent = FakeGroqAgent(
            FakeTools(),
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "Compris."}
                            ],
                        }
                    ]
                }
            ],
        )

        agent.run("Je travaille sur le projet Vega One.")

        names = {
            item["function"]["name"]
            for item in agent.payloads[0]["tools"]
            if item.get("type") == "function"
        }
        self.assertNotIn("remember_information", names)
        self.assertIn("recall_information", names)
        self.assertIn("reset_conversation_context", names)

    def test_groq_exposes_persistent_memory_write_tool_on_explicit_request(self):
        agent = FakeGroqAgent(
            FakeTools(),
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "Compris."}
                            ],
                        }
                    ]
                }
            ],
        )

        agent.run("Retiens que mon projet s'appelle Vega One.")

        names = {
            item["function"]["name"]
            for item in agent.payloads[0]["tools"]
            if item.get("type") == "function"
        }
        self.assertIn("remember_information", names)

    def test_memory_permission_prompt_is_detected(self):
        self.assertTrue(
            _looks_like_memory_permission_prompt(
                "Souhaitez-vous que je retienne cette information ?"
            )
        )
        self.assertTrue(
            _looks_like_memory_permission_prompt(
                "Souhaitez‑vous que je retienne cette information ?"
            )
        )
        self.assertFalse(
            _looks_like_memory_permission_prompt(
                "D'accord, parlons de votre projet Atlas."
            )
        )

    def test_groq_does_not_ask_to_persist_ordinary_conversation_fact(self):
        agent = FakeGroqAgent(
            FakeTools(),
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        "Souhaitez-vous que je retienne "
                                        "cette information ?"
                                    ),
                                }
                            ],
                        }
                    ]
                }
            ],
        )

        result = agent.run(
            "Je travaille sur un projet qui s'appelle Atlas Scope."
        )

        self.assertNotIn("souhaitez-vous", result.text.lower())
        self.assertIn("cette conversation", result.text.lower())

    def test_memory_write_requires_explicit_user_request(self):
        self.assertFalse(
            _is_explicit_memory_write_request(
                "Mon deuxième projet s'appelle Neptune."
            )
        )
        self.assertFalse(
            _is_explicit_memory_write_request(
                "Je travaille actuellement sur le projet Atlas."
            )
        )
        self.assertTrue(
            _is_explicit_memory_write_request(
                "Retiens que mon projet préféré s'appelle Orion."
            )
        )
        self.assertTrue(
            _is_explicit_memory_write_request(
                "Garde ça en mémoire pour plus tard."
            )
        )

    def test_groq_blocks_unsolicited_persistent_memory_write(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_memory_blocked",
                            "name": "remember_information",
                            "arguments": "{\"content\":\"Projet Neptune\"}",
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "D'accord.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Mon deuxième projet s'appelle Neptune.")

        self.assertEqual(tools.calls, [])
        self.assertEqual(len(result.actions), 1)
        self.assertFalse(result.actions[0].success)
        self.assertEqual(
            result.actions[0].detail,
            "memory_write_blocked_not_explicit",
        )

    def test_groq_allows_explicit_persistent_memory_write(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_memory_allowed",
                            "name": "remember_information",
                            "arguments": "{\"content\":\"Projet Orion\"}",
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "C'est mémorisé.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run(
            "Retiens que mon projet de test préféré s'appelle Orion."
        )

        self.assertEqual(
            tools.calls,
            [("remember_information", {"content": "Projet Orion"})],
        )
        self.assertEqual(len(result.actions), 1)
        self.assertTrue(result.actions[0].success)

    def test_action_promise_is_detected(self):
        self.assertTrue(
            _looks_like_action_promise(
                "Je vais chercher cette information pour vous."
            )
        )
        self.assertTrue(
            _looks_like_action_promise(
                "J'ai ouvert Chrome. Je vais maintenant ouvrir YouTube."
            )
        )

    def test_unnecessary_followup_is_detected(self):
        self.assertTrue(
            _looks_like_unnecessary_followup(
                "Would you like me to perform the search now?"
            )
        )

    def test_english_drift_is_detected(self):
        self.assertTrue(
            _looks_mostly_english(
                "I have opened Chrome for you. Would you like me to search now?"
            )
        )
        self.assertFalse(
            _looks_mostly_english(
                "J'ai ouvert Chrome. Je lance maintenant la recherche."
            )
        )

    def test_hidden_thinking_is_never_spoken(self):
        value = (
            "internal reasoning that must stay hidden</think>\n"
            "Bonjour, que puis-je faire pour vous ?"
        )
        self.assertEqual(
            _visible_text(value),
            "Bonjour, que puis-je faire pour vous ?",
        )

    def test_regular_answer_is_preserved(self):
        self.assertEqual(
            _visible_text("Bonjour !"),
            "Bonjour !",
        )

    def test_ollama_native_loop_executes_multiple_tools_then_answers(self):
        tools = FakeTools()
        agent = FakeOllamaAgent(
            tools,
            [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "open_application",
                                    "arguments": {"name": "Chrome"},
                                }
                            },
                            {
                                "function": {
                                    "name": "search_web",
                                    "arguments": {"query": "agents IA"},
                                }
                            },
                        ],
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": "Chrome est ouvert et la recherche est lancée.",
                    }
                },
            ],
        )

        result = agent.run(
            "Ouvre Chrome et cherche les agents IA"
        )

        self.assertEqual(len(tools.calls), 2)
        self.assertEqual(len(result.actions), 2)
        self.assertIn("recherche", result.text)
        self.assertEqual(len(agent.payloads), 2)
        second_messages = agent.payloads[1]["messages"]
        self.assertTrue(any(m.get("role") == "tool" for m in second_messages))

    def test_ollama_conversation_context_survives_next_turn(self):
        tools = FakeTools()
        agent = FakeOllamaAgent(
            tools,
            [
                {"message": {"role": "assistant", "content": "Quel dossier ?"}},
                {"message": {"role": "assistant", "content": "Compris."}},
            ],
        )

        agent.run("Je veux ouvrir un dossier")
        agent.run("baristas")

        second_payload = agent.payloads[1]
        contents = [
            str(message.get("content", ""))
            for message in second_payload["messages"]
        ]
        self.assertTrue(
            any("Je veux ouvrir un dossier" in value for value in contents)
        )

    def test_openai_mcp_approval_can_resume_after_yes(self):
        tools = FakeTools()
        agent = FakeOpenAIAgent(
            tools,
            [
                {
                    "id": "resp_mcp_1",
                    "output": [
                        {
                            "id": "mcpr_test_1",
                            "type": "mcp_approval_request",
                            "arguments": "{\"query\":\"client\"}",
                            "name": "search_mail",
                            "server_label": "gmail",
                        }
                    ],
                },
                {
                    "id": "resp_mcp_2",
                    "output": [
                        {
                            "id": "mcp_call_1",
                            "type": "mcp_call",
                            "approval_request_id": "mcpr_test_1",
                            "arguments": "{\"query\":\"client\"}",
                            "error": None,
                            "name": "search_mail",
                            "output": "{\"messages\":[]}",
                            "server_label": "gmail",
                        },
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Aucun message trouvé.",
                                }
                            ],
                        },
                    ],
                },
            ],
        )

        first = agent.run("Cherche les messages du client")
        self.assertIn("autorisation", first.text.lower())

        second = agent.run("oui")
        self.assertEqual(second.text, "Aucun message trouvé.")
        self.assertEqual(
            agent.payloads[1]["input"][0]["type"],
            "mcp_approval_response",
        )
        self.assertTrue(agent.payloads[1]["input"][0]["approve"])
        self.assertEqual(
            agent.payloads[1]["previous_response_id"],
            "resp_mcp_1",
        )
        self.assertTrue(
            any(action.name == "mcp:gmail:search_mail" for action in second.actions)
        )

    def test_cerebras_secondary_failover_matches_quota_and_service_errors(self):
        self.assertTrue(
            CerebrasResponsesAgent._should_try_secondary(
                AgentRuntimeUnavailable("cerebras API error 429: quota")
            )
        )
        self.assertTrue(
            CerebrasResponsesAgent._should_try_secondary(
                AgentRuntimeUnavailable("cerebras API error 503")
            )
        )
        self.assertFalse(
            CerebrasResponsesAgent._should_try_secondary(
                AgentRuntimeUnavailable("cerebras API error 400: bad request")
            )
        )

    def test_cerebras_provider_uses_gpt_oss_and_no_groq_browser_tool(self):
        tools = FakeTools()
        agent = CerebrasResponsesAgent(tools)
        agent.api_key = "test"

        self.assertEqual(agent.provider_name, "cerebras")
        self.assertEqual(agent.model, "gpt-oss-120b")
        names = {
            item.get("type")
            for item in agent._tool_definitions()
        }
        self.assertNotIn("browser_search", names)

    def test_groq_provider_uses_gpt_oss_chat_tools(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "id": "resp_groq_1",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Bonjour.",
                                }
                            ],
                        }
                    ],
                }
            ],
        )
        result = agent.run("Bonjour")

        self.assertEqual(result.text, "Bonjour.")
        self.assertEqual(agent.provider_name, "groq")
        self.assertEqual(agent.model, "openai/gpt-oss-120b")
        tool_types = {
            item.get("type")
            for item in agent.payloads[0]["tools"]
        }
        self.assertIn("function", tool_types)
        self.assertEqual(agent.payloads[0]["tool_choice"], "auto")

    def test_groq_repairs_ms_football_turn_when_first_reply_has_no_tool(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "id": "resp_msf_1",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "Je suis là."}
                            ],
                        }
                    ],
                },
                {
                    "id": "resp_msf_2",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_msf_1",
                            "name": "msf_count_records",
                            "arguments": "{\"model\":\"Player\"}",
                        }
                    ],
                },
                {
                    "id": "resp_msf_3",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Il y a 264 joueurs.",
                                }
                            ],
                        }
                    ],
                },
            ],
        )

        result = agent.run("Combien de joueurs dans MS Football ?")

        self.assertEqual(result.text, "Il y a 264 joueurs.")
        self.assertEqual(tools.calls[0][0], "msf_count_records")
        self.assertTrue(agent.payloads[0]["ms_football_only"])
        self.assertEqual(agent.payloads[0]["tool_choice"], "auto")
        self.assertIn(
            "msf_count_records",
            agent.payloads[1]["msf_tool_names"],
        )

    def test_groq_leaves_msf_domain_after_clear_general_topic_switch(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_players",
                            "name": "msf_count_records",
                            "arguments": "{\"model\":\"Player\"}",
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "264 joueurs."}
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "D'accord, parlons du projet Atlas.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Tu veux y ajouter des paiements.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        agent.run("Combien de joueurs dans MS Football ?")
        agent.run("Je travaille maintenant sur le projet Atlas.")
        result = agent.run("Et je veux aussi ajouter des paiements.")

        self.assertEqual(result.text, "Tu veux y ajouter des paiements.")
        self.assertFalse(agent.payloads[2]["ms_football_only"])
        self.assertFalse(agent.payloads[3]["ms_football_only"])
        regular_tool_names = {
            item["function"]["name"]
            for item in agent.payloads[3]["tools"]
            if item.get("type") == "function"
        }
        self.assertNotIn("msf_count_records", regular_tool_names)
        self.assertNotIn("msf_capabilities", regular_tool_names)

    def test_groq_keeps_msf_domain_for_video_followup(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "id": "resp_1",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "msf_count_records",
                            "arguments": "{\"model\":\"Player\"}",
                        }
                    ],
                },
                {
                    "id": "resp_2",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "264 joueurs."}
                            ],
                        }
                    ],
                },
                {
                    "id": "resp_3",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_2",
                            "name": "msf_count_records",
                            "arguments": "{\"model\":\"Video\"}",
                        }
                    ],
                },
                {
                    "id": "resp_4",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "10 vidéos."}
                            ],
                        }
                    ],
                },
            ],
        )

        agent.run("Combien de joueurs dans MS Football ?")
        result = agent.run("Combien de vidéos sont en cours ?")

        self.assertEqual(result.text, "10 vidéos.")
        self.assertTrue(agent.payloads[2]["ms_football_only"])
        self.assertIn(
            "msf_count_records",
            agent.payloads[2]["msf_tool_names"],
        )

    def test_recent_msf_grounding_does_not_skip_new_followup_query(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_first",
                            "name": "msf_count_records",
                            "arguments": "{\"model\":\"Player\"}",
                        }
                    ],
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "264 joueurs."}
                            ],
                        }
                    ],
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": ""}
                            ],
                        }
                    ],
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_followup",
                            "name": "msf_count_records",
                            "arguments": "{\"model\":\"Video\"}",
                        }
                    ],
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "10 vidéos."}
                            ],
                        }
                    ],
                },
            ],
        )

        agent.run("Combien de joueurs dans MS Football ?")
        result = agent.run("Et combien de vidéos sont en cours ?")

        self.assertEqual(result.text, "10 vidéos.")
        self.assertEqual(tools.calls[-1][0], "msf_count_records")
        self.assertGreaterEqual(len(agent.payloads), 5)

    def test_groq_hides_msf_tools_from_regular_turns(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "id": "resp_regular_1",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "Bonjour."}
                            ],
                        }
                    ],
                }
            ],
        )

        agent.run("Bonjour")

        names = {
            item["function"]["name"]
            for item in agent.payloads[0]["tools"]
            if item.get("type") == "function"
        }
        self.assertNotIn("msf_capabilities", names)
        self.assertIn("open_application", names)

    def test_recent_project_name_matches_temporary_context(self):
        messages = [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": "Je travaille sur un projet qui s'appelle Atlas Scope.",
            },
            {"role": "assistant", "content": "D'accord."},
            {
                "role": "user",
                "content": "Donne-moi toutes les infos sur AtlasScope.",
            },
        ]

        self.assertTrue(
            _query_matches_recent_user_context("AtlasScope", messages)
        )
        self.assertFalse(
            _query_matches_recent_user_context("Bitcoin", messages)
        )

    def test_groq_keeps_project_context_across_fifteen_followups(self):
        agent = FakeGroqAgent(FakeTools(), [])
        messages = [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": "Mon projet de test s'appelle Atlas Scope.",
            },
            {"role": "assistant", "content": "Compris."},
        ]
        for index in range(15):
            messages.extend(
                [
                    {
                        "role": "user",
                        "content": f"Information suivante numéro {index}.",
                    },
                    {
                        "role": "assistant",
                        "content": f"Bien reçu {index}.",
                    },
                ]
            )
        agent._messages = messages

        agent._trim_history()

        user_contents = [
            item.get("content", "")
            for item in agent._messages
            if item.get("role") == "user"
        ]
        self.assertIn(
            "Mon projet de test s'appelle Atlas Scope.",
            user_contents,
        )
        self.assertEqual(len(user_contents), 16)

    def test_groq_blocks_web_search_for_recent_conversation_project(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "D'accord, parlons d'Atlas Scope.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_context_web",
                            "name": "search_web",
                            "arguments": "{\"query\":\"AtlasScope\"}",
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        "Atlas Scope est le projet dont nous "
                                        "parlons dans cette conversation."
                                    ),
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        agent.run("Je travaille sur un projet qui s'appelle Atlas Scope.")
        result = agent.run("Donne-moi toutes les infos sur AtlasScope.")

        self.assertNotIn(
            ("search_web", {"query": "AtlasScope"}),
            tools.calls,
        )
        self.assertEqual(len(result.actions), 1)
        self.assertFalse(result.actions[0].success)
        self.assertIn(
            "contextual_subject_web_search_not_requested",
            result.actions[0].detail,
        )

    def test_groq_allows_explicit_web_search_for_recent_project_name(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "D'accord."}
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_explicit_web",
                            "name": "search_web",
                            "arguments": "{\"query\":\"AtlasScope\"}",
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "La recherche web est ouverte.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        agent.run("Je travaille sur un projet qui s'appelle Atlas Scope.")
        result = agent.run("Recherche AtlasScope sur Internet.")

        self.assertIn(
            ("search_web", {"query": "AtlasScope"}),
            tools.calls,
        )
        self.assertEqual(len(result.actions), 1)
        self.assertTrue(result.actions[0].success)

    def test_groq_semantic_reset_tool_clears_temporary_context(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Nous parlons d'Atlas Nova.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_reset_context",
                            "name": "reset_conversation_context",
                            "arguments": "{}",
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Je n'ai plus ce contexte.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        agent.run("Je travaille sur Atlas Nova.")
        reset_result = agent.run(
            "Oublie ça, on repart de zéro sur un autre sujet."
        )

        self.assertEqual(
            reset_result.actions[0].name,
            "reset_conversation_context",
        )
        self.assertTrue(reset_result.actions[0].success)
        self.assertIn("repart de zéro", reset_result.text)
        self.assertEqual(len(agent._messages), 1)
        self.assertEqual(agent._messages[0]["role"], "system")

        agent.run("De quoi parlait-on avant ?")
        third_messages = agent.payloads[2]["messages"]
        self.assertFalse(
            any(
                "Atlas Nova" in str(item.get("content", ""))
                for item in third_messages
                if isinstance(item, dict)
            )
        )

    def test_groq_keeps_conversation_history_locally(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "id": "resp_groq_history_1",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Quel joueur ?",
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "resp_groq_history_2",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Compris.",
                                }
                            ],
                        }
                    ],
                },
            ],
        )

        agent.run("Je cherche un joueur")
        agent.run("Mohamed")

        second_input = agent.payloads[1]["messages"]
        self.assertTrue(
            any(
                item.get("role") == "user"
                and item.get("content") == "Je cherche un joueur"
                for item in second_input
                if isinstance(item, dict)
            )
        )
        self.assertTrue(
            any(
                item.get("role") == "user"
                and item.get("content") == "Mohamed"
                for item in second_input
                if isinstance(item, dict)
            )
        )

    def test_sensitive_local_function_waits_for_user_approval(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "id": "resp_change_1",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_change_1",
                            "name": "msf_commit_mutation",
                            "arguments": "{\"change_id\":\"abc123\"}",
                        }
                    ],
                },
                {
                    "id": "resp_change_2",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Modification confirmée.",
                                }
                            ],
                        }
                    ],
                },
            ],
        )

        first = agent.run("Applique la modification préparée")
        self.assertIn("confirmation", first.text.lower())
        self.assertEqual(tools.calls, [])

        second = agent.run("oui")
        self.assertEqual(second.text, "Modification confirmée.")
        self.assertEqual(tools.calls[0][0], "msf_commit_mutation")
        self.assertTrue(
            any(
                item.get("role") == "tool"
                and item.get("tool_call_id") == "call_change_1"
                for item in agent.payloads[1]["messages"]
                if isinstance(item, dict)
            )
        )

    def test_openai_loop_returns_function_result_then_continues(self):
        tools = FakeTools()
        agent = FakeOpenAIAgent(
            tools,
            [
                {
                    "id": "resp_1",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "open_application",
                            "arguments": "{\"name\":\"VLC Media Player\"}",
                        }
                    ],
                },
                {
                    "id": "resp_2",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "VLC est ouvert.",
                                }
                            ],
                        }
                    ],
                },
            ],
        )

        result = agent.run("Ouvre VLC Media Player")

        self.assertEqual(len(tools.calls), 1)
        self.assertEqual(result.text, "VLC est ouvert.")
        self.assertEqual(
            agent.payloads[1]["previous_response_id"],
            "resp_1",
        )
        self.assertEqual(
            agent.payloads[1]["input"][0]["type"],
            "function_call_output",
        )


if __name__ == "__main__":
    unittest.main()
