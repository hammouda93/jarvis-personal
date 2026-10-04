import unittest
from unittest.mock import patch

from jarvis_agent.ui import NODE_BY_KEY, NODES, _safe_console_log, _tool_visual_node


class _CP1252Stream:
    encoding = "cp1252"

    def __init__(self):
        self.buffer = ""

    def write(self, value):
        value.encode(self.encoding, errors="strict")
        self.buffer += value
        return len(value)

    def flush(self):
        return None


class SafeConsoleLogTests(unittest.TestCase):
    def test_unicode_log_does_not_crash_cp1252_console(self):
        stream = _CP1252Stream()

        with patch("jarvis_agent.ui.sys.stdout", stream):
            _safe_console_log("Bonjour\u202fJarvis سلام")

        self.assertIn("Bonjour Jarvis", stream.buffer)
        self.assertIn("\\u", stream.buffer)


    def test_visual_route_maps_tools_without_changing_tool_behavior(self):
        self.assertEqual(_tool_visual_node("research_web"), "internet")
        self.assertEqual(_tool_visual_node("open_web_search"), "browser")
        self.assertEqual(_tool_visual_node("open_application"), "windows")
        self.assertEqual(_tool_visual_node("inspect_active_window"), "windows")
        self.assertEqual(_tool_visual_node("msf_query_records"), "ms_football")
        self.assertEqual(_tool_visual_node("recall_information"), "memory")

    def test_unknown_tool_stays_in_reasoning_visual_node(self):
        self.assertEqual(_tool_visual_node("future_generic_tool"), "think")

    def test_visual_nodes_have_unique_keys(self):
        keys = [node.key for node in NODES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_text_conversation_is_marked_as_future_visual_only(self):
        self.assertEqual(
            NODE_BY_KEY["conversation"].subtitle,
            "Bientôt disponible",
        )


if __name__ == "__main__":
    unittest.main()
