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
        self.assertTrue(items[0].lower().startswith("ouvre un site"))
        self.assertTrue(items[1].startswith("recherche"))
        self.assertTrue(items[2].startswith("ouvre le premier"))
        self.assertIn(items[3].lower().split()[0], {"vérifie", "verifie"})

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

    def test_verified_navigation_correlates_actual_found_link_to_requested_step(self):
        events = []
        trace = ConversationMissionTrace(
            "Ouvre une page, inspecte le menu, puis clique sur Joueurs et vérifie la page.",
            events.append, trace_id="real",
        )
        trace.start()
        actions = [
            result("browser_find", detail=json.dumps({
                "matches": [{"name": "Joueurs",
                             "href": "https://example.test/players/"}],
                "unique": True, "scope": ["browser", 7],
            })),
            result("browser_click", detail=json.dumps({
                "verified": True, "postcondition": "navigation_observed",
                "post_observation": {"tab": {
                    "tab_id": 7, "url": "https://example.test/players/",
                    "title": "Tableau de bord des Joueurs",
                }},
            })),
        ]
        trace.finish(actions)
        joined = "\n".join(events)
        self.assertIn("state=EVIDENCE_CORRELATED", joined)
        self.assertIn("source=observed_href_and_verified_navigation", joined)
        self.assertIn("goal_verification=not_certified", joined)

    def test_unverified_navigation_and_unrelated_link_do_not_correlate(self):
        for verified, href in ((False, "https://example.test/players/"),
                               (True, "https://example.test/unrelated/")):
            events = []
            trace = ConversationMissionTrace(
                "Clique sur Joueurs", events.append, trace_id="guard",
            )
            trace.start()
            trace.finish([
                result("browser_find", detail=json.dumps({"matches": [
                    {"name": "Joueurs", "href": href}]})),
                result("browser_click", detail=json.dumps({
                    "verified": verified, "postcondition": "navigation_observed",
                    "post_observation": {"tab": {
                        "url": "https://example.test/players/"}}})),
            ])
            self.assertNotIn("EVIDENCE_CORRELATED", "\n".join(events))

    def test_anaphoric_conditional_step_correlates_observed_open_control(self):
        events = []
        trace = ConversationMissionTrace(
            "Ouvre un site, inspecte le panneau latéral, puis ouvre-le si nécessaire.",
            events.append, trace_id="conditional",
        )
        trace.start()
        trace.finish([result("browser_click", detail=json.dumps({
            "dispatched": True, "verified": False,
            "target_after": {"name": "Ouvrir le panneau"},
        }))])
        joined = "\n".join(events)
        self.assertIn("state=ACTIVITY_CORRELATED", joined)
        self.assertIn("goal_verification=not_certified", joined)
        self.assertNotIn("goal_verified=true", joined)

    def test_anaphoric_activity_does_not_guess_wrong_target(self):
        events = []
        trace = ConversationMissionTrace(
            "Inspecte la fenêtre, puis ouvre-le si nécessaire.",
            events.append, trace_id="not-guessed",
        )
        trace.start()
        trace.finish([result("browser_click", detail=json.dumps({
            "dispatched": True, "target_after": {"name": "Ouvrir les paramètres"},
        }))])
        self.assertNotIn("ACTIVITY_CORRELATED", "\n".join(events))

    def test_exact_clicked_ref_correlates_verified_redirect_without_guessing(self):
        events = []
        trace = ConversationMissionTrace(
            "Ouvre une page, puis clique sur Joueurs et vérifie le résultat.",
            events.append, trace_id="ref-matched",
        )
        trace.start()
        trace.ingest("[AGENT_TOOL] call=browser_find args={'tab_id': 7, 'text': 'Joueurs'}")
        trace.ingest("[AGENT_TOOL] call=browser_click args={'tab_id': 7, 'ref': 'observed-ref'}")
        trace.finish([
            result("browser_find", detail=json.dumps({
                "matches": [{"name": "Joueurs", "ref": "observed-ref",
                             "href": "https://example.test/intermediate"}],
                "unique": True,
            })),
            result("browser_click", detail=json.dumps({
                "verified": True, "postcondition": "navigation_observed",
                "post_observation": {"tab": {
                    "url": "https://example.test/redirected"}}
            })),
        ])
        self.assertIn("state=EVIDENCE_CORRELATED", "\n".join(events))
        self.assertIn("goal_verification=not_certified", "\n".join(events))

    def test_wrong_clicked_ref_cannot_correlate_even_with_same_href(self):
        events = []
        trace = ConversationMissionTrace(
            "Clique sur Joueurs", events.append, trace_id="ref-different",
        )
        trace.start()
        trace.ingest("[AGENT_TOOL] call=browser_find args={}")
        trace.ingest("[AGENT_TOOL] call=browser_click args={'ref':'different-ref'}")
        trace.finish([
            result("browser_find", detail=json.dumps({"matches": [
                {"name": "Joueurs", "ref": "good-ref",
                 "href": "https://example.test/players/"}]})),
            result("browser_click", detail=json.dumps({
                "verified": True, "postcondition": "navigation_observed",
                "post_observation": {"tab": {
                    "url": "https://example.test/players/"}}})),
        ])
        self.assertNotIn("EVIDENCE_CORRELATED", "\n".join(events))

    def test_only_trusted_runtime_completion_event_certifies_trace(self):
        events = []
        trace = ConversationMissionTrace(
            "Ouvre une page puis vérifie son état", events.append,
            trace_id="certified",
        )
        trace.start()
        trace.ingest("[AGENT_TOOL] call=browser_click args={}")
        trace.ingest("[MISSION] independent_goal_proof=browser_navigation")
        trace.finish([result("browser_click", detail='{"verified":true}')])
        self.assertIn("goal_verification=not_certified", "\n".join(events))
        fresh = []
        certified = ConversationMissionTrace(
            "Ouvre une page puis vérifie son état", fresh.append,
            trace_id="trusted",
        )
        certified.start()
        certified.ingest(
            "[MISSION] state=COMPLETED evidence=independent_browser_navigation"
        )
        certified.finish([result("browser_get_active_tab")])
        logs = "\n".join(fresh)
        self.assertIn("state=GOAL_CERTIFIED", logs)
        self.assertIn("state=COMPLETED", logs)
        self.assertIn("goal_verification=certified", logs)

    def test_certified_goal_marks_outline_as_covered_not_individual_step_proof(self):
        goal = ("Ouvre https://example.test/overview/, inspecte le menu latéral, "
                "ouvre-le si nécessaire, puis clique sur Joueurs. "
                "Vérifie la page affichée et donne-moi son URL exacte.")
        events = []
        trace = ConversationMissionTrace(goal, events.append, trace_id="coverage")
        trace.start()
        self.assertEqual(len(trace.steps), 4)
        trace.ingest(
            "[MISSION] state=COMPLETED evidence=independent_browser_navigation"
        )
        trace.finish([result("browser_click", detail='{"verified":true}')])
        joined = "\\n".join(events)
        self.assertEqual(joined.count("state=COVERED_BY_GOAL_CERTIFICATE"), 4)
        self.assertIn("goal_verification=certified", joined)
        self.assertIn("step_verified=not_independently_assessed", joined)
        self.assertIn("tool_evidence=not_assigned", joined)
        self.assertNotIn("state=NO_ASSIGNED_TOOL_EVIDENCE", joined)

    def test_untrusted_goal_claim_never_covers_outline_steps(self):
        events = []
        trace = ConversationMissionTrace(
            "Ouvre un site puis clique sur Joueurs et vérifie la page",
            events.append, trace_id="not-covered"
        )
        trace.start()
        trace.ingest("[MISSION] independent_goal_proof=browser_navigation")
        trace.finish([result("browser_click", detail='{"verified":true}')])
        joined = "\\n".join(events)
        self.assertIn("goal_verification=not_certified", joined)
        self.assertNotIn("state=COVERED_BY_GOAL_CERTIFICATE", joined)

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
        self.assertIn("state=OBSERVING tool=semantic_memory_search",
                      "\n".join(events))
        trace.finish([
            result("open_application", True),
            result("inspect_active_window", True),
            result("semantic_memory_search", False, "no matching evidence"),
        ])
        all_logs = "\n".join(events)
        self.assertIn("step_hint=3", all_logs)
        self.assertIn("effect=FAILED", all_logs)
        self.assertIn("goal_verification=not_certified", all_logs)

    def test_verification_phase_is_distinct_from_effect_and_goal(self):
        events = []
        trace = ConversationMissionTrace(
            "Vérifie qu'une fenêtre est ouverte", events.append, trace_id="verify",
        )
        trace.start()
        trace.ingest("[AGENT_TOOL] call=computer_verify args={}")
        self.assertIn("state=VERIFYING tool=computer_verify", "\n".join(events))
        trace.finish([result("computer_verify", detail='{"verified":true}')])
        self.assertIn("effect=TOOL_EFFECT_VERIFIED", "\n".join(events))
        self.assertIn("goal_verification=not_certified", "\n".join(events))

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
