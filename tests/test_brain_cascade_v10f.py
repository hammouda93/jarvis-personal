"""Configurable brain extension: offline tests, no paid API calls."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from jarvis_agent.brain_cascade import (
    BrainConfigError, ExtraBrain, load_extra_brains, save_extra_brains,
)
from jarvis_agent.agent_runtime import (
    AgentRuntimeUnavailable, CerebrasResponsesAgent,
)
from jarvis_agent.config import settings
from test_agent_runtime import FakeTools
from test_provider_continuity_v10d import response


class BrainConfigTests(unittest.TestCase):
    def test_no_config_preserves_legacy_chain(self):
        with TemporaryDirectory() as td:
            self.assertEqual(load_extra_brains(Path(td) / "missing.json"), ())

    def test_additional_brains_preserve_order_and_never_store_secrets(self):
        a = ExtraBrain.parse({
            "id": "gemini-work", "provider": "gemini", "model": "chosen-model",
            "api_key_env": "MY_GEMINI_KEY",
        })
        b = ExtraBrain.parse({
            "id": "grok-work", "provider": "grok", "model": "chosen-model-2",
            "enabled": False,
        })
        with TemporaryDirectory() as td:
            path = Path(td) / "brain_config.json"
            save_extra_brains([a, b], path)
            self.assertEqual(load_extra_brains(path), (a, b))
            payload = path.read_text(encoding="utf8")
            self.assertNotIn("my-secret", payload)
            self.assertNotIn('"api_key":', payload)

    def test_reject_plaintext_keys_and_insecure_endpoints(self):
        with self.assertRaisesRegex(BrainConfigError, "plaintext"):
            ExtraBrain.parse({"id": "bad-key", "provider": "openai", "model": "x", "api_key": "secret"})
        with self.assertRaisesRegex(BrainConfigError, "https"):
            ExtraBrain.parse({"id": "bad-url", "provider": "custom", "model": "x",
                              "base_url": "http://third-party.example/v1"})
        with self.assertRaisesRegex(BrainConfigError, "https"):
            ExtraBrain.parse({"id": "bad-url", "provider": "custom", "model": "x",
                              "base_url": "https://user:password@third-party.example/v1"})

    def test_duplicate_identifiers_rejected(self):
        a = ExtraBrain.parse({"id": "gemini-work", "provider": "gemini", "model": "a"})
        with TemporaryDirectory() as td:
            with self.assertRaises(BrainConfigError):
                save_extra_brains([a, a], Path(td) / "config.json")

    def test_malformed_configuration_not_silently_accepted(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "brains.json"
            path.write_text('{"version":1,"brains":{"wrong":true}}', encoding="utf8")
            with self.assertRaises(BrainConfigError):
                load_extra_brains(path)


class BrainCascadeTests(unittest.TestCase):
    def _config(self):
        return replace(settings, cerebras_api_key="fixture-primary",
                       cerebras_secondary_api_key="fixture-secondary",
                       groq_api_key="fixture-groq", cerebras_fallback_to_groq=True)

    def test_primary_secondary_groq_then_extra_preserves_original_order(self):
        primary = Mock()
        secondary = Mock()
        extra = Mock()
        primary.chat.completions.create.side_effect = RuntimeError("402 payment_required")
        secondary.chat.completions.create.side_effect = RuntimeError("429 quota")
        extra.chat.completions.create.return_value = response(text="Answer from added brain")
        brain = ExtraBrain.parse({"id": "gemini-work", "provider": "gemini",
                                   "model": "test-model", "max_estimated_tokens": 100000})
        with (patch("jarvis_agent.agent_runtime.settings", self._config()),
              patch("jarvis_agent.brain_cascade.load_extra_brains", return_value=(brain,)),
              patch("jarvis_agent.agent_runtime.load_extra_brains", return_value=(brain,), create=True),
              patch("jarvis_agent.brain_cascade.resolved_api_key", return_value="fixture-extra"),
              patch("openai.OpenAI", return_value=extra)):
            agent = CerebrasResponsesAgent(FakeTools())
            with patch.object(agent, "_get_client", side_effect=lambda:
                              secondary if agent.api_key == "fixture-secondary" else primary):
                with patch.object(agent, "_chat_via_groq_fallback",
                                  side_effect=AgentRuntimeUnavailable("429 groq quota")) as groq:
                    result = agent.run("Bonjour")
        self.assertIn("Answer from added brain", result.text)
        self.assertEqual(primary.chat.completions.create.call_count, 1)
        self.assertEqual(secondary.chat.completions.create.call_count, 1)
        self.assertEqual(groq.call_count, 1)
        self.assertEqual(extra.chat.completions.create.call_count, 1)
        self.assertEqual(agent.last_effective_credential, "brain:gemini-work")

    def test_memory_scope_never_automatically_uploads_to_new_provider(self):
        brain = ExtraBrain.parse({"id": "grok-work", "provider": "grok", "model": "test"})
        with patch("jarvis_agent.brain_cascade.load_extra_brains", return_value=(brain,)):
            agent = CerebrasResponsesAgent(FakeTools())
            agent._memory_scope_active = True
            with self.assertRaisesRegex(AgentRuntimeUnavailable, "cloud_memory_scope"):
                agent._chat_via_configured_brains(tool_choice="auto",
                                                  ms_football_only=False, msf_tool_names=None)

    def test_disabled_extra_brain_does_not_receive_calls(self):
        brain = ExtraBrain.parse({"id": "disabled", "provider": "grok", "model": "test",
                                  "enabled": False})
        with patch("jarvis_agent.brain_cascade.load_extra_brains", return_value=(brain,)):
            agent = CerebrasResponsesAgent(FakeTools())
            with self.assertRaisesRegex(AgentRuntimeUnavailable, "no_configured"):
                agent._chat_via_configured_brains(tool_choice="auto",
                                                  ms_football_only=False, msf_tool_names=None)


if __name__ == "__main__":
    unittest.main()
