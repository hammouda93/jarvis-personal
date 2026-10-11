from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

from jarvis_agent import windows_perception as win
from jarvis_agent.instruction_clauses import requires_empty_target


class WritePreconditionTests(unittest.TestCase):
    def test_empty_is_distinct_from_missing_value_or_a_display_label(self):
        empty = SimpleNamespace(get_value=lambda: "")
        unknown = SimpleNamespace(window_text=lambda: "Document")
        self.assertEqual(win._strict_control_value(empty), "")
        self.assertIsNone(win._strict_control_value(unknown))
        self.assertEqual(win._strict_control_value(SimpleNamespace(get_value=lambda: " ")), " ")

    def test_current_nonempty_value_blocks_before_write(self):
        target = Mock()
        target.get_value.return_value = "Existing content must stay"
        with patch.object(win, "_snapshot_element", return_value=target):
            result = win.write_ui_element("Document", "New content", ref="observed", precondition_value="")
        self.assertFalse(result.success)
        self.assertIn("precondition", result.detail)
        target.set_edit_text.assert_not_called()
        target.set_focus.assert_not_called()

    def test_unknown_value_is_not_treated_as_blank(self):
        target = SimpleNamespace()
        with patch.object(win, "_snapshot_element", return_value=target):
            result = win.write_ui_element("Document", "New content", ref="observed", precondition_value="")
        self.assertFalse(result.success)
        self.assertIn("unknown", result.detail)

    def test_positive_blank_constraint_and_negation_are_separate(self):
        self.assertTrue(requires_empty_target("Ouvre un document vide puis ecris le texte."))
        self.assertTrue(requires_empty_target("Write into an empty document."))
        self.assertFalse(requires_empty_target("Ne vide aucun document."))
        self.assertFalse(requires_empty_target('Explique "document vide".'))

    def test_computer_core_checks_fresh_value_not_cached_empty_snapshot(self):
        from test_foundations_v4 import ReplayDesktop
        from jarvis_agent.computer_grounding import ComputerGrounding
        backend = ReplayDesktop()
        backend.controls = [{"ref": "native", "name": "Editor", "type": "Edit", "writable": True,
            "bounds": [10, 10, 100, 60], "value": ""}]
        core = ComputerGrounding(backend)
        ref = core.observe("101")["elements"][0]["ref"]
        backend.read_value = lambda window_id, element: "Existing content after observation"
        with self.assertRaisesRegex(RuntimeError, "precondition"):
            core.act(ref, "write", text="Must not replace", precondition_value="")
        self.assertEqual(backend.actions, [])
        backend.read_value = lambda window_id, element: ""
        self.assertTrue(core.act(ref, "write", text="Allowed", precondition_value="")["verified"])

    def test_brain_enforces_blank_constraint_even_when_model_omits_precondition(self):
        from test_agent_runtime import FakeGroqAgent, FakeTools
        from jarvis_agent.native_tools import AgentActionResult
        tools = FakeTools()
        def execute(name, arguments):
            tools.calls.append((name, arguments))
            return AgentActionResult(name, True, "Fixture verified", json.dumps({"verified": True}))
        tools.execute = execute
        responses = [{"output": [{"type": "function_call", "call_id": "write1",
            "name": "write_ui_element", "arguments": '{"name":"Editor","text":"Fixture"}'}]},
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Controle effectue."}]}]}]
        agent = FakeGroqAgent(tools, responses)
        agent.run("Dans un document vide, ecris Fixture.")
        self.assertEqual(tools.calls[0][1]["precondition_value"], "")
