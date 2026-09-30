import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_agent.connectors import ConnectorRegistry, MCPConnector


class ConnectorRegistryTests(unittest.TestCase):
    def test_remote_mcp_tool_reads_token_from_environment(self):
        connector = MCPConnector.from_mapping(
            {
                "id": "github",
                "server_label": "github",
                "server_description": "GitHub MCP",
                "server_url": "https://example.com/mcp",
                "authorization_env": "TEST_MCP_TOKEN",
                "require_approval": "always",
                "allowed_tools": ["read_repository"],
            }
        )
        with patch.dict(os.environ, {"TEST_MCP_TOKEN": "secret-token"}):
            tool = connector.openai_tool()

        self.assertIsNotNone(tool)
        self.assertEqual(tool["type"], "mcp")
        self.assertEqual(tool["authorization"], "secret-token")
        self.assertEqual(tool["allowed_tools"], ["read_repository"])

    def test_authenticated_connector_is_hidden_when_token_missing(self):
        connector = MCPConnector.from_mapping(
            {
                "id": "mail",
                "server_label": "mail",
                "server_url": "https://example.com/mcp",
                "authorization_env": "MISSING_TEST_TOKEN",
            }
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MISSING_TEST_TOKEN", None)
            self.assertIsNone(connector.openai_tool())

    def test_registry_loads_local_json_without_inline_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "connectors.local.json"
            path.write_text(
                json.dumps(
                    {
                        "connectors": [
                            {
                                "id": "demo",
                                "server_label": "demo",
                                "server_url": "https://example.com/mcp",
                                "require_approval": "always",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            tools = ConnectorRegistry(path=path).openai_tools()

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["server_label"], "demo")
        self.assertNotIn("authorization", tools[0])

    def test_plain_http_remote_server_is_rejected(self):
        with self.assertRaises(ValueError):
            MCPConnector.from_mapping(
                {
                    "id": "bad",
                    "server_label": "bad",
                    "server_url": "http://example.com/mcp",
                }
            )


if __name__ == "__main__":
    unittest.main()
