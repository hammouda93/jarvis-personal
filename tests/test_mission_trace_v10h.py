"""Offline tests for user-visible mission traces. No API, UI or SQLite."""
from __future__ import annotations

import json
import re
import unittest

from jarvis_agent.mission_trace_v10h import ConversationMissionTrace, requested_steps
from jarvis_agent.native_tools import AgentActionResult


def result(name, success=True, detail=""):
    return AgentActionResult(name=name, success=success, message="test", detail=detail)


class MissionTraceV10HTests(unittest.TestCase):
    def test_compound_goal_lists_requested_steps_without_inventing_success(self):
        goal = ("Ouvre un site dans Chrome, recherche un sujet, "
                "puis ouvre le premier résultat et vérifie sa page.")
        items = requested_steps(goal)
        self.assertEqual(len(items), 4)
        self.assertTrue(items[0].startswith("ouvre un site"))
        self.assertTrue(items[1].startswith("recherche"))
        self.assertTrue(items[2].startswith("ouvre le premier"))
        self.assertTrue(items[3].startswith("verifie"))

        events = []
        trace = ConversationMissionTrace(goal, events.append, trace_id="t1")
        trace.start()
        trace.ingest("[AGENT_TOOL] call=open_url args={}")
        trace.ingest("[AGENT_TOOL] result=open_url success=True detail='navigation_pending'")
        trace.finish((result("open_url", detail=json.dumps({
            "dispatched": True, "verified": False,
            "postcondition": "navigation_pending",
        })),))
        all_logs = "\n".join(events)
        self.assertIn("objective=", all_logs)
        self.assertEqual(all_logs.count("state=REQUESTED"), 4)
        self.assertIn("step_hint=1", all_logs)
        self.assertIn("DISPATCHED_UNVERIFIED", all_logs)
        self.assertIn("goal_verification=not_certified", all_logs)
        self.assertNotIn("state=COMPLETED", all_logs)

    def test_click_success_never_proves_installation(self):
        events = []
        trace = ConversationMissionTrace(
            "Installe un programme et vérifie sa présence",
            events.append, trace_id="installer",
        )
        trace.start()
        trace.ingest("[AGENT_TOOL] call=click_ui_element args={}")
        trace.ingest("[AGENT_TOOL] result=click_ui_element success=True detail='ok'")
        trace.finish((result("click_ui_element", detail='{"verified":true}'),))
        all_logs = "\n".join(events)
        self.assertIn("TOOL_EFFECT_VERIFIED", all_logs)
        self.assertIn("goal_proof=not_inferred", all_logs)
        self.assertIn("RESULTS_RECORDED_GOAL_UNVERIFIED", all_logs)

    def test_memory_and_window_task_keeps_both_visible(self):
        goal = ("Ouvre une application, vérifie la fenêtre, "
                "puis retrouve une information en mémoire.")
        events = []
        trace = ConversationMissionTrace(goal, events.append, trace_id="mixed")
        trace.start()
        self.assertEqual(len(trace.steps), 3)
        trace.ingest("[AGENT_TOOL] call=open_application args={}")
        trace.ingest("[AGENT_TOOL] call=inspect_active_window args={}")
        trace.ingest("[AGENT_TOOL] call=semantic_memory_search args={}")
        trace.finish([
            result("open_application", True),
            result("inspect_active_window", True),
            result("semantic_memory_search", False, "no matching evidence"),
        ])
        all_logs = "\n".join(events)
        self.assertIn("step_hint=3", all_logs)
        self.assertIn("effect=FAILED", all_logs)
        self.assertIn("goal_verification=not_certified", all_logs)

    def test_tool_names_do_not_make_completion_claims(self):
        events = []
        trace = ConversationMissionTrace("Ferme la fenêtre", events.append, trace_id="close")
        trace.start()
        trace.ingest("[AGENT_TOOL] call=close_window args={}")
        trace.finish([result("close_window", True, "Fermeture vérifiée.")])
        self.assertIn("TOOL_RETURNED_SUCCESS", "\n".join(events))
        self.assertNotIn("goal_verified=true", "\n".join(events))

    def test_followup_references_previous_goal_without_claiming_durable_mission(self):
        events = []
        previous = ConversationMissionTrace(
            "Ouvre une application et vérifie le résultat",
            events.append, trace_id="old",
        )
        previous.start()
        previous.finish([], error="AgentRuntimeUnavailable")
        follow = ConversationMissionTrace(
            "vas-y", events.append, previous=previous, trace_id="new",
        )
        follow.start()
        joined = "\n".join(events)
        self.assertIn("previous_trace=old", joined)
        self.assertIn("source=conversation_not_durable", joined)
        self.assertIn("previous_objective=", joined)

    def test_no_previous_trace_has_no_fake_resume(self):
        events = []
        trace = ConversationMissionTrace("oui", events.append, trace_id="one")
        trace.start()
        self.assertNotIn("state=FOLLOWUP", "\n".join(events))

    def test_quotes_and_negative_constraints_are_not_commands_in_outline(self):
        goal = 'Lis le texte "ouvre une fenêtre" mais ne ferme jamais la fenêtre.'
        outline = requested_steps(goal)
        self.assertFalse(any("ferme jamais" in x for x in outline))

    def test_error_finishes_trace_but_not_mission(self):
        events = []
        trace = ConversationMissionTrace("Ouvre un fichier", events.append, trace_id="err")
        trace.start()
        trace.finish([], error="AgentRuntimeUnavailable")
        trace.finish([], error="IgnoredSecondCall")
        self.assertEqual(sum("state=INTERRUPTED" in e for e in events), 1)
        self.assertIn("error_type=AgentRuntimeUnavailable", "\n".join(events))

    def test_no_network_calls_in_trace(self):
        import inspect
        import jarvis_agent.mission_trace_v10h as module
        source = inspect.getsource(module)
        for marker in ("openai.OpenAI(", "requests.get(", "urllib.request.", "httpx."):
            self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()
