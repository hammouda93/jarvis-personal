import unittest
from dataclasses import replace
from unittest.mock import patch

from jarvis_agent.browser_adapter import BrowserAdapter
from jarvis_agent.config import settings as real_settings
from jarvis_agent.native_tools import NativeToolRegistry


class BrowserDomContractTests(unittest.TestCase):
    def test_invalid_cdp_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            BrowserAdapter("not-a-cdp-endpoint")

    def test_browser_tools_hidden_when_disabled(self):
        registry = NativeToolRegistry()
        with patch(
            "jarvis_agent.native_tools.settings",
            replace(real_settings, browser_enabled=False),
        ):
            names = {item["function"]["name"] for item in registry.ollama_tools()}
        self.assertNotIn("inspect_browser_page", names)

    def test_browser_tools_exposed_when_enabled(self):
        registry = NativeToolRegistry()
        with patch(
            "jarvis_agent.native_tools.settings",
            replace(
                real_settings,
                browser_enabled=True,
                browser_cdp_url="http://127.0.0.1:9222",
            ),
        ):
            names = {item["function"]["name"] for item in registry.ollama_tools()}
        for name in (
            "list_browser_pages",
            "inspect_browser_page",
            "write_browser_element",
            "click_browser_element",
            "press_browser_element",
        ):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
