import copy
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from jarvis_agent.config import settings as real_settings

from jarvis_agent.agent_runtime import (
    OllamaToolAgent,
    OpenAIResponsesAgent,
    GroqResponsesAgent,
    CerebrasResponsesAgent,
    AgentRuntimeUnavailable,
    _looks_like_action_promise,
    _looks_like_pseudo_tool_syntax,
    _looks_like_unnecessary_followup,
    _is_explicit_memory_write_request,
    _looks_like_memory_permission_prompt,
    _query_matches_recent_user_context,
    _looks_mostly_english,
    _visible_text,
    _actions_have_verified_proof,
    _looks_like_clear_operational_feedback,
    _requested_action_capabilities,
    _missing_requested_action_capabilities,
    _inspection_requests_visual_fallback,
)
from jarvis_agent.native_tools import AgentActionResult


class FakeKnowledge:
    def __init__(self):
        self.context = {"skills": [], "lessons": [], "app_profiles": []}
        self.runs = []

    def relevant_context(self, query, *, limit=3):
        return copy.deepcopy(self.context)

    def record_run(self, **kwargs):
        self.runs.append(copy.deepcopy(kwargs))


class FakeTools:
    def __init__(self):
        self.calls = []
        self.knowledge = FakeKnowledge()

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
                    "name": "research_web",
                    "description": "background research",
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
                    "name": "open_web_search",
                    "description": "visible browser search",
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
                    "name": "inspect_active_window",
                    "description": "inspect UI",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_ui_element",
                    "description": "write UI",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string"},
                            "text": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": ["replace", "append", "insert"],
                            },
                        },
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "type_text_active_window",
                    "description": "unverified focused typing",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "title": {"type": "string"},
                            "mode": {"type": "string"},
                        },
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "close_window",
                    "description": "close a top level window",
                    "parameters": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "close_tab",
                    "description": "close a tab without closing the window",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "observe_screen",
                    "description": "local visual observation",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "focus": {"type": "string"},
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "click_visual_target",
                    "description": "local visual target click",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "title": {"type": "string"},
                        },
                        "required": ["target"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_visual_target",
                    "description": "local visual target write",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "text": {"type": "string"},
                            "title": {"type": "string"},
                            "mode": {"type": "string"},
                        },
                        "required": ["target", "text"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_agent_knowledge",
                    "description": "search operational knowledge",
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
                    "name": "save_verified_skill",
                    "description": "save verified reusable skill",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "goal": {"type": "string"},
                            "procedure": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "success_checks": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "name", "goal", "procedure", "success_checks"
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "save_feedback_lesson",
                    "description": "save reusable feedback lesson",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "scope": {"type": "string"},
                            "pattern": {"type": "string"},
                            "rule": {"type": "string"},
                        },
                        "required": ["pattern", "rule"],
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

    def test_pseudo_tool_syntax_is_detected(self):
        self.assertTrue(
            _looks_like_pseudo_tool_syntax(
                'Je saisis le texte.{"type":"inspectactivewindow","title":"Bloc-notes"}'
            )
        )
        self.assertFalse(
            _looks_like_pseudo_tool_syntax(
                "Le Bloc-notes est ouvert et prêt."
            )
        )

    def test_groq_repairs_pseudo_tool_text_into_real_call(self):
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
                                    "text": (
                                        'Je le fais.{"type":"inspectactivewindow",'
                                        '"title":"Bloc-notes"}'
                                    ),
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect_real",
                            "name": "inspect_active_window",
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
                                    "text": "La fenêtre a été inspectée réellement.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Inspecte réellement le Bloc-notes.")

        self.assertIn(("inspect_active_window", {}), tools.calls)
        self.assertNotIn('{"type"', result.text)
        self.assertIn("réellement", result.text)

    def test_groq_skips_extra_inspection_when_write_self_verifies(self):
        class VerifiedWriteTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "write_ui_element":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="ok",
                        detail=(
                            '{"mode":"append","verified":true,'
                            '"before":"Bonjour Jarvis",'
                            '"value":"Bonjour Jarvis test"}'
                        ),
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = VerifiedWriteTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_write_verified",
                            "name": "write_ui_element",
                            "arguments": (
                                '{"ref":"e7","text":" test","mode":"append"}'
                            ),
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
                                    "text": "Le texte a été ajouté.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Ajoute test après Bonjour Jarvis.")

        self.assertEqual(
            tools.calls,
            [
                (
                    "write_ui_element",
                    {
                        "ref": "e7",
                        "text": " test",
                        "mode": "append",
                    },
                )
            ],
        )
        self.assertIn("ajouté", result.text)

    def test_groq_verifies_ui_after_write_before_concluding(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_write",
                            "name": "write_ui_element",
                            "arguments": (
                                '{"ref":"e1","text":"bonjour Jarvis"}'
                            ),
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
                                    "text": "Le texte est saisi.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_verify",
                            "name": "inspect_active_window",
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
                                    "text": "Le texte est visible dans l'éditeur.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Écris bonjour Jarvis dans l'éditeur.")

        self.assertEqual(
            tools.calls[:2],
            [
                (
                    "write_ui_element",
                    {"ref": "e1", "text": "bonjour Jarvis"},
                ),
                ("inspect_active_window", {}),
            ],
        )
        self.assertIn("visible", result.text)

    def test_groq_baseline_hides_new_learning_and_vision_tools(self):
        tools = FakeTools()
        tools.knowledge.context = {
            "skills": [{"name": "should_not_be_injected"}],
            "lessons": [{"rule": "should_not_be_injected"}],
            "app_profiles": [{"display_name": "ShouldNotInject"}],
        }
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
                                    "text": "Réponse baseline.",
                                }
                            ],
                        }
                    ]
                }
            ],
        )

        definitions = {
            item["function"]["name"]
            for item in agent._tool_definitions()
            if item.get("type") == "function"
        }

        self.assertNotIn("observe_screen", definitions)
        self.assertNotIn("click_visual_target", definitions)
        self.assertNotIn("write_visual_target", definitions)
        self.assertNotIn("type_text_active_window", definitions)
        self.assertNotIn("search_agent_knowledge", definitions)
        self.assertNotIn("save_verified_skill", definitions)
        self.assertNotIn("save_feedback_lesson", definitions)

        result = agent.run("Ouvre le Bloc-notes.")

        self.assertEqual(result.text, "Réponse baseline.")
        self.assertFalse(
            any(
                item.get("role") == "system"
                and "CONNAISSANCE_OPERATIONNELLE_LOCALE"
                in str(item.get("content") or "")
                for item in agent.payloads[0]["messages"]
            )
        )

    def test_groq_repairs_write_goal_after_only_opening_application(self):
        class VerifiedRepairTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "write_ui_element":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="ok",
                        detail=(
                            '{"verified":true,'
                            '"value":"Bonjour Jarvis"}'
                        ),
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = VerifiedRepairTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_open_notepad",
                            "name": "open_application",
                            "arguments": '{"name":"Notepad"}',
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
                                    "text": "C'est fait.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect_notepad",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Bloc-notes"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_write_notepad",
                            "name": "write_ui_element",
                            "arguments": (
                                '{"ref":"e7","text":"Bonjour Jarvis",'
                                '"mode":"replace"}'
                            ),
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
                                    "text": "Le texte a été écrit.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Écris Bonjour Jarvis dans le Bloc-notes.")

        self.assertEqual(
            [name for name, _args in tools.calls],
            [
                "open_application",
                "inspect_active_window",
                "write_ui_element",
            ],
        )
        self.assertIn("écrit", result.text)

    def test_groq_unverified_write_does_not_satisfy_write_goal(self):
        class EventuallyVerifiedTools(FakeTools):
            def __init__(self):
                super().__init__()
                self.write_count = 0

            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "write_ui_element":
                    self.write_count += 1
                    if self.write_count == 1:
                        return AgentActionResult(
                            name=name,
                            success=True,
                            message="typed but not verified",
                            detail=(
                                '{"verified":false,'
                                '"before":"Rechercher",'
                                '"value":"Rechercher"}'
                            ),
                        )
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="verified",
                        detail=(
                            '{"verified":true,'
                            '"before":"",'
                            '"value":"Bonjour"}'
                        ),
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = EventuallyVerifiedTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect_first",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Application"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_write_wrong",
                            "name": "write_ui_element",
                            "arguments": (
                                '{"ref":"e17","text":"Bonjour",'
                                '"mode":"replace"}'
                            ),
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
                                    "text": "Le texte a été saisi.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect_retry",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Application"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_write_correct",
                            "name": "write_ui_element",
                            "arguments": (
                                '{"ref":"e18","text":"Bonjour",'
                                '"mode":"replace"}'
                            ),
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
                                    "text": "Le texte a été saisi et vérifié.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Écris Bonjour dans le champ message.")

        self.assertEqual(
            [name for name, _args in tools.calls],
            [
                "inspect_active_window",
                "write_ui_element",
                "inspect_active_window",
                "write_ui_element",
            ],
        )
        self.assertEqual(tools.write_count, 2)
        self.assertIn("vérifié", result.text)

    def test_groq_repairs_write_goal_for_stt_ecrivain_variant(self):
        class VerifiedRepairTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "write_ui_element":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="ok",
                        detail='{"verified":true,"value":"Bonjour Jarvis"}',
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = VerifiedRepairTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_open_notepad_stt",
                            "name": "open_application",
                            "arguments": '{"name":"Notepad"}',
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
                                    "text": "C'est fait.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect_notepad_stt",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Bloc-notes"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_write_notepad_stt",
                            "name": "write_ui_element",
                            "arguments": (
                                '{"ref":"e7","text":"Bonjour Jarvis",'
                                '"mode":"replace"}'
                            ),
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
                                    "text": "Le texte a été écrit.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run(
            "Ouvre bloc-notes et écrivain « Bonjour Jarvis »."
        )

        self.assertEqual(
            [name for name, _args in tools.calls],
            [
                "open_application",
                "inspect_active_window",
                "write_ui_element",
            ],
        )
        self.assertIn("écrit", result.text)

    def test_groq_blocks_window_close_when_user_requested_tab(self):
        class VerifiedTabTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "close_tab":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="ok",
                        detail=(
                            '{"target":"YouTube","verified":true}'
                        ),
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = VerifiedTabTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_wrong_close",
                            "name": "close_window",
                            "arguments": '{"title":"YouTube"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_close_tab",
                            "name": "close_tab",
                            "arguments": '{"name":"YouTube"}',
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
                                    "text": "L'onglet YouTube est fermé.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Ferme seulement l'onglet YouTube.")

        self.assertNotIn(
            ("close_window", {"title": "YouTube"}),
            tools.calls,
        )
        self.assertIn(
            ("close_tab", {"name": "YouTube"}),
            tools.calls,
        )
        self.assertEqual(
            result.actions[0].detail,
            "close_window_blocked_for_tab_request",
        )

    def test_groq_blocks_window_close_for_stt_anglais_variant(self):
        class VerifiedTabTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "close_tab":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="ok",
                        detail='{"target":"YouTube","verified":true}',
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = VerifiedTabTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_wrong_close_stt",
                            "name": "close_window",
                            "arguments": '{"title":"YouTube"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_close_tab_stt",
                            "name": "close_tab",
                            "arguments": '{"name":"YouTube"}',
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
                                    "text": "L'onglet YouTube est fermé.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Ferme seulement l'anglais YouTube.")

        self.assertNotIn(
            ("close_window", {"title": "YouTube"}),
            tools.calls,
        )
        self.assertIn(
            ("close_tab", {"name": "YouTube"}),
            tools.calls,
        )
        self.assertEqual(
            result.actions[0].detail,
            "close_window_blocked_for_tab_request",
        )

    def test_groq_blocked_close_triggers_dialog_inspection(self):
        class BlockedCloseTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "close_window":
                    return AgentActionResult(
                        name=name,
                        success=False,
                        message="La fermeture est bloquée.",
                        detail=(
                            "Une boîte de dialogue ou un état non enregistré "
                            "peut bloquer la fermeture."
                        ),
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = BlockedCloseTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_close",
                            "name": "close_window",
                            "arguments": '{"title":"Installation Cursor"}',
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
                                    "text": "Je ne peux pas fermer.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect_dialog",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Installation - Cursor (User)"}',
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
                                    "text": "La boîte de confirmation est visible.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Ferme l'installation de Cursor.")

        self.assertEqual(
            tools.calls,
            [
                ("close_window", {"title": "Installation Cursor"}),
                ("inspect_active_window", {}),
            ],
        )
        self.assertIn("confirmation", result.text)

    def test_groq_search_submission_reuses_current_ui_instead_of_reopening_site(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_wrong_open",
                            "name": "open_url",
                            "arguments": '{"url":"https://www.youtube.com"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"YouTube"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_search",
                            "name": "click_ui_element",
                            "arguments": '{"name":"Search"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_verify",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"YouTube"}',
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
                                    "text": "La recherche est lancée.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Lance la recherche.")

        self.assertNotIn(
            ("open_url", {"url": "https://www.youtube.com"}),
            tools.calls,
        )
        self.assertEqual(
            [name for name, _args in tools.calls],
            [
                "inspect_active_window",
                "click_ui_element",
                "inspect_active_window",
            ],
        )
        self.assertEqual(
            result.actions[0].detail,
            "open_url_blocked_for_search_submission",
        )

    def test_groq_requires_reinspection_between_ui_mutations(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_accept",
                            "name": "click_ui_element",
                            "arguments": '{"ref":"e4"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_next_stale",
                            "name": "click_ui_element",
                            "arguments": '{"ref":"e6"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_refresh",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Installation Cursor"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_next_fresh",
                            "name": "click_ui_element",
                            "arguments": '{"ref":"e6"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_verify",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Installation Cursor"}',
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
                                    "text": "Étape suivante ouverte.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run(
            "Accepte l'option puis clique sur Suivant dans l'installation."
        )

        self.assertEqual(
            [name for name, _args in tools.calls],
            [
                "click_ui_element",
                "inspect_active_window",
                "click_ui_element",
                "inspect_active_window",
            ],
        )
        self.assertTrue(
            any(
                action.detail == "ui_action_blocked_until_reinspection"
                for action in result.actions
            )
        )

    def test_groq_resumes_deferred_ui_action_after_fresh_inspection(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_accept",
                            "name": "click_ui_element",
                            "arguments": '{"ref":"e4"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_stale_next",
                            "name": "click_ui_element",
                            "arguments": '{"ref":"e6"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_refresh",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Installation Cursor"}',
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
                                    "text": "Le bouton Suivant est maintenant actif.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_fresh_next",
                            "name": "click_ui_element",
                            "arguments": '{"name":"Suivant","control_type":"Button"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_verify",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Installation Cursor"}',
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
                                    "text": "L'installation est passée à l'étape suivante.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run(
            "Accepte le contrat puis continue vers l'étape suivante."
        )

        self.assertEqual(
            [name for name, _args in tools.calls],
            [
                "click_ui_element",
                "inspect_active_window",
                "click_ui_element",
                "inspect_active_window",
            ],
        )
        self.assertTrue(
            any(
                action.detail == "ui_action_blocked_until_reinspection"
                for action in result.actions
            )
        )
        self.assertIn("étape suivante", result.text)

    def test_compact_inspection_preserves_capability_refs(self):
        controls = [
            {
                "ref": f"e{index}",
                "type": "Button",
                "enabled": True,
                "name": f"Button {index}",
                "bounds": [0, index * 10, 100, index * 10 + 8],
            }
            for index in range(1, 30)
        ]
        controls.append(
            {
                "ref": "e30",
                "type": "Edit",
                "enabled": True,
                "writable": True,
                "label": "Nom du fichier :",
                "bounds": [100, 700, 800, 735],
            }
        )
        payload = {
            "window": {"title": "Enregistrer sous"},
            "controls": controls,
            "capabilities": {
                "writable": [
                    {"ref": "e30", "label": "Nom du fichier :"}
                ],
                "actionable": [
                    {"ref": "e29", "label": "Enregistrer"}
                ],
            },
            "snapshot": {
                "total_interactive": 30,
                "selected_interactive": 30,
                "truncated": False,
                "has_document_region": False,
                "vision_recommended": False,
            },
        }
        result = AgentActionResult(
            name="inspect_active_window",
            success=True,
            message="ok",
            detail=json.dumps(payload, ensure_ascii=False),
        )

        compact = json.loads(
            GroqResponsesAgent._compact_tool_content(
                "inspect_active_window",
                result,
            )
        )
        detail = json.loads(compact["detail"])

        self.assertIn(
            {"ref": "e30", "label": "Nom du fichier :"},
            detail["capabilities"]["writable"],
        )
        self.assertTrue(
            any(
                item.get("ref") == "e30"
                for item in detail["controls"]
            )
        )

    def test_browser_compaction_prioritizes_content_before_navigation(self):
        controls = []
        for index in range(1, 31):
            controls.append(
                {
                    "ref": f"bobs1:e{index}",
                    "type": "button",
                    "name": f"Navigation {index}",
                    "region": "navigation",
                    "actionable": True,
                    "enabled": True,
                }
            )
        controls.append(
            {
                "ref": "bobs1:e31",
                "type": "link",
                "name": "Messi first video result",
                "region": "content",
                "actionable": True,
                "enabled": True,
            }
        )
        payload = {
            "observation_id": "bobs1",
            "window": {
                "title": "messi - YouTube",
                "url": "https://www.youtube.com/results?search_query=messi",
            },
            "browser": True,
            "sensor": "dom",
            "controls": controls,
            "accessibility_tree": [
                {"role": "generic", "name": "noise"} for _ in range(180)
            ],
            "capabilities": {
                "writable": [],
                "actionable": [
                    {"ref": item["ref"], "label": item["name"]}
                    for item in controls
                ],
            },
            "snapshot": {"semantic_coverage": "usable"},
            "visible_text": ["Messi first video result", "Second result"],
        }
        result = AgentActionResult(
            name="inspect_browser_page",
            success=True,
            message="ok",
            detail=json.dumps(payload, ensure_ascii=False),
        )

        compact = json.loads(
            GroqResponsesAgent._compact_tool_content(
                "inspect_browser_page",
                result,
            )
        )
        detail = json.loads(compact["detail"])

        self.assertEqual(
            detail["visible_text"][0],
            "Messi first video result",
        )
        self.assertNotIn("accessibility_tree", detail)
        self.assertTrue(
            any(
                item.get("ref") == "bobs1:e31"
                for item in detail["controls"]
            )
        )
        self.assertTrue(
            any(
                item.get("ref") == "bobs1:e31"
                for item in detail["capabilities"]["actionable"]
            )
        )

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(
            real_settings,
            compatibility_baseline=False,
            vision_enabled=True,
            vision_actions_enabled=False,
            operational_learning_enabled=False,
            strict_proof_enabled=False,
            focused_typing_fallback_enabled=False,
        ),
    )
    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(
            real_settings,
            compatibility_baseline=False,
            vision_enabled=True,
            vision_actions_enabled=False,
        ),
    )
    def test_groq_tool_result_immediately_follows_tool_call_in_vision_mode(self):
        class InspectTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "inspect_active_window":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="inspection",
                        detail=(
                            '{"window":{"title":"Custom App"},'
                            '"controls":[],'
                            '"capabilities":{"writable":[],"actionable":[]},'
                            '"snapshot":{"semantic_coverage":"usable",'
                            '"vision_recommended":false}}'
                        ),
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        agent = FakeGroqAgent(
            InspectTools(),
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect_protocol",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Custom App"}',
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
                                    "text": "Inspection terminée.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Inspecte Custom App.")

        self.assertIn("terminée", result.text)
        messages = agent.payloads[1]["messages"]
        assistant_index = next(
            i
            for i, item in enumerate(messages)
            if item.get("role") == "assistant"
            and item.get("tool_calls")
        )
        self.assertEqual(
            messages[assistant_index + 1].get("role"),
            "tool",
        )
        self.assertEqual(
            messages[assistant_index + 1].get("tool_call_id"),
            "call_inspect_protocol",
        )

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(
            real_settings,
            compatibility_baseline=False,
            vision_enabled=True,
            vision_actions_enabled=False,
            operational_learning_enabled=False,
            strict_proof_enabled=False,
            focused_typing_fallback_enabled=False,
        ),
    )
    def test_groq_switches_to_visual_sensor_after_insufficient_inspection(self):
        class PerceptionTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "inspect_active_window":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="structured",
                        detail=json.dumps(
                            {
                                "window": {"title": "Opaque App"},
                                "controls": [
                                    {"ref": "obs1:e1", "type": "Button", "name": "Close"}
                                ],
                                "snapshot": {
                                    "semantic_coverage": "insufficient",
                                    "vision_recommended": True,
                                },
                            }
                        ),
                    )
                if name == "observe_screen":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="visual",
                        detail=json.dumps(
                            {
                                "window": {"title": "Opaque App"},
                                "sensor": "visual",
                                "observation_json": {
                                    "summary": "Search field visible",
                                    "targets": [
                                        {
                                            "label": "Search",
                                            "role": "search_input",
                                            "box_1000": [50, 100, 450, 180],
                                            "confidence": 0.94,
                                        }
                                    ],
                                },
                            }
                        ),
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = PerceptionTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "inspect1",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Opaque App"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "inspect2",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Opaque App"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "vision1",
                            "name": "observe_screen",
                            "arguments": (
                                '{"title":"Opaque App",'
                                '"focus":"Find the search field"}'
                            ),
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
                                    "text": "Je vois maintenant le champ de recherche.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Inspecte l'application et trouve le champ de recherche.")

        self.assertEqual(
            [name for name, _args in tools.calls],
            ["inspect_active_window", "observe_screen"],
        )
        self.assertTrue(
            any(
                action.detail == "structured_perception_insufficient_use_vision"
                for action in result.actions
            )
        )
        self.assertIn("champ de recherche", result.text)

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(
            real_settings,
            compatibility_baseline=False,
            vision_enabled=True,
            vision_actions_enabled=False,
            operational_learning_enabled=False,
            strict_proof_enabled=False,
            focused_typing_fallback_enabled=False,
        ),
    )
    def test_groq_vision_mode_exposes_observation_but_not_visual_click(self):
        agent = FakeGroqAgent(FakeTools(), [])
        definitions = {
            item["function"]["name"]
            for item in agent._tool_definitions()
            if item.get("type") == "function"
        }

        self.assertIn("observe_screen", definitions)
        self.assertNotIn("click_visual_target", definitions)
        self.assertNotIn("write_visual_target", definitions)
        self.assertNotIn("type_text_active_window", definitions)

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(
            real_settings,
            compatibility_baseline=False,
            vision_enabled=True,
            vision_actions_enabled=True,
            operational_learning_enabled=False,
            strict_proof_enabled=False,
            focused_typing_fallback_enabled=False,
        ),
    )
    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(
            real_settings,
            compatibility_baseline=False,
            vision_enabled=True,
            vision_actions_enabled=True,
            operational_learning_enabled=False,
            strict_proof_enabled=False,
            focused_typing_fallback_enabled=False,
        ),
    )
    def test_groq_visual_write_satisfies_write_goal_after_verification(self):
        class VisualWriteTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "write_visual_target":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="written",
                        detail='{"verified":false,"confidence":0.94}',
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = VisualWriteTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_visual_write",
                            "name": "write_visual_target",
                            "arguments": (
                                '{"target":"champ Nom du fichier",'
                                '"text":"jarvis_test.txt",'
                                '"title":"Enregistrer sous",'
                                '"mode":"replace"}'
                            ),
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
                                    "text": "Le nom a été saisi.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_verify_visual_write",
                            "name": "inspect_active_window",
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
                                    "text": "Le nom est visible dans le dialogue.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run(
            "Écris jarvis_test.txt dans le champ Nom du fichier."
        )

        self.assertEqual(
            [name for name, _args in tools.calls],
            ["write_visual_target", "inspect_active_window"],
        )
        self.assertIn("visible", result.text)

    def test_groq_visual_click_requires_after_state_verification(self):
        class VisualTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "click_visual_target":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="clicked",
                        detail='{"verified":false,"confidence":0.91}',
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = VisualTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_visual",
                            "name": "click_visual_target",
                            "arguments": (
                                '{"target":"bouton Enregistrer",'
                                '"title":"Enregistrer sous"}'
                            ),
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
                                    "text": "Le bouton a été cliqué.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_verify_visual",
                            "name": "inspect_active_window",
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
                                    "text": "Le nouvel état est visible.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Clique sur le bouton Enregistrer.")

        self.assertEqual(
            [name for name, _args in tools.calls],
            ["click_visual_target", "inspect_active_window"],
        )
        self.assertIn("visible", result.text)

    def test_verified_proof_requires_mutation_and_after_state(self):
        self.assertFalse(
            _actions_have_verified_proof(
                [
                    AgentActionResult(
                        "inspect_active_window",
                        True,
                        "ok",
                        "{}",
                    )
                ]
            )
        )
        self.assertTrue(
            _actions_have_verified_proof(
                [
                    AgentActionResult(
                        "click_ui_element",
                        True,
                        "ok",
                        "{}",
                    ),
                    AgentActionResult(
                        "observe_screen",
                        True,
                        "ok",
                        '{"observation":"new state visible"}',
                    ),
                ]
            )
        )
        self.assertFalse(
            _actions_have_verified_proof(
                [
                    AgentActionResult(
                        "write_ui_element",
                        True,
                        "ok",
                        '{"verified":true}',
                    ),
                    AgentActionResult(
                        "observe_screen",
                        False,
                        "timeout",
                        "timed out",
                    ),
                ]
            )
        )

    def test_clear_operational_feedback_detector(self):
        self.assertTrue(
            _looks_like_clear_operational_feedback(
                "Non, tu as juste recherché le contact, tu n'as pas envoyé le message."
            )
        )
        self.assertFalse(
            _looks_like_clear_operational_feedback(
                "Oui, maintenant c'est bon."
            )
        )

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(real_settings, operational_learning_enabled=True),
    )
    def test_groq_blocks_skill_learning_without_verified_proof(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_skill_blocked",
                            "name": "save_verified_skill",
                            "arguments": (
                                '{"name":"send_message","goal":"Send a message",'
                                '"procedure":["open conversation"],'
                                '"success_checks":["message visible"]}'
                            ),
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
                                    "text": "Je n'enregistre pas encore ce skill.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Envoie un message.")

        self.assertNotIn(
            ("save_verified_skill", {
                "name": "send_message",
                "goal": "Send a message",
                "procedure": ["open conversation"],
                "success_checks": ["message visible"],
            }),
            tools.calls,
        )
        self.assertEqual(
            result.actions[0].detail,
            "skill_write_blocked_without_verified_proof",
        )

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(real_settings, operational_learning_enabled=True),
    )
    def test_groq_allows_skill_learning_after_verified_ui_mutation(self):
        class VerifiedTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "write_ui_element":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="ok",
                        detail='{"verified":true,"mode":"append"}',
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = VerifiedTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_write_proof",
                            "name": "write_ui_element",
                            "arguments": (
                                '{"ref":"e1","text":" test","mode":"append"}'
                            ),
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_skill_allowed",
                            "name": "save_verified_skill",
                            "arguments": (
                                '{"name":"append_document_text",'
                                '"goal":"Append text without losing existing content",'
                                '"procedure":["inspect document","append text"],'
                                '"success_checks":["old and new text visible"]}'
                            ),
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
                                    "text": "C'est fait.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Ajoute du texte après le contenu existant.")

        self.assertTrue(
            any(name == "save_verified_skill" for name, _args in tools.calls)
        )
        self.assertTrue(
            any(action.name == "save_verified_skill" for action in result.actions)
        )

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(real_settings, operational_learning_enabled=True),
    )
    def test_groq_blocks_feedback_lesson_without_user_correction(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_lesson_blocked",
                            "name": "save_feedback_lesson",
                            "arguments": (
                                '{"scope":"ui","pattern":"confirmation",'
                                '"rule":"do not repeat"}'
                            ),
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
                                    "text": "Compris.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Oui, c'est bon.")

        self.assertEqual(
            result.actions[0].detail,
            "lesson_write_blocked_without_clear_feedback",
        )

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(real_settings, operational_learning_enabled=True),
    )
    def test_groq_injects_relevant_local_knowledge_ephemerally(self):
        tools = FakeTools()
        tools.knowledge.context = {
            "skills": [
                {
                    "name": "messaging_send_message",
                    "goal": "Send a message",
                    "procedure": ["open conversation", "verify send"],
                }
            ],
            "lessons": [],
            "app_profiles": [
                {
                    "display_name": "WhatsApp",
                    "aliases": ["WhatsApp Desktop"],
                    "launch_hint": r"C:\\Users\\private\\WhatsApp.lnk",
                    "window_title_patterns": ["Private chat - WhatsApp"],
                    "observed_capabilities": ["Edit", "Button"],
                    "confidence": 0.8,
                    "success_count": 2,
                    "failure_count": 0,
                }
            ],
        }
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
                                    "text": "Je vais utiliser la procédure locale.",
                                }
                            ],
                        }
                    ]
                }
            ],
        )

        agent.run("Envoie un message avec WhatsApp.")

        messages = agent.payloads[0]["messages"]
        self.assertTrue(
            any(
                item.get("role") == "system"
                and "CONNAISSANCE_OPERATIONNELLE_LOCALE"
                in str(item.get("content") or "")
                for item in messages
            )
        )
        self.assertFalse(
            any(
                item.get("role") == "system"
                and "CONNAISSANCE_OPERATIONNELLE_LOCALE"
                in str(item.get("content") or "")
                for item in agent._messages[1:]
            )
        )
        sent_payload = str(agent.payloads[0]["messages"])
        self.assertNotIn("launch_hint", sent_payload)
        self.assertNotIn("Private chat - WhatsApp", sent_payload)
        self.assertNotIn(r"C:\\Users\\private", sent_payload)

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(real_settings, operational_learning_enabled=True),
    )
    def test_groq_learning_checkpoint_saves_reusable_verified_workflow(self):
        class LearningTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "write_ui_element":
                    return AgentActionResult(
                        name=name,
                        success=True,
                        message="ok",
                        detail='{"verified":true,"mode":"append"}',
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="ok",
                    detail=str(arguments),
                )

        tools = LearningTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_open",
                            "name": "open_application",
                            "arguments": '{"name":"Notepad"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"Bloc-notes"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_write",
                            "name": "write_ui_element",
                            "arguments": (
                                '{"ref":"e7","text":" test","mode":"append"}'
                            ),
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
                                    "text": "Le texte est ajouté.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_learn",
                            "name": "save_verified_skill",
                            "arguments": (
                                '{"name":"edit_existing_document",'
                                '"goal":"Edit an existing text document safely",'
                                '"procedure":["inspect editor","choose writable document",'
                                '"append without deleting existing content"],'
                                '"success_checks":["old and new text are visible"]}'
                            ),
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
                                    "text": "Le texte a été ajouté.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run(
            "Ouvre le Bloc-notes et ajoute du texte au document existant."
        )

        self.assertTrue(
            any(name == "save_verified_skill" for name, _ in tools.calls)
        )
        self.assertIn("ajouté", result.text)

    @patch(
        "jarvis_agent.agent_runtime.settings",
        replace(real_settings, operational_learning_enabled=True),
    )
    def test_groq_feedback_checkpoint_saves_generic_lesson(self):
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
                                    "text": "Compris.",
                                }
                            ],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_feedback_lesson",
                            "name": "save_feedback_lesson",
                            "arguments": (
                                '{"scope":"messaging",'
                                '"pattern":"contact search confused with message composer",'
                                '"rule":"Open and verify the conversation before typing the message."}'
                            ),
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
                                    "text": "Compris, je corrigerai ce comportement.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run(
            "Non, tu as juste écrit dans la recherche de contacts, "
            "tu n'as pas écrit dans le champ message."
        )

        self.assertTrue(
            any(name == "save_feedback_lesson" for name, _ in tools.calls)
        )
        self.assertIn("corrigerai", result.text)

    def test_trusted_session_grounding_carries_opened_file_to_followup(self):
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
                                    "text": "Je poursuis l'installation.",
                                }
                            ],
                        }
                    ]
                }
            ],
        )
        opened_path = (
            r"C:\Users\salah\Downloads\CursorUserSetup-x64-3.22.12.exe"
        )
        agent._remember_session_grounding(
            "open_file",
            {"name": "Cursor", "within": "Téléchargements"},
            AgentActionResult(
                name="open_file",
                success=True,
                message="opened",
                detail=opened_path,
            ),
        )

        agent.run("Procède à l'installation.")

        system_prompt = agent.payloads[0]["messages"][0]["content"]
        self.assertIn("TRUSTED SESSION GROUNDING", system_prompt)
        self.assertIn(opened_path, system_prompt)
        self.assertIn("opened_file_request: Cursor", system_prompt)
        self.assertIn("Do not invent a replacement filename/path.", system_prompt)

    def test_reset_clears_trusted_session_grounding(self):
        agent = FakeGroqAgent(FakeTools(), [])
        agent._remember_session_grounding(
            "open_file",
            {"name": "Cursor"},
            AgentActionResult(
                name="open_file",
                success=True,
                message="opened",
                detail=r"C:\Downloads\CursorUserSetup.exe",
            ),
        )

        agent.reset()
        agent._refresh_session_grounding_prompt()

        self.assertEqual(agent._session_grounding, {})
        self.assertNotIn(
            "TRUSTED SESSION GROUNDING",
            agent._messages[0]["content"],
        )

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

    def test_persistent_recall_guard_ignores_prior_memory_write_request(self):
        messages = [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": "Mémorise que le film que je veux regarder est Arrival.",
            },
            {"role": "assistant", "content": "C'est mémorisé."},
            {
                "role": "user",
                "content": "Quel film voulais-je regarder ?",
            },
        ]

        self.assertFalse(
            _query_matches_recent_user_context("Arrival", messages)
        )

    def test_persistent_recall_guard_ignores_prior_recall_question(self):
        messages = [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": "Quel film voulais-je regarder ?",
            },
            {"role": "assistant", "content": "Je ne sais pas."},
            {
                "role": "user",
                "content": "Rappelle-moi le film mémorisé.",
            },
        ]

        self.assertFalse(
            _query_matches_recent_user_context("Arrival", messages)
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
        self.assertNotIn(
            "souhaitez-vous",
            str(agent._messages[-1].get("content", "")).lower(),
        )
        self.assertEqual(
            agent._messages[-1].get("content"),
            result.text,
        )

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

    def test_conceptual_addition_does_not_require_ui_write(self):
        self.assertNotIn(
            "write_ui",
            _requested_action_capabilities(
                "Je veux aussi ajouter des paiements au projet Atlas."
            ),
        )

    def test_adding_text_still_requires_ui_write(self):
        self.assertIn(
            "write_ui",
            _requested_action_capabilities(
                "Ajoute du texte dans le document."
            ),
        )

    def test_compound_open_and_search_requires_site_search_completion(self):
        self.assertIn(
            "site_search",
            _requested_action_capabilities(
                "Ouvre YouTube et recherche Messi."
            ),
        )
        self.assertIn(
            "site_search",
            _requested_action_capabilities(
                "Vas y recherche Messi."
            ),
        )

    def test_visible_search_url_satisfies_open_and_search_goal(self):
        actions = [
            AgentActionResult(
                name="open_url",
                success=True,
                message="opened",
                detail="https://www.google.com/search?q=python+documentation",
            )
        ]

        self.assertEqual(
            _missing_requested_action_capabilities(
                "Ouvre Chrome et cherche la documentation Python.",
                actions,
            ),
            set(),
        )

    def test_insufficient_structured_inspection_requests_visual_fallback(self):
        action = AgentActionResult(
            name="inspect_active_window",
            success=True,
            message="observed",
            detail=json.dumps(
                {
                    "snapshot": {
                        "semantic_coverage": "insufficient",
                        "vision_recommended": True,
                    }
                }
            ),
        )
        self.assertTrue(_inspection_requests_visual_fallback(action))

        usable = AgentActionResult(
            name="inspect_active_window",
            success=True,
            message="observed",
            detail=json.dumps(
                {
                    "snapshot": {
                        "semantic_coverage": "usable",
                        "vision_recommended": False,
                    }
                }
            ),
        )
        self.assertFalse(_inspection_requests_visual_fallback(usable))

    def test_insufficient_snapshot_skips_vision_when_write_target_is_available(self):
        action = AgentActionResult(
            name="inspect_active_window",
            success=True,
            message="observed",
            detail=json.dumps(
                {
                    "capabilities": {
                        "writable": [{"ref": "obs1:e7"}],
                        "actionable": [],
                    },
                    "snapshot": {
                        "semantic_coverage": "insufficient",
                        "vision_recommended": True,
                    },
                }
            ),
        )

        self.assertFalse(
            _inspection_requests_visual_fallback(
                action,
                "Écris TEST dans le document.",
            )
        )

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
                                    "name": "open_web_search",
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
                            "name": "research_web",
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
            ("research_web", {"query": "AtlasScope"}),
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
                            "name": "research_web",
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
        phases = []
        result = agent.run(
            "Recherche AtlasScope sur Internet.",
            phase=phases.append,
        )

        self.assertIn(
            ("research_web", {"query": "AtlasScope"}),
            tools.calls,
        )
        self.assertFalse(
            any(value.startswith("researching:") for value in phases)
        )
        self.assertEqual(len(result.actions), 1)
        self.assertTrue(result.actions[0].success)

    def test_groq_blocks_visible_search_when_user_did_not_request_browser(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_visible_search",
                            "name": "open_web_search",
                            "arguments": "{\"query\":\"Windows AUMID docs\"}",
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
                                    "text": "J'ai recherché la documentation.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run(
            "Trouve la meilleure méthode documentée pour lancer une app MSIX."
        )

        self.assertNotIn(
            ("open_web_search", {"query": "Windows AUMID docs"}),
            tools.calls,
        )
        self.assertEqual(len(result.actions), 1)
        self.assertFalse(result.actions[0].success)
        self.assertIn(
            "visible_web_search_not_explicit",
            result.actions[0].detail,
        )

    def test_groq_can_research_in_background_after_recoverable_failure(self):
        class RecoveryTools(FakeTools):
            def execute(self, name, arguments, *, approved=False):
                self.calls.append((name, arguments))
                if name == "open_application":
                    return AgentActionResult(
                        name=name,
                        success=False,
                        message="application not found",
                        detail="Application introuvable",
                    )
                return AgentActionResult(
                    name=name,
                    success=True,
                    message="research complete",
                    detail='{"provider":"groq_browser_search","visible_browser_opened":false}',
                )

        tools = RecoveryTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_open",
                            "name": "open_application",
                            "arguments": "{\"name\":\"Example App\"}",
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_research",
                            "name": "research_web",
                            "arguments": "{\"query\":\"Windows packaged app launch methods\"}",
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
                                    "text": "J'ai trouvé une méthode alternative documentée.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        phases = []
        result = agent.run(
            "Ouvre Example App.",
            phase=phases.append,
        )

        self.assertEqual(
            [name for name, _args in tools.calls],
            ["open_application", "research_web"],
        )
        research_phases = [
            value
            for value in phases
            if value.startswith("researching:")
        ]
        self.assertEqual(len(research_phases), 1)
        research_payload = json.loads(
            research_phases[0].split(":", 1)[1]
        )
        self.assertEqual(research_payload["reason"], "local_failure")
        self.assertIn(
            "Windows packaged app launch methods",
            research_payload["query"],
        )
        self.assertEqual(len(result.actions), 2)
        self.assertFalse(result.actions[0].success)
        self.assertTrue(result.actions[1].success)

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

    def test_result_selection_blocks_blind_enter_until_real_inspection(self):
        tools = FakeTools()
        agent = FakeGroqAgent(
            tools,
            [
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_enter",
                            "name": "press_key",
                            "arguments": '{"key":"Enter"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_inspect",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"YouTube"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_click",
                            "name": "click_ui_element",
                            "arguments": '{"ref":"e26"}',
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_verify",
                            "name": "inspect_active_window",
                            "arguments": '{"title":"YouTube"}',
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
                                    "text": "La première vidéo est ouverte.",
                                }
                            ],
                        }
                    ]
                },
            ],
        )

        result = agent.run("Ouvre la première vidéo.")

        self.assertNotIn(("press_key", {"key": "Enter"}), tools.calls)
        self.assertIn(
            ("inspect_active_window", {"title": "YouTube"}),
            tools.calls,
        )
        self.assertIn(("click_ui_element", {"ref": "e26"}), tools.calls)
        self.assertIn("ouverte", result.text)

    def test_direct_local_browser_action_is_recorded_without_model_call(self):
        agent = GroqResponsesAgent(FakeTools())
        before = len(agent._messages)

        agent.record_external_turn(
            "Recherche Messi",
            "Je recherche Messi sur YouTube.",
            action_name="browser.search",
            action_detail="https://www.youtube.com/results?search_query=messi",
            success=True,
        )

        self.assertEqual(len(agent._messages), before + 2)
        self.assertEqual(agent._messages[-2]["role"], "user")
        self.assertEqual(agent._messages[-2]["content"], "Recherche Messi")
        self.assertEqual(agent._messages[-1]["role"], "assistant")
        self.assertIn("[LOCAL_ACTION] browser.search success=1", agent._messages[-1]["content"])
        self.assertIn("youtube.com/results?search_query=messi", agent._messages[-1]["content"])

    def test_browser_selection_rule_requires_real_inspection_before_enter(self):
        from jarvis_agent.agent_runtime import _SYSTEM_INSTRUCTIONS

        self.assertIn(
            "N'utilise pas Enter comme substitut",
            _SYSTEM_INSTRUCTIONS,
        )
        self.assertIn(
            "inspecte une seconde fois la même fenêtre",
            _SYSTEM_INSTRUCTIONS,
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
