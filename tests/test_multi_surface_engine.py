"""Independent multi-window fixtures; the engine only sees sensor data and receipts."""
import json
import time
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from jarvis_agent.config import settings as real_settings
from jarvis_agent.computer_use_controller import ComputerUseController
from jarvis_agent.mission_surfaces import ExpectedSurfaceTransition, MissionSurfaces
from jarvis_agent.native_tools import AgentActionResult, NativeToolRegistry
from jarvis_agent.perception_router import PerceptionManager
from jarvis_agent.screen_vision import ScreenObservation, observe_screen, visual_action_guard
from jarvis_agent.ui_observation import SurfaceIdentity, UIEntity, UIObservation, object_detail
from jarvis_agent.ui_verifier import parse_conditions, verify_conditions
from jarvis_agent.windows_perception import UIActionResult, inspect_active_window


def condition(kind="value_equals", value="42", *, surface="", label="Notes", **extra):
    return {"kind": kind, "value": value, "label": label, "surface": surface, **extra}


class IndependentDesktop:
    def __init__(self):
        self.windows = {
            101: {"hwnd": 101, "pid": 42, "process_start": "p42", "title": "Notes", "bounds": [0, 0, 600, 400]},
            202: {"hwnd": 202, "pid": 43, "process_start": "p43", "title": "Notes", "bounds": [640, 0, 1240, 400]},
        }
        self.values = {101: "", 202: ""}
        self.status = {101: "En attente", 202: "En attente"}
        self.foreground = 101
        self.refs = {}
        self.sequence = 0
        self.dialog_kind = "owned"
        self.mutations = []

    def inventory(self):
        return [dict(x) for x in self.windows.values()]

    def activate(self, identity):
        if not identity.same_binding(SurfaceIdentity.from_payload(self.windows[int(identity.window_id)])):
            return UIActionResult(False, "identity changed", "stale")
        self.foreground = int(identity.window_id)
        return UIActionResult(True, "activated", json.dumps({"verified": True}))

    def observe(self, *, title=None, window_id=""):
        self.sequence += 1
        hwnd = int(window_id) if window_id else self.foreground
        if hwnd not in self.windows:
            return UIActionResult(False, "closed", "stale")
        self.refs.clear()
        if hwnd < 300:
            items = [("Document", "Notes", self.values[hwnd], True),
                     ("Button", "Enregistrer", None, False),
                     ("Heading", self.status[hwnd], None, False)]
        else:
            items = [("Button", "Confirmer", None, False), ("Heading", "Confirmation", None, False)]
        controls = []
        for index, (role, label, value, writable) in enumerate(items, 1):
            ref = f"obs{self.sequence}:e{index}"
            self.refs[ref] = (hwnd, label)
            controls.append({"ref": ref, "type": role, "name": label, "value": value,
                             "writable": writable, "actionable": writable or role == "Button",
                             "bounds": [0, index * 45, 180, index * 45 + 35]})
        return UIActionResult(True, "observed", json.dumps({
            "window": self.windows[hwnd], "controls": controls,
            "snapshot": {"semantic_coverage": "usable", "tree_complete": False}}))

    def act(self, name, args):
        hwnd, label = self.refs[args["ref"]]
        self.mutations.append((hwnd, name, label))
        if name == "write_ui_element":
            self.values[hwnd] = args["text"]
            return AgentActionResult(name, True, "exact readback", json.dumps({"verified": True}))
        if label == "Enregistrer":
            owner = hwnd if self.dialog_kind != "unrelated" else 999
            self.windows[303] = {"hwnd": 303, "pid": 42, "process_start": "p42", "title": "Confirmation",
                                 "bounds": [20, 30, 380, 330], "owner_hwnd": owner}
            if self.dialog_kind == "ambiguous":
                self.windows[404] = {**self.windows[303], "hwnd": 404}
        elif label == "Confirmer":
            owner = self.windows[hwnd]["owner_hwnd"]
            self.status[owner] = "Enregistré"
            del self.windows[hwnd]
        return AgentActionResult(name, True, "delivered", "{}")


class MultiSurfaceEngineTests(unittest.TestCase):
    def setUp(self):
        self.desktop = IndependentDesktop()
        self.controller = ComputerUseController(perception=PerceptionManager(structured=self.desktop.observe),
            surface_inventory=self.desktop.inventory, surface_activator=self.desktop.activate)
        self.controller.begin("Écris 42 dans les deux documents puis vérifie.")

    def execute(self, name, args=None):
        return self.controller.execute(name, args or {}, self.desktop.act)

    def bind(self, name, hwnd):
        self.assertTrue(self.execute("observe_ui", {"window_id": str(hwnd)}).success)
        self.assertTrue(self.execute("bind_ui_surface", {"name": name}).success)

    def two_goals(self):
        self.bind("source", 101)
        self.bind("destination", 202)
        self.assertTrue(self.execute("observe_ui", {"surface": "source"}).success)
        self.assertTrue(self.execute("define_ui_goal", {"conditions": [condition(surface="source"),
                                                                     condition(surface="destination")]}).success)

    def write(self, surface, value="42"):
        switched = self.execute("switch_ui_surface", {"surface": surface})
        self.assertTrue(object_detail(switched.detail)["verified"])
        return self.execute("act_ui", {"surface": surface, "operation": "write", "target": {"label": "Notes"},
                                         "text": value, "expected": [condition(surface=surface, value=value)]})

    def prepare_dialog(self):
        self.bind("editor", 101)
        goal = condition("text_present", "Enregistré", surface="editor", label="", role="content_title")
        self.assertTrue(self.execute("define_ui_goal", {"conditions": [goal], "future_surfaces": ["confirmation"]}).success)

    def open_dialog(self):
        return self.execute("act_ui", {"operation": "click", "target": {"label": "Enregistrer"},
            "expected_transition": {"kind": "owned_window", "surface": "confirmation"},
            "expected": [condition("target_present", "", surface="confirmation", label="Confirmer")]})

    def test_same_titles_do_not_merge_and_both_documents_must_be_verified(self):
        self.two_goals()
        self.assertTrue(object_detail(self.write("source").detail)["verified"])
        self.assertFalse(self.controller.summary()["goal_completed"])
        self.assertFalse(self.execute("verify_ui_goal").success)
        self.assertTrue(object_detail(self.write("destination").detail)["verified"])
        self.assertFalse(self.controller.summary()["goal_completed"])
        final = self.execute("verify_ui_goal")
        self.assertTrue(final.success, final.detail)
        self.assertEqual(self.desktop.values, {101: "42", 202: "42"})
        facts = object_detail(final.detail)["ui_verification"]["predicates"]
        self.assertEqual({x["surface"] for x in facts}, {"source", "destination"})
        self.assertEqual(len({x["surface_ref"] for x in facts}), 2)

    def test_final_sweep_detects_a_background_document_changed_again(self):
        self.two_goals()
        self.write("source")
        self.write("destination")
        self.desktop.values[101] = "changed by another actor"
        final = self.execute("verify_ui_goal")
        self.assertFalse(final.success)
        self.assertFalse(self.controller.summary()["goal_completed"])
        self.assertEqual(self.controller.goal_verdict.status, "failed")

    def test_surface_alias_cannot_be_rebound_to_same_title_or_pid_reuse(self):
        self.bind("source", 101)
        self.execute("observe_ui", {"window_id": "202"})
        result = self.execute("bind_ui_surface", {"name": "source"})
        self.assertFalse(result.success)
        self.assertIn("SURFACE_BINDING_IMMUTABLE", result.detail)
        self.desktop.windows[101]["process_start"] = "p42-reused"
        result = self.execute("observe_ui", {"surface": "source"})
        self.assertFalse(result.success)
        self.assertIn("WRONG_BOUND_SURFACE", result.detail)
        self.assertNotEqual(self.controller.state.current.generation, self.controller.state.generation)

    def test_unbound_or_undeclared_goal_surface_is_rejected_before_any_mutation(self):
        self.execute("observe_ui")
        result = self.execute("define_ui_goal", {"conditions": [condition(surface="imaginary")]})
        self.assertFalse(result.success)
        self.assertFalse(self.controller.goal)
        self.assertEqual(self.desktop.mutations, [])

    def test_new_alias_is_frozen_but_existing_alias_can_be_reread(self):
        self.two_goals()
        result = self.execute("bind_ui_surface", {"name": "replacement"})
        self.assertFalse(result.success)
        self.assertIn("SURFACE_BINDINGS_FROZEN", result.detail)
        self.assertTrue(self.execute("bind_ui_surface", {"name": "source"}).success)

    def test_action_on_another_bound_surface_requires_selection(self):
        self.two_goals()
        result = self.execute("act_ui", {"surface": "destination", "operation": "write", "target": {"label": "Notes"},
            "text": "42", "expected": [condition(surface="destination")]})
        self.assertFalse(result.success)
        self.assertIn("SURFACE_SELECTION_REQUIRED", result.detail)
        self.assertEqual(self.desktop.mutations, [])

    def test_action_postcondition_cannot_borrow_another_windows_value(self):
        self.two_goals()
        result = self.execute("act_ui", {"operation": "write", "target": {"label": "Notes"}, "text": "42",
                                          "expected": [condition(surface="destination")]})
        self.assertFalse(result.success)
        self.assertIn("POSTCONDITION_SURFACE_MISMATCH", result.detail)
        self.assertEqual(self.desktop.mutations, [])

    def test_owned_dialog_is_discovered_and_return_to_editor_is_explicit(self):
        self.prepare_dialog()
        opened = self.open_dialog()
        self.assertTrue(object_detail(opened.detail)["verified"], opened.detail)
        self.assertEqual(self.controller.surfaces.get("confirmation").identity.window_id, "303")
        confirmed = self.execute("act_ui", {"operation": "click", "surface": "confirmation",
            "target": {"label": "Confirmer"}, "expected_transition": {"kind": "bound_surface", "surface": "editor"},
            "expected": [condition("text_present", "Enregistré", surface="editor", label="", role="content_title")]})
        self.assertTrue(object_detail(confirmed.detail)["verified"], confirmed.detail)
        self.assertTrue(self.execute("verify_ui_goal").success)
        self.assertNotIn(303, self.desktop.windows)

    def test_same_pid_without_owner_does_not_prove_dialog(self):
        self.desktop.dialog_kind = "unrelated"
        self.prepare_dialog()
        opened = self.open_dialog()
        self.assertFalse(object_detail(opened.detail)["verified"])
        self.assertTrue(self.controller.pending_verification())
        self.assertNotIn("confirmation", self.controller.surfaces.bindings)
        self.assertFalse(self.execute("switch_ui_surface", {"surface": "editor"}).success)
        self.assertEqual(len(self.desktop.mutations), 1)

    def test_two_new_owned_dialogs_are_ambiguous_and_neither_is_clicked(self):
        self.desktop.dialog_kind = "ambiguous"
        self.prepare_dialog()
        opened = self.open_dialog()
        self.assertFalse(object_detail(opened.detail)["verified"])
        self.assertIn("AMBIGUOUS_SURFACE_TRANSITION", opened.detail)
        self.assertNotIn("confirmation", self.controller.surfaces.bindings)
        self.assertEqual(len(self.desktop.mutations), 1)

    def test_preexisting_owned_window_is_not_claimed_as_new_dialog(self):
        self.desktop.windows[303] = {"hwnd": 303, "pid": 42, "process_start": "p42", "title": "Confirmation",
                                     "bounds": [20, 30, 380, 330], "owner_hwnd": 101}
        self.prepare_dialog()
        opened = self.open_dialog()
        self.assertFalse(object_detail(opened.detail)["verified"])
        self.assertIn("EXPECTED_SURFACE_NOT_OBSERVED", opened.detail)
        self.assertTrue(self.controller.pending_verification())

    def test_transition_cannot_use_unscoped_expected_or_new_message(self):
        self.prepare_dialog()
        for expected in ([condition("target_present", "", label="Confirmer")],
                         [condition("new_text", "sent", surface="confirmation", label="", role="message")]):
            result = self.execute("act_ui", {"operation": "click", "target": {"label": "Enregistrer"},
                "expected_transition": {"kind": "owned_window", "surface": "confirmation"}, "expected": expected})
            self.assertFalse(result.success)
        self.assertEqual(self.desktop.mutations, [])

    def test_resume_preserves_bound_surfaces_and_new_mission_clears_them(self):
        self.two_goals()
        self.write("source")
        self.controller.begin("continuer", resume=True)
        self.assertEqual(set(self.controller.surfaces.bindings), {"source", "destination", "default"})
        self.assertTrue(self.controller.goal)
        self.controller.begin("Nouvelle mission")
        self.assertFalse(self.controller.surfaces.bindings)
        self.assertFalse(self.controller.state.verified_facts)
        self.assertFalse(self.controller.goal)

    def test_cancel_and_budget_apply_to_surface_switches(self):
        self.bind("source", 101)
        self.controller.cancel()
        self.assertFalse(self.execute("switch_ui_surface", {"surface": "source"}).success)
        self.controller.begin("Nouvelle mission")
        self.bind("source", 101)
        with patch("jarvis_agent.computer_use_controller.settings", replace(real_settings, ui_max_actions=0)):
            self.assertFalse(self.execute("switch_ui_surface", {"surface": "source"}).success)

    def test_goal_rejects_send_context_from_another_surface(self):
        self.bind("source", 101)
        self.bind("destination", 202)
        result = self.execute("define_ui_goal", {"conditions": [
            condition("text_present", "Salah", label="", role="content_title", surface="source"),
            condition("new_text", "bonjour", label="", role="message", surface="destination")]})
        self.assertFalse(result.success)
        self.assertIn("GOAL_CONTEXT_SURFACE_MISMATCH", result.detail)


class SurfaceContractTests(unittest.TestCase):
    def test_pinned_native_only_inspection_keeps_identity_for_vision_fallback(self):
        window = {"hwnd": 101, "handle": 101, "pid": 42, "process_start": "p42", "title": "Opaque",
                  "bounds": [0, 0, 600, 400]}
        vision = Mock(return_value=ScreenObservation(True, "pixels", json.dumps({**window,
            "captured_width": 600, "captured_height": 400,
            "observation_json": {"targets": [{"role": "search_input", "label": "Search", "confidence": .98,
                                                "box_1000": [20, 30, 500, 100]}]}})))
        with patch("jarvis_agent.windows_perception._native_window_by_id", return_value=window), \
             patch("jarvis_agent.windows_perception._native_window_is_minimized", return_value=False), \
             patch("jarvis_agent.windows_perception._uia_window_from_handle", side_effect=OSError("empty")), \
             patch("jarvis_agent.windows_perception._try_cua_inspection", return_value=None), \
             patch("jarvis_agent.perception_router.settings", replace(real_settings, vision_enabled=True)):
            manager = PerceptionManager(vision=vision)
            result = manager.perceive(window_id="101", target={"role": "search_input", "operation": "write"})
        self.assertTrue(result.success, result.detail)
        self.assertEqual(manager.state.current.scope.window_id, "101")
        self.assertEqual(manager.state.current.scope.pid, 42)
        self.assertEqual(manager.state.current.entities[0].sensor, "vision")
        self.assertEqual(vision.call_args.kwargs["window_id"], "101")

    def test_covered_native_window_is_not_interpreted_as_its_covering_window(self):
        gui = Mock()
        gui.IsIconic.return_value = False
        gui.GetForegroundWindow.return_value = 999
        with patch.dict("sys.modules", {"win32gui": gui}), \
             patch("jarvis_agent.screen_vision.settings", replace(real_settings, computer_use_enabled=True, vision_enabled=True)), \
             patch("jarvis_agent.windows_perception._native_window_by_id", return_value={"hwnd": 101, "title": "Opaque"}), \
             patch("PIL.ImageGrab.grab") as grab, patch("jarvis_agent.screen_vision.analyze_screen_bytes") as vlm:
            result = observe_screen(window_id="101")
        self.assertFalse(result.success)
        self.assertIn("BOUND_SURFACE_ACTIVATION_REQUIRED", result.detail)
        grab.assert_not_called()
        vlm.assert_not_called()

    def test_visual_action_is_rejected_when_focus_changed_since_capture(self):
        gui = Mock()
        gui.GetForegroundWindow.return_value = 202
        with patch.dict("sys.modules", {"win32gui": gui}), \
             patch("jarvis_agent.screen_vision.settings", replace(real_settings, computer_use_enabled=True)), \
             patch("jarvis_agent.windows_perception._native_window_by_id", return_value={"hwnd": 101}):
            self.assertFalse(visual_action_guard({"hwnd": 101, "pinned_window": True}))

    def test_negative_partial_tree_evidence_remains_inconclusive(self):
        registry = MissionSurfaces()
        before = UIObservation("a", SurfaceIdentity(window_id="1", pid=1))
        registry.bind("document", before)
        predicates = parse_conditions([{"kind": "target_absent", "label": "Error", "surface": "document"}])
        registry.freeze(predicates, before)
        registry.observe(replace(before, observation_id="b"))
        self.assertEqual(registry.verify(predicates, action_id="goal", max_age_s=30).status, "inconclusive")

    def test_stale_surface_cannot_reuse_its_earlier_success(self):
        registry = MissionSurfaces()
        before = UIObservation("a", SurfaceIdentity(window_id="1", pid=1),
                               (UIEntity("a:e1", "a:e1", "uia", "Edit", label="Notes", value="42"),))
        registry.bind("document", before)
        predicates = parse_conditions([condition(surface="document")])
        registry.freeze(predicates, before)
        registry.observe(replace(before, observation_id="b", monotonic_at=time.monotonic()-90))
        self.assertEqual(registry.verify(predicates, action_id="goal", max_age_s=30).reason, "STALE_SURFACE_EVIDENCE")

    def test_new_text_requires_a_matching_verified_action_even_when_visible(self):
        registry = MissionSurfaces()
        before = UIObservation("a", SurfaceIdentity(window_id="1", pid=1))
        registry.bind("conversation", before)
        predicates = parse_conditions([{"kind": "new_text", "value": "bonjour", "role": "message", "surface": "conversation"}])
        registry.freeze(predicates, before)
        after = replace(before, observation_id="b", entities=(UIEntity("b:e1", "", "uia", "message",
                                                                      ("message",), label="bonjour"),))
        registry.observe(after)
        self.assertEqual(registry.verify(predicates, action_id="goal", max_age_s=30).reason, "NEW_TEXT_ACTION_PROOF_REQUIRED")
        action = verify_conditions(predicates, before=before, after=after, action_id="send")
        registry.record_message_proof(predicates, action, before, after)
        self.assertTrue(registry.verify(predicates, action_id="goal", max_age_s=30).passed)
        different = parse_conditions([{"kind": "new_text", "value": "different", "role": "message", "surface": "conversation"}])
        self.assertFalse(registry.verify(different, action_id="goal", max_age_s=30).passed)

    def test_popup_uses_new_page_and_opener_not_title(self):
        source = SurfaceIdentity(page_ref="page_source", document_generation=2)
        transition = ExpectedSurfaceTransition.from_dict({"kind": "popup", "surface": "confirmation"}, source,
                                                          [{"page_ref": "page_source"}])
        registry = MissionSurfaces()
        registry.future.add("confirmation")
        items = [{"page_ref": "unrelated", "title": "Confirmation"},
                 {"page_ref": "new_page", "title": "Different title", "opener_page_ref": "page_source"}]
        self.assertEqual(registry.resolve_transition(transition, items).page_ref, "new_page")
        items.append({"page_ref": "second_page", "opener_page_ref": "page_source"})
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            registry.resolve_transition(transition, items)

    def test_title_only_surface_cannot_be_bound_for_cross_window_actions(self):
        with self.assertRaisesRegex(ValueError, "TOO_WEAK"):
            MissionSurfaces().bind("document", UIObservation("a", SurfaceIdentity(title="Notes")))

    def test_exact_native_inspection_never_falls_back_to_a_similar_title(self):
        with patch("jarvis_agent.windows_perception._native_window_by_id", side_effect=ValueError("closed")), \
             patch("jarvis_agent.windows_perception._window_by_title") as fuzzy:
            self.assertFalse(inspect_active_window(window_id="101", title="Notes").success)
        fuzzy.assert_not_called()

    def test_legacy_activation_hidden_only_on_experimental_path(self):
        with patch("jarvis_agent.native_tools.settings", replace(real_settings, computer_use_enabled=True)):
            names = {item["function"]["name"] for item in NativeToolRegistry().ollama_tools()}
            self.assertNotIn("activate_window", names)
            self.assertTrue({"list_ui_surfaces", "bind_ui_surface", "switch_ui_surface"} <= names)
        with patch("jarvis_agent.native_tools.settings", replace(real_settings, computer_use_enabled=False)):
            self.assertIn("activate_window", {item["function"]["name"] for item in NativeToolRegistry().ollama_tools()})


if __name__ == "__main__":
    unittest.main()
