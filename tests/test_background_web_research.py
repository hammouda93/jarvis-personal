import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from jarvis_agent.background_web_research import BackgroundWebResearch
from jarvis_agent.config import settings as real_settings


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class BackgroundWebResearchTests(unittest.TestCase):
    @patch(
        "jarvis_agent.background_web_research.settings",
        replace(
            real_settings,
            groq_api_key="test-key",
            groq_browser_search=True,
            groq_agent_model="openai/gpt-oss-120b",
        ),
    )
    @patch("jarvis_agent.background_web_research.urllib.request.urlopen")
    def test_groq_research_is_server_side_and_returns_evidence(
        self,
        urlopen_mock,
    ):
        urlopen_mock.return_value = _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Use the documented Windows AUMID route.",
                            "executed_tools": [
                                {
                                    "search_results": [
                                        {
                                            "title": "Microsoft documentation",
                                            "url": "https://learn.microsoft.com/example",
                                            "snippet": "AUMID details",
                                        }
                                    ]
                                }
                            ],
                        }
                    }
                ]
            }
        )

        result = BackgroundWebResearch().research(
            "How should a Windows MSIX app be launched by AUMID?"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider, "groq_browser_search")
        self.assertIn("AUMID", result.answer)
        self.assertTrue(result.evidence)

        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            payload["tools"],
            [{"type": "browser_search"}],
        )
        self.assertEqual(payload["tool_choice"], "required")

    @patch(
        "jarvis_agent.background_web_research.settings",
        replace(
            real_settings,
            groq_api_key="test-key",
            groq_browser_search=True,
            groq_agent_model="openai/gpt-oss-120b",
        ),
    )
    @patch("jarvis_agent.background_web_research.urllib.request.urlopen")
    def test_groq_responses_browser_search_is_primary_invisible_provider(
        self,
        urlopen_mock,
    ):
        urlopen_mock.return_value = _FakeHTTPResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Python 3.x is current.",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://www.python.org/downloads/",
                                        "title": "Python downloads",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        result = BackgroundWebResearch().research(
            "current version of Python"
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.provider,
            "groq_responses_browser_search",
        )
        self.assertIn("Python", result.answer)
        self.assertTrue(result.evidence)

        request = urlopen_mock.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/responses"))
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            payload["tools"],
            [{"type": "browser_search"}],
        )
        self.assertEqual(payload["tool_choice"], "required")

    @patch(
        "jarvis_agent.background_web_research.settings",
        replace(
            real_settings,
            groq_api_key="",
            groq_browser_search=True,
        ),
    )
    def test_missing_provider_fails_without_opening_user_browser(self):
        result = BackgroundWebResearch().research("current documentation")

        self.assertFalse(result.success)
        self.assertEqual(
            result.error,
            "no_background_research_provider_configured",
        )
        self.assertFalse(result.as_dict()["visible_browser_opened"])


if __name__ == "__main__":
    unittest.main()
