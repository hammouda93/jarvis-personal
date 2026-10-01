import copy
import unittest
from types import SimpleNamespace

from jarvis_agent.agent_runtime import (
    OllamaToolAgent,
    OpenAIResponsesAgent,
    GroqResponsesAgent,
    _looks_like_action_promise,
    _looks_like_unnecessary_followup,
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
