import contextlib
import json
import random
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvis_agent.config import settings as real_settings
from jarvis_agent.computer_use_controller import ComputerUseController, _action_conditions
from jarvis_agent.computer_use_runtime import ComputerUseRuntime, compact_ui_tool_detail
from jarvis_agent.event_journal import StructuredEventJournal
from jarvis_agent.native_tools import AgentActionResult, NativeToolRegistry
from jarvis_agent.perception_router import PerceptionManager
from jarvis_agent.screen_vision import ScreenObservation, visual_action_guard
from jarvis_agent.semantic_grounding import fuse_observation
from jarvis_agent.target_resolver import TargetIntent, resolve_target
from jarvis_agent.tracing_runtime import StructuredTracingRuntime, TracingToolRegistry
from jarvis_agent.ui_geometry import CaptureGeometry, normalized_box
from jarvis_agent.ui_observation import SurfaceIdentity, UIEntity, UIObservation
from jarvis_agent.ui_state import ProgressTracker, state_transition
from jarvis_agent.ui_verifier import Postcondition, parse_conditions, verify_conditions
from jarvis_agent.vision_providers import LocalOllamaVisionProvider, validated_localization
from jarvis_agent.windows_perception import UIActionResult


SCOPE = {"title": "Unknown surface", "hwnd": 101, "pid": 42, "bounds": [-1200, 50, 0, 950]}


def payload(controls=(), **extra):
    return {"window": dict(SCOPE), "controls": list(controls),
            "snapshot": {"semantic_coverage": "usable", "tree_complete": False}, **extra}


def control(ref="obs1:e1", role="Edit", label="Search", value="", **extra):
    return {"ref": ref, "type": role, "name": label, "value": value,
            "bounds": [-1150, 95, -800, 140], "writable": role == "Edit",
            "actionable": role in {"Edit", "Button", "ListItem"}, **extra}


def visual(**extra):
    return {**SCOPE, "captured_width": 1200, "captured_height": 900,
            "observation_json": {"targets": [{"role": "search_input", "label": "Search", "value": "",
                "box_1000": [40, 50, 335, 100], "confidence": .97}]}, **extra}


def observation(controls=(), id="obs", **extra):
    return fuse_observation(payload(controls, **extra), None, observation_id=id)


class ActionPostconditionContractTests(unittest.TestCase):
    def test_cursor_style_selected_postcondition_binds_to_resolved_target(self):
        target = UIEntity(
            ref="obs2:e4",
            native_ref="obs2:e4",
            sensor="uia",
            technical_role="RadioButton",
            semantic_roles=("radio_button",),
            label="Je comprends et j'accepte les termes du contrat de licence",
            selected=False,
            enabled=True,
        )

        conditions = _action_conditions(
            [{"kind": "selected", "value": "true"}],
            target,
        )

        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0].kind, "selected")
        self.assertEqual(conditions[0].label, target.label)

    def test_enabled_shorthand_is_expanded_without_weakening_target_scope(self):
        target = UIEntity(
            ref="obs2:e7",
            native_ref="obs2:e7",
            sensor="uia",
            technical_role="Button",
            semantic_roles=("button",),
            label="Suivant",
            enabled=True,
        )

        conditions = _action_conditions(
            [
                {
                    "kind": "target_present",
                    "role": "Button",
                    "label": "Suivant",
                    "enabled": "true",
                }
            ],
            target,
        )

        self.assertEqual([item.kind for item in conditions], ["target_present", "enabled"])
        self.assertEqual(conditions[1].label, "Suivant")
        self.assertEqual(conditions[1].value, "true")


class PerceptionContractTests(unittest.TestCase):
    def test_geometry_negative_monitor_origin_and_resize(self):
        geometry = CaptureGeometry((-1920, 100, 0, 1180), 960, 540)
        self.assertEqual(geometry.click_point([0, 0, 1000, 1000]), (-960, 640))

    def test_crop_transform_keeps_physical_offset(self):
        geometry = CaptureGeometry((-1920, 100, 0, 1180), 300, 200, (400, 200, 1000, 600))
        self.assertEqual(geometry.box_to_screen([0, 0, 1000, 1000]), (-1520, 300, -920, 700))
        self.assertEqual(geometry.click_point([0, 0, 1000, 1000]), (-1220, 500))

    def test_normalized_domain_must_be_explicit(self):
        g = CaptureGeometry((0, 0, 999, 999), 999, 999, normalized_domain=999)
        self.assertEqual(g.box_to_screen([0, 0, 999, 999]), (0, 0, 999, 999))
        self.assertNotEqual(CaptureGeometry((0, 0, 999, 999), 999, 999).box_to_screen([0, 0, 999, 999]),
                            (0, 0, 999, 999))

    def test_invalid_boxes_never_become_coordinates(self):
        for box in ([0, 0, 0, 1], [0, 0, float("nan"), 1], [True, 0, 1, 1],
                    [0, 0, 1001, 1], [0, 0, "42", 99], [-1, 0, 2, 2]):
            with self.subTest(box=box):
                self.assertIsNone(normalized_box(box))

    def test_false_string_and_nonfinite_confidence_are_rejected(self):
        for changes in ({"found": "false"}, {"confidence": float("nan")}, {"confidence": True}):
            self.assertIsNone(validated_localization({"found": True, "confidence": .99,
                "box_1000": [0, 0, 30, 20], **changes}))

    def test_same_object_keeps_uia_executor_and_visual_semantics(self):
        obs = fuse_observation(payload([control(label="")]), visual(), observation_id="obs1")
        self.assertEqual(len(obs.entities), 1)
        self.assertEqual(obs.entities[0].native_ref, "obs1:e1")
        self.assertEqual(obs.entities[0].sensor, "uia")
        self.assertIn("search_input", obs.entities[0].semantic_roles)
        self.assertEqual(len(obs.entities[0].evidence_ids), 2)

    def test_conflicting_window_is_never_fused(self):
        obs = fuse_observation(payload(), visual(hwnd=102), observation_id="obs1")
        self.assertFalse(obs.entities)
        self.assertIn("SENSOR_SCOPE_MISMATCH", obs.uncertainties)

    def test_vision_cannot_relabel_password_field_as_search(self):
        obs=fuse_observation(payload([control(label="Password")]),visual(),observation_id="obs1")
        self.assertIn("SENSOR_SEMANTIC_CONFLICT",obs.uncertainties)
        self.assertEqual(resolve_target(TargetIntent(role="search_input",operation="write"),obs).status,"not_found")
        self.assertFalse(obs.entities[0].visible)

    def test_moved_window_invalidates_sensor_cohort(self):
        obs = fuse_observation(payload(), visual(bounds=[-1100, 50, 100, 950]), observation_id="obs1")
        self.assertIn("SENSOR_GEOMETRY_CHANGED", obs.uncertainties)

    def test_driver_window_token_is_not_a_native_handle(self):
        identity = SurfaceIdentity.from_payload({"window": {"window_id": "driver:7", "hwnd": 101, "pid": 42}})
        self.assertEqual(identity.window_id, "101")

    def test_pid_reuse_is_not_the_same_surface(self):
        left = SurfaceIdentity(window_id="101", pid=42, process_start="old")
        self.assertFalse(left.same_surface(replace(left, process_start="new")))

    def test_browser_visible_text_is_preserved(self):
        obs = observation(visible_text=["Résultat accessible", "سلام"])
        self.assertEqual(obs.text_regions, ("Résultat accessible", "سلام"))

    def test_truncated_value_cannot_prove_exact_equality(self):
        obs = observation([control(value="preview", value_length=5000)])
        verdict = verify_conditions(parse_conditions([{"kind": "value_equals", "role": "search_input", "value": "preview"}]),
                                    before=None, after=obs)
        self.assertEqual(verdict.status, "inconclusive")

    def test_two_equal_targets_require_more_evidence(self):
        obs = observation([control(), control(ref="obs1:e2")])
        self.assertEqual(resolve_target(TargetIntent(role="search_input", operation="write"), obs).status, "ambiguous")

    def test_stale_ref_does_not_fall_back_to_similar_label(self):
        obs = observation([control()])
        self.assertEqual(resolve_target(TargetIntent(ref="obs0:e1", label="Search"), obs).status, "stale")

    def test_wrong_surface_ref_cannot_override_scope(self):
        obs = observation([control()])
        self.assertEqual(resolve_target(TargetIntent(ref="obs1:e1", within="Other window"), obs).status, "wrong_scope")

    def test_generation_and_age_gate_targets(self):
        obs = observation([control()])
        self.assertEqual(resolve_target(TargetIntent(ref="obs1:e1"), obs, generation=1).status, "stale")
        self.assertEqual(resolve_target(TargetIntent(ref="obs1:e1"), replace(obs, monotonic_at=time.monotonic()-100), max_age_s=3).status, "stale")

    def test_click_does_not_allow_read_only_text(self):
        obs = observation([control(role="Heading", label="Contact")])
        self.assertEqual(resolve_target(TargetIntent(role="content_title"), obs).status, "not_found")
        self.assertEqual(resolve_target(TargetIntent(role="content_title", operation="read"), obs).status, "resolved")

    def test_target_missing_in_otherwise_usable_tree_escalates(self):
        reader = Mock(return_value=UIActionResult(True, "ok", json.dumps(payload([control(role="Document", label="Notes")]))))
        vision = Mock(return_value=ScreenObservation(True, "ok", json.dumps(visual())))
        with patch("jarvis_agent.perception_router.settings", replace(real_settings, vision_enabled=True)):
            manager = PerceptionManager(structured=reader, vision=vision)
            result = manager.perceive(target={"role": "search_input", "operation": "write"})
        self.assertTrue(result.success)
        vision.assert_called_once()
        self.assertEqual(manager.state.current.coverage["mission_target"], "resolved")

    def test_exact_document_avoids_vision(self):
        reader = Mock(return_value=UIActionResult(True, "ok", json.dumps(payload([control(role="Document", label="Notes", writable=True)]))))
        vision = Mock()
        with patch("jarvis_agent.perception_router.settings", replace(real_settings, vision_enabled=True)):
            manager = PerceptionManager(structured=reader, vision=vision)
            manager.perceive(target={"role": "text_input", "operation": "write"})
        vision.assert_not_called()

    def test_uia_failure_can_use_vision(self):
        with patch("jarvis_agent.perception_router.settings", replace(real_settings, vision_enabled=True)):
            manager = PerceptionManager(structured=lambda **kw: UIActionResult(False, "UIA error", "no provider"),
                vision=lambda **kw: ScreenObservation(True, "ok", json.dumps(visual())))
            self.assertTrue(manager.perceive().success)
        self.assertTrue(manager.state.current.entities)

    def test_native_visual_execution_requires_capture_identity(self):
        with patch("jarvis_agent.screen_vision.settings", replace(real_settings, computer_use_enabled=True)):
            self.assertFalse(visual_action_guard(visual()))

    def test_moved_target_pixels_are_rejected_before_click(self):
        from PIL import Image
        from jarvis_agent.screen_vision import _CAPTURE_FRAMES
        metadata = {**visual(), "capture_id": "test", "box_1000": [0, 0, 100, 100], "monotonic_at": time.monotonic()}
        _CAPTURE_FRAMES["test"] = Image.new("RGB", (1200, 900), "white")
        self.addCleanup(_CAPTURE_FRAMES.pop, "test", None)
        with patch("jarvis_agent.screen_vision.settings", replace(real_settings, computer_use_enabled=True)), \
             patch("jarvis_agent.screen_vision._native_target_window", return_value=SCOPE), \
             patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (120, 90), "black")):
            self.assertFalse(visual_action_guard(metadata))


class SemanticTechnicalRoleProofTests(unittest.TestCase):
    def test_semantic_role_plus_technical_label_can_prove_exact_native_value(self):
        scope = SurfaceIdentity(
            title="Éditeur",
            window_id="100",
            pid=42,
        )
        entity = UIEntity(
            ref="proof:e1",
            native_ref="",
            sensor="uia",
            technical_role="Document",
            semantic_roles=("text_input",),
            label="bonjour jarvis",
            value="bonjour jarvis",
            writable=True,
            actionable=True,
            evidence_ids=("native:readback",),
        )
        after = UIObservation(
            "proof",
            scope,
            (entity,),
            coverage={"tree_complete": False},
        )
        condition = Postcondition(
            kind="value_equals",
            value="bonjour jarvis",
            role="text_input",
            label="Document",
        )

        verdict = verify_conditions(
            (condition,),
            before=None,
            after=after,
        )

        self.assertTrue(verdict.passed)
        self.assertEqual(
            verdict.predicates[0]["observed"],
            "bonjour jarvis",
        )

    def test_technical_label_alias_is_not_used_without_matching_role(self):
        scope = SurfaceIdentity(title="Éditeur", window_id="100", pid=42)
        entity = UIEntity(
            ref="proof:e1",
            native_ref="",
            sensor="uia",
            technical_role="Document",
            semantic_roles=("text_input",),
            label="contenu réel",
            value="bonjour",
        )
        after = UIObservation("proof", scope, (entity,), coverage={})
        condition = Postcondition(
            kind="value_equals",
            value="bonjour",
            label="Document",
        )

        verdict = verify_conditions((condition,), before=None, after=after)

        self.assertFalse(verdict.passed)


class PostconditionTests(unittest.TestCase):
    def test_arbitrary_inspection_is_not_goal_evidence(self):
        before = observation(id="a")
        after = observation(id="b")
        v = verify_conditions(parse_conditions([{"kind": "text_present", "value": "Done"}]), before=before, after=after)
        self.assertEqual(v.status, "inconclusive")

    def test_absence_in_partial_tree_is_unknown(self):
        self.assertEqual(verify_conditions(parse_conditions([{"kind": "target_absent", "label": "Dialog"}]),
            before=None, after=observation()).status, "inconclusive")

    def test_empty_input_does_not_prove_send(self):
        before = observation([control(label="Message", value="bonjour")], id="a")
        after = observation([control(label="Message", value="")], id="b")
        self.assertEqual(verify_conditions(parse_conditions([{"kind": "new_text", "role": "message", "value": "bonjour"}]),
            before=before, after=after).status, "inconclusive")

    def test_existing_text_does_not_prove_new_message(self):
        message = control(role="message", label="bonjour", writable=False)
        self.assertEqual(verify_conditions(parse_conditions([{"kind": "new_text", "role": "message", "value": "bonjour"}]),
            before=observation([message], id="a"), after=observation([message], id="b")).status, "inconclusive")

    def test_new_scoped_message_proves_transition(self):
        before = observation(id="a")
        after = observation([control(role="message", label="bonjour", region="conversation")], id="b")
        v = verify_conditions(parse_conditions([{"kind": "new_text", "role": "message", "value": "bonjour", "region": "conversation"}]),
            before=before, after=after, require_transition=True)
        self.assertTrue(v.passed)
        self.assertTrue(v.predicates[0]["evidence_ids"])

    def test_identical_state_does_not_prove_click(self):
        condition = parse_conditions([{"kind": "target_present", "label": "Search"}])
        self.assertEqual(verify_conditions(condition, before=observation([control()], id="a"),
            after=observation([control()], id="b"), require_transition=True).status, "inconclusive")

    def test_wrong_window_with_right_text_is_unsafe(self):
        after = observation([control(value="bonjour")], id="b", window={**SCOPE, "hwnd": 102})
        self.assertEqual(verify_conditions(parse_conditions([{"kind": "value_equals", "role": "search_input", "value": "bonjour"}]),
            before=observation(id="a"), after=after).status, "unsafe")

    def test_page_transition_requires_explicit_permission(self):
        before = UIObservation("a", SurfaceIdentity(page_ref="page1", document_generation=0, url="/step1"))
        after = UIObservation("b", replace(before.scope, document_generation=1, url="/step2"))
        conditions = parse_conditions([{"kind": "url_contains", "value": "step2"}])
        self.assertEqual(verify_conditions(conditions, before=before, after=after).status, "unsafe")
        self.assertTrue(verify_conditions(conditions, before=before, after=after, allow_document_transition=True).passed)
        self.assertEqual(verify_conditions(conditions, before=before, after=replace(after, scope=replace(after.scope, page_ref="page2")),
            allow_document_transition=True).status, "unsafe")

    def test_invalid_or_empty_predicates_are_rejected(self):
        for value in ([], [{"kind": "anything"}], [{"kind": "text_present", "value": ""}],
                      [{"kind": "focused"}], [{"kind": "text_present", "value": "Done", "hidden": True}]):
            with self.assertRaises(ValueError):
                parse_conditions(value)

    def test_temporal_state_reports_value_change_without_fake_disappearance(self):
        transition = state_transition(observation([control(value="")], id="a"),
                                      observation([control(ref="obs2:e1", value="Salah")], id="b"))
        self.assertTrue(any(x["kind"] == "value" for x in transition["changes"]))
        transition = state_transition(observation([control()], id="a"), observation(id="b"))
        self.assertFalse(any(x["kind"] == "disappeared" for x in transition["changes"]))


class UnknownSurface:
    """Independent fake application; the engine only sees observations and receipts."""
    def __init__(self, seed=1):
        self.sequence = 0
        self.search = ""
        self.contact = "Salah"
        self.opened = False
        self.composer = ""
        self.messages = []
        self.actions = []
        self.refs = {}
        self.order = random.Random(seed).sample(["search", "item", "title", "composer", "send", "message"], 6)

    def observe(self, **kwargs):
        self.sequence += 1
        items = []
        self.refs = {}
        for key in self.order:
            if key == "search":
                item = control(label="Recherche", value=self.search)
            elif key == "item" and self.search == self.contact and not self.opened:
                item = control(role="ListItem", label=self.contact)
            elif key == "title" and self.opened:
                item = control(role="Heading", label=self.contact, region="conversation")
            elif key == "composer" and self.opened:
                item = control(label="Message", value=self.composer, region="conversation")
            elif key == "send" and self.opened:
                item = control(role="Button", label="Envoyer", region="conversation")
            elif key == "message":
                for value in self.messages:
                    ref = f"obs{self.sequence}:e{len(items)+1}"
                    items.append(control(ref=ref, role="message", label=value, region="conversation"))
                continue
            else:
                continue
            ref = f"obs{self.sequence}:e{len(items)+1}"
            item["ref"] = ref
            self.refs[ref] = key
            items.append(item)
        return UIActionResult(True, "observed", json.dumps(payload(items, observation_id=f"obs{self.sequence}")))

    def execute(self, name, args):
        self.actions.append((name, dict(args)))
        key = self.refs[args["ref"]]
        if name == "write_ui_element":
            if key == "search": self.search = args["text"]
            elif key == "composer": self.composer = args["text"]
            return AgentActionResult(name, True, "write", '{"verified":true}')
        if key == "item": self.opened = True
        elif key == "send": self.messages.append(self.composer); self.composer = ""
        return AgentActionResult(name, True, "clicked", "{}")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        config = replace(real_settings, computer_use_enabled=True, vision_enabled=False, ui_max_actions=24,
                         ui_max_observations=36, ui_mission_timeout_s=240, ui_target_max_age_s=30)
        for module in ("computer_use_controller", "perception_router", "native_tools", "agent_runtime"):
            self.stack.enter_context(patch("jarvis_agent."+module+".settings", config))
        self.fixture = UnknownSurface()
        self.manager = PerceptionManager(structured=self.fixture.observe)
        self.controller = ComputerUseController(perception=self.manager)
        self.controller.begin("Cherche Salah et ouvre sa conversation")

    def call(self, name, **args):
        return self.controller.execute(name, args, self.fixture.execute)

    def start(self):
        self.assertTrue(self.call("observe_ui").success)
        self.assertTrue(self.call("define_ui_goal", conditions=[{"kind":"text_present", "role":"content_title", "value":"Salah"}]).success)

    def test_unknown_layout_multi_step_goal_is_verified_for_12_seeds(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                self.fixture = UnknownSurface(seed)
                self.manager = PerceptionManager(structured=self.fixture.observe)
                self.controller = ComputerUseController(perception=self.manager)
                self.controller.begin("Cherche Salah et ouvre sa conversation")
                self.start()
                written = self.call("act_ui", operation="write", target={"role":"search_input"}, text="Salah",
                    expected=[{"kind":"value_equals", "role":"search_input", "value":"Salah"}])
                self.assertTrue(json.loads(written.detail)["verified"])
                self.assertFalse(self.controller.summary()["goal_completed"])
                opened = self.call("act_ui", operation="click", target={"role":"list_item", "label":"Salah"},
                    expected=[{"kind":"text_present", "role":"content_title", "value":"Salah"}])
                self.assertTrue(json.loads(opened.detail)["verified"])
                self.assertTrue(self.fixture.opened)  # Independent application oracle.
                self.assertTrue(self.controller.summary()["goal_completed"])
                self.assertEqual(len(self.fixture.actions), 2)

    def test_exact_unicode_readback_does_not_reinspect_or_call_vision(self):
        self.controller.begin("Écris le texte exact dans le champ courant.")
        self.call("observe_ui")
        count = self.fixture.sequence
        text = "Bonjour éàç — مرحبا 😀"
        result = self.call("act_ui", operation="write", target={"role":"search_input"}, text=text,
            expected=[{"kind":"value_equals", "role":"search_input", "value":text}])
        self.assertTrue(json.loads(result.detail)["verified"])
        self.assertEqual(self.fixture.sequence, count)
        self.assertEqual(self.fixture.search, text)

    def test_goal_cannot_be_weakened_after_failure(self):
        self.start()
        result = self.call("define_ui_goal", conditions=[{"kind":"target_present", "role":"search_input"}])
        self.assertFalse(result.success)
        self.assertIn("GOAL_ALREADY_FROZEN", result.detail)

    def test_send_goal_requires_new_message_and_recipient(self):
        self.controller.begin("Envoie bonjour à Salah")
        self.call("observe_ui")
        for conditions in ([{"kind":"value_equals", "role":"message_composer", "value":""}],
                           [{"kind":"new_text", "role":"message", "value":"bonjour"}]):
            self.assertFalse(self.call("define_ui_goal", conditions=conditions).success)

    def test_send_requires_recipient_before_delivery_and_new_message_after(self):
        self.controller.begin("Envoie bonjour à Salah")
        self.fixture.opened = True
        self.call("observe_ui")
        self.assertTrue(self.call("define_ui_goal", conditions=[
            {"kind":"text_present", "role":"content_title", "value":"Salah"},
            {"kind":"new_text", "role":"message", "value":"bonjour", "region":"conversation"}]).success)
        self.call("act_ui", operation="write", target={"role":"message_composer"}, text="bonjour",
                  expected=[{"kind":"value_equals", "role":"message_composer", "value":"bonjour"}])
        result = self.call("act_ui", operation="click", target={"role":"send_button"},
                  expected=[{"kind":"new_text", "role":"message", "value":"bonjour", "region":"conversation"}])
        self.assertTrue(json.loads(result.detail)["verified"])
        self.assertEqual(self.fixture.messages, ["bonjour"])
        self.assertTrue(self.controller.summary()["goal_completed"])

    def test_inconclusive_action_is_not_repeated(self):
        self.start()
        self.call("act_ui", operation="write", target={"role":"search_input"}, text="Salah",
                  expected=[{"kind":"value_equals", "role":"search_input", "value":"Salah"}])
        result = self.call("act_ui", operation="click", target={"role":"list_item", "label":"Salah"},
                  expected=[{"kind":"text_present", "value":"Missing proof"}])
        self.assertFalse(json.loads(result.detail)["verified"])
        count = len(self.fixture.actions)
        retry = self.call("act_ui", operation="click", target={"role":"list_item", "label":"Salah"},
                  expected=[{"kind":"text_present", "value":"Missing proof"}])
        self.assertFalse(retry.success)
        self.assertEqual(len(self.fixture.actions), count)
        self.assertIn("PENDING_POSTCONDITION", retry.detail)

    def test_fresh_observation_can_finish_pending_verification(self):
        self.start()
        self.call("act_ui", operation="write", target={"role":"search_input"}, text="Salah",
                  expected=[{"kind":"text_present", "role":"content_title", "value":"Salah"}])
        self.assertTrue(self.controller.pending_verification())
        self.fixture.opened = True
        self.call("observe_ui")
        self.assertFalse(self.controller.pending_verification())

    def test_same_inspection_without_new_evidence_stops(self):
        for _ in range(3): self.assertTrue(self.call("observe_ui").success)
        result = self.call("observe_ui")
        self.assertFalse(result.success)
        self.assertIn("NO_NEW_EVIDENCE", result.detail)

    def test_action_budget_and_internal_observation_budget(self):
        self.controller.action_count = 24
        self.assertIn("UI_ACTION_BUDGET_EXHAUSTED", self.call("act_ui").detail)
        self.controller.observation_count = 36
        self.assertIn("UI_OBSERVATION_BUDGET_EXHAUSTED", self.controller._observe("observe_ui", {}).detail)

    def test_cancel_and_timeout_stop_without_actions(self):
        self.controller.cancel()
        self.assertIn("UI_MISSION_CANCELLED", self.call("observe_ui").detail)
        self.controller.begin("new")
        self.controller.started_at -= 1000
        self.assertIn("UI_MISSION_TIMEOUT", self.call("observe_ui").detail)
        self.assertFalse(self.fixture.actions)

    def test_legacy_blind_click_is_blocked_only_on_opt_in_engine(self):
        result = self.call("click_ui_element", ref="obs1:e1")
        self.assertFalse(result.success)
        self.assertIn("EXPLICIT_POSTCONDITION_REQUIRED", result.detail)
        self.assertFalse(self.fixture.actions)

    def test_stale_ref_is_rejected_and_semantic_target_can_be_reacquired(self):
        self.controller.begin("Écris Salah dans le champ courant.")
        self.call("observe_ui")
        old_ref = self.manager.state.current.entities[0].ref
        self.manager.state.invalidate()
        result = self.call("act_ui", operation="write", target={"ref":old_ref}, text="Salah",
                    expected=[{"kind":"value_equals", "role":"search_input", "value":"Salah"}])
        self.assertFalse(result.success)
        self.assertFalse(self.fixture.actions)
        result = self.call("act_ui", operation="write", target={"role":"search_input"}, text="Salah",
                    expected=[{"kind":"value_equals", "role":"search_input", "value":"Salah"}])
        self.assertTrue(json.loads(result.detail)["verified"])

    def test_runtime_rejects_model_success_without_goal_proof(self):
        class Delegate:
            def run(inner, text, **kwargs):
                action = self.call("observe_ui")
                return __import__("jarvis_agent.agent_runtime", fromlist=["AgentTurnResult"]).AgentTurnResult("Mission terminée", (action,))
        registry = NativeToolRegistry()
        registry._computer_use = self.controller
        result = ComputerUseRuntime(Delegate(), registry).run("Cherche Salah")
        self.assertFalse(result.goal_completed)
        self.assertEqual(result.mission_status, "inconclusive")
        self.assertNotIn("Mission terminée", result.text)

    def test_single_native_receipt_does_not_complete_a_compound_mission(self):
        from jarvis_agent.agent_runtime import AgentTurnResult
        registry=NativeToolRegistry()
        registry._computer_use=self.controller
        class Delegate:
            def run(inner,text,**kwargs):
                action=registry.execute("close_window",{"title":"Unknown surface"})
                return AgentTurnResult("Tout est terminé",(action,))
        with patch.object(registry,"_execute_legacy",return_value=AgentActionResult("close_window",True,"closed",'{"verified":true}')):
            result=ComputerUseRuntime(Delegate(),registry).run("Ferme la fenêtre et ouvre le navigateur")
        self.assertFalse(result.goal_completed)
        self.assertEqual(result.mission_status,"inconclusive")

    def test_acknowledgement_never_calls_planner_or_tools(self):
        registry = NativeToolRegistry()
        delegate = Mock()
        for text in ("Très bien", "Merci", "Parfait", "Thanks", "Très bien, merci."):
            result = ComputerUseRuntime(delegate, registry).run(text)
            self.assertEqual(result.actions, ())
        delegate.run.assert_not_called()
        self.assertIsNone(registry._computer_use)

    def test_tracing_records_evidence_and_does_not_turn_delivery_into_success(self):
        from jarvis_agent.agent_runtime import AgentTurnResult
        registry = NativeToolRegistry()
        registry._computer_use = self.controller
        with tempfile.TemporaryDirectory() as tmp:
            journal = StructuredEventJournal(Path(tmp)/"events.db")
            proxy = TracingToolRegistry(registry, journal=journal)
            class Delegate:
                def run(inner, text, **kwargs):
                    action = proxy.execute("observe_ui", {})
                    return AgentTurnResult("Terminé", (action,))
            result = StructuredTracingRuntime(ComputerUseRuntime(Delegate(), proxy), proxy).run("Ouvre Salah")
            mission = journal.recent_missions()[0]
            self.assertEqual(mission["status"], "blocked")
            events = journal.mission_trace(mission["mission_id"])
            self.assertIn("observation", [x["kind"] for x in events])
            self.assertIn("ui.state_transition", [x["kind"] for x in events])
            self.assertNotIn("mission.completed", [x["kind"] for x in events])
            self.assertFalse(result.goal_completed)

    def test_shadow_mirror_preserves_explicit_unverified_goal(self):
        from jarvis_agent.shadow_kernel_runtime import KernelShadowObserver
        from jarvis_agent.agent_runtime import AgentTurnResult
        with tempfile.TemporaryDirectory() as tmp:
            observer=KernelShadowObserver(base_dir=Path(tmp),owner_user_id="fixture-user",settings=real_settings)
            turn=AgentTurnResult("Non confirmé",(AgentActionResult("click_ui_element",True,"clicked","{}"),),goal_completed=False)
            mirrored=observer.observe_agent_turn("Cherche la cible",turn)
            self.assertFalse(mirrored.success)

    def test_schema_and_json_compaction_preserve_refs_and_proofs(self):
        registry = NativeToolRegistry()
        names = {x["function"]["name"] for x in registry.ollama_tools()}
        self.assertIn("act_ui", names)
        self.assertNotIn("click_visual_target", names)
        self.assertIn("write_ui_element", names)
        source = {"ui_observation": {"entities":[{"ref":f"obs1:e{i}", "value":"é"*500} for i in range(150)]},
                  "mission_state":{"goal_completed":False}, "ui_verification":{"status":"inconclusive", "action_id":"a"}}
        compacted = json.loads(compact_ui_tool_detail(json.dumps(source), max_chars=4000))
        self.assertEqual(compacted["ui_verification"]["action_id"], "a")
        self.assertFalse(compacted["mission_state"]["goal_completed"])


    def test_no_effect_guard_recognizes_same_semantics_with_new_refs(self):
        tracker = ProgressTracker()
        before = observation([control()], id="a")
        after = observation([control(ref="obs2:e1")], id="b")
        verdict = verify_conditions(parse_conditions([{"kind":"text_present", "value":"Missing"}]), before=before, after=after)
        for _ in range(2): tracker.record_action(semantic_signature="same intent", before=before, after=after, verdict=verdict)
        self.assertFalse(tracker.may_repeat(after, "same intent"))
        self.assertTrue(tracker.may_repeat(after, "different intent"))

    def test_cerebras_shared_runtime_completes_goal_and_keeps_valid_tool_json(self):
        from jarvis_agent.agent_runtime import CerebrasResponsesAgent
        registry=NativeToolRegistry()
        registry._computer_use=self.controller
        responses=[
            ("observe_ui",{}),
            ("define_ui_goal",{"conditions":[{"kind":"text_present","role":"content_title","value":"Salah"}]}),
            ("act_ui",{"operation":"write","target":{"role":"search_input"},"text":"Salah",
                       "expected":[{"kind":"value_equals","role":"search_input","value":"Salah"}]}),
            ("act_ui",{"operation":"click","target":{"role":"list_item","label":"Salah"},
                       "expected":[{"kind":"text_present","role":"content_title","value":"Salah"}]}),
            ("verify_ui_goal",{}),
            ("","Conversation ouverte."),
        ]
        class Planner(CerebrasResponsesAgent):
            def _chat(inner, **kwargs):
                name,args=responses.pop(0)
                calls=[SimpleNamespace(id="call"+str(len(responses)),function=SimpleNamespace(name=name,arguments=json.dumps(args)))] if name else []
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="" if name else args,tool_calls=calls))])
        planner=Planner(registry)
        planner.api_key="test-only"
        with patch.object(registry,"_execute_legacy",side_effect=lambda name,args,**kw:self.fixture.execute(name,args)):
            result=ComputerUseRuntime(planner,registry).run("Cherche Salah et ouvre sa conversation")
        self.assertTrue(result.goal_completed)
        self.assertTrue(self.fixture.opened)
        self.assertEqual(len(self.fixture.actions),2)
        for message in planner._messages:
            if message.get("role")=="tool":
                envelope=json.loads(message["content"])
                json.loads(envelope["detail"])

    def test_simple_native_write_finishes_without_cosmetic_model_round(self):
        from jarvis_agent.agent_runtime import CerebrasResponsesAgent

        fixture = NativeDocumentFixture("bonjour jarvis")
        manager = PerceptionManager(structured=fixture.observe)
        controller = ComputerUseController(perception=manager)
        registry = NativeToolRegistry()
        registry._computer_use = controller

        class Planner(CerebrasResponsesAgent):
            calls = 0

            def _chat(inner, **kwargs):
                inner.calls += 1
                if inner.calls == 1:
                    name, args = "inspect_active_window", {}
                elif inner.calls == 2:
                    name, args = (
                        "write_ui_element",
                        {
                            "ref": fixture.ref,
                            "text": " comment cv",
                            "mode": "append",
                        },
                    )
                else:
                    raise AssertionError(
                        "A verified native append must not trigger a cosmetic model round"
                    )
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call" + str(inner.calls),
                                        function=SimpleNamespace(
                                            name=name,
                                            arguments=json.dumps(args),
                                        ),
                                    )
                                ],
                            )
                        )
                    ]
                )

        planner = Planner(registry)
        planner.api_key = "test-only"
        with patch.object(
            registry,
            "_execute_legacy",
            side_effect=fixture.execute,
        ):
            result = ComputerUseRuntime(planner, registry).run(
                "Ajoutes à la fin comment cv"
            )

        self.assertTrue(result.goal_completed)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(fixture.value, "bonjour jarvis comment cv")
        self.assertIn("vérifiée", result.text)

    def test_verified_goal_finishes_without_cosmetic_extra_model_round(self):
        from jarvis_agent.agent_runtime import CerebrasResponsesAgent
        registry = NativeToolRegistry()
        registry._computer_use = self.controller
        responses = [
            ("observe_ui", {}),
            (
                "define_ui_goal",
                {
                    "conditions": [
                        {
                            "kind": "value_equals",
                            "role": "search_input",
                            "value": "Salah",
                        }
                    ]
                },
            ),
            (
                "act_ui",
                {
                    "operation": "write",
                    "target": {"role": "search_input"},
                    "text": "Salah",
                    "expected": [
                        {
                            "kind": "value_equals",
                            "role": "search_input",
                            "value": "Salah",
                        }
                    ],
                },
            ),
        ]

        class Planner(CerebrasResponsesAgent):
            calls = 0

            def _chat(inner, **kwargs):
                inner.calls += 1
                name, args = responses.pop(0)
                calls = [
                    SimpleNamespace(
                        id="call" + str(inner.calls),
                        function=SimpleNamespace(
                            name=name,
                            arguments=json.dumps(args),
                        ),
                    )
                ]
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=calls,
                            )
                        )
                    ]
                )

        planner = Planner(registry)
        planner.api_key = "test-only"
        with patch.object(
            registry,
            "_execute_legacy",
            side_effect=lambda name, args, **kw: self.fixture.execute(name, args),
        ):
            result = ComputerUseRuntime(planner, registry).run("Écris Salah dans la recherche.")

        self.assertTrue(result.goal_completed)
        self.assertEqual(planner.calls, 3)
        self.assertIn("vérifiée", result.text)

    def test_verified_goal_blocks_further_mutations(self):
        self.fixture.opened=True
        self.start()
        self.assertTrue(self.controller.summary()["goal_completed"] is False)
        self.call("verify_ui_goal")
        self.assertTrue(self.controller.summary()["goal_completed"])
        result=self.call("act_ui",operation="write",target={"role":"search_input"},text="Nour",
                    expected=[{"kind":"value_equals","role":"search_input","value":"Nour"}])
        self.assertIn("GOAL_ALREADY_COMPLETED",result.detail)
        self.assertFalse(self.fixture.actions)


class VisionProviderTests(unittest.TestCase):
    def test_remote_provider_is_not_accepted_when_local_only(self):
        with self.assertRaises(ValueError):
            LocalOllamaVisionProvider(endpoint="https://remote.example/api/chat", model="vision")

    def test_grounding_model_and_understanding_model_are_independent(self):
        provider = LocalOllamaVisionProvider(endpoint="http://127.0.0.1:11434/api/chat", model="understand", grounding_model="ground")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"message":{"content":"{}"}}'
        with patch("jarvis_agent.vision_providers.urllib.request.urlopen", return_value=response) as http:
            provider.request(b"image", prompt="see")
            self.assertEqual(json.loads(http.call_args.args[0].data)["model"], "understand")
            provider.request(b"image", prompt="ground", grounding=True)
            data = json.loads(http.call_args.args[0].data)
            self.assertEqual(data["model"], "ground")
            self.assertEqual(data["format"]["properties"]["found"], {"type":"boolean"})

    def test_target_grounder_reuses_image_and_rejects_disagreement(self):
        from jarvis_agent.screen_vision import analyze_screen_bytes
        first=json.dumps(visual()["observation_json"])
        probe={"found":True,"role":"search_input","label":"Search","box_1000":[700,700,950,950],"confidence":.99}
        config=replace(real_settings,vision_enabled=True,vision_grounding_enabled=True)
        with patch("jarvis_agent.screen_vision.settings",config), \
             patch("jarvis_agent.screen_vision._call_local_vision",side_effect=[(first,.1),(json.dumps(probe),.2)]) as sensor:
            result=analyze_screen_bytes(b"same-image",visual(),target={"role":"search_input"})
        self.assertEqual([x.args[0] for x in sensor.call_args_list],[b"same-image",b"same-image"])
        data=json.loads(result.detail)
        self.assertIn("GROUNDING_DISAGREEMENT_OR_AMBIGUITY",data["observation_json"]["ambiguities"])
        obs=fuse_observation(payload(),data,observation_id="obs")
        self.assertEqual(resolve_target(TargetIntent(role="search_input"),obs).status,"not_found")



class NativeDocumentFixture:
    """Independent value/readback oracle behind a generic opaque window title."""
    def __init__(self, value=""):
        self.value = value
        self.observations = 0
        self.deliveries = []
        self.ref = ""

    def observe(self, **kwargs):
        self.observations += 1
        self.ref = f"doc{self.observations}:e1"
        result = payload([control(ref=self.ref, role="Document", label="", value=self.value,
                                  writable=True)], observation_id=f"doc{self.observations}",
                         capabilities={"writable": [{"ref": self.ref}]})
        result["snapshot"].update(truncated=True, vision_recommended=True)
        return UIActionResult(True, "Document observé.", json.dumps(result, ensure_ascii=False))

    def execute(self, name, args, **kwargs):
        self.deliveries.append((name, dict(args)))
        if name == "open_application":
            return AgentActionResult(name, True, "Éditeur ouvert.", "launch delivered")
        if name != "write_ui_element" or args["ref"] != self.ref:
            return AgentActionResult(name, False, "Ref périmée.", "stale_ref")
        before = self.value
        mode = str(args.get("mode") or "replace")
        self.value = before + args["text"] if mode == "append" else args["text"]
        return AgentActionResult(name, True, "Valeur relue exactement.", json.dumps({
            "verified": True, "mode": mode, "before": before,
            "value": self.value, "value_length": len(self.value)}, ensure_ascii=False))


class ObservedLogRegressionTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        config = replace(real_settings, compatibility_baseline=False, computer_use_enabled=True,
                         vision_enabled=True, strict_proof_enabled=False, operational_learning_enabled=False,
                         ui_target_max_age_s=30)
        for module in ("computer_use_controller", "native_tools", "perception_router", "agent_runtime"):
            self.stack.enter_context(patch("jarvis_agent."+module+".settings", config))
        self.fixture = NativeDocumentFixture()
        self.vision = Mock(side_effect=AssertionError("Vision must not replace readable native values"))
        self.controller = ComputerUseController(perception=PerceptionManager(
            structured=self.fixture.observe, vision=self.vision))
        self.registry = NativeToolRegistry()
        self.registry._computer_use = self.controller
        self.stack.enter_context(patch.object(self.registry, "_execute_legacy", side_effect=self.fixture.execute))

    def test_compound_write_is_blocked_before_goal_for_both_native_tools(self):
        for name in ("write_ui_element", "act_ui"):
            with self.subTest(name=name):
                self.controller.begin("Ouvre un éditeur et écris Bonjour.")
                self.registry.execute("observe_ui", {})
                args = {"ref": self.fixture.ref, "text": "Bonjour", "mode": "replace"}
                if name == "act_ui":
                    args = {"operation": "write", "target": {"ref": self.fixture.ref}, "text": "Bonjour",
                            "expected": [{"kind": "value_equals", "role": "Document", "value": "Bonjour"}]}
                result = self.registry.execute(name, args)
                self.assertFalse(result.success)
                self.assertEqual(result.detail, "GOAL_NOT_DEFINED")
        self.assertEqual(self.fixture.deliveries, [])
        self.assertEqual(self.fixture.value, "")

    def test_single_native_append_uses_exact_readback_without_goal_repair(self):
        self.fixture.value = "bonjour jarvis"
        self.controller.begin("Ajoutes à la fin comment cv")
        self.registry.execute("inspect_active_window", {})

        result = self.registry.execute(
            "write_ui_element",
            {
                "ref": self.fixture.ref,
                "text": " comment cv",
                "mode": "append",
            },
        )

        self.assertTrue(result.success)
        detail = json.loads(result.detail)
        self.assertTrue(detail["verified"])
        self.assertTrue(detail["single_native_proven"])
        self.assertEqual(self.fixture.value, "bonjour jarvis comment cv")
        self.assertEqual(
            [name for name, _args in self.fixture.deliveries],
            ["write_ui_element"],
        )
        self.vision.assert_not_called()

    def test_frozen_native_value_goal_reuses_readback_without_extra_perception(self):
        self.controller.begin("Ouvre un éditeur et écris Bonjour é — مرحبا.")
        self.registry.execute("observe_ui", {})
        value = "Bonjour é — مرحبا"
        self.registry.execute("define_ui_goal", {"conditions": [
            {"kind": "value_equals", "role": "Document", "value": value}]})
        result = self.registry.execute("write_ui_element", {"ref": self.fixture.ref, "text": value, "mode": "replace"})
        self.assertTrue(json.loads(result.detail)["verified"])
        self.assertTrue(self.controller.summary()["goal_completed"])
        self.assertEqual(self.fixture.value, value)
        self.assertEqual(self.fixture.observations, 1)
        self.vision.assert_not_called()

    def test_single_native_write_compatibility_path_remains_verified(self):
        registry, fixture = self.registry, self.fixture
        class Planner:
            def run(self, text, **kwargs):
                observed = registry.execute("inspect_active_window", {})
                written = registry.execute("write_ui_element", {"ref": fixture.ref, "text": "Test V2 terminé", "mode": "replace"})
                return __import__("jarvis_agent.agent_runtime", fromlist=["AgentTurnResult"]).AgentTurnResult(
                    "Texte vérifié.", (observed, written))
        result = ComputerUseRuntime(Planner(), registry).run("Remplace tout le contenu par Test V2 terminé. Vérifie le résultat.")
        self.assertTrue(result.goal_completed)
        self.assertEqual(self.fixture.observations, 1)
        self.assertEqual(self.fixture.value, "Test V2 terminé")
        self.vision.assert_not_called()

    def test_cerebras_can_retry_precondition_blocked_write_after_defining_goal(self):
        from jarvis_agent.agent_runtime import CerebrasResponsesAgent
        text = "Bonjour Salah — été, cœur, مرحبا"
        responses = [
            ("open_application", {"name": "éditeur inconnu"}),
            ("inspect_active_window", {}),
            ("write_ui_element", {"ref": "doc1:e1", "text": text, "mode": "replace"}),
            ("define_ui_goal", {"conditions": [{"kind": "value_equals", "role": "Document", "value": text}]}),
            ("write_ui_element", {"ref": "doc1:e1", "text": text, "mode": "replace"}),
            ("", "Texte écrit et vérifié."),
        ]
        class Planner(CerebrasResponsesAgent):
            def _chat(self, **kwargs):
                name, args = responses.pop(0)
                calls = [SimpleNamespace(id="call"+str(len(responses)), function=SimpleNamespace(
                    name=name, arguments=json.dumps(args, ensure_ascii=False)))] if name else []
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                    content="" if name else args, tool_calls=calls))])
        planner = Planner(self.registry)
        planner.api_key = "test-only"
        result = ComputerUseRuntime(planner, self.registry).run("Ouvre un éditeur et écris exactement : " + text)
        self.assertTrue(result.goal_completed)
        self.assertEqual(self.fixture.value, text)
        self.assertEqual([name for name, _ in self.fixture.deliveries], ["open_application", "write_ui_element"])
        self.assertEqual(self.fixture.observations, 1)
        self.vision.assert_not_called()
        for message in planner._messages:
            if message.get("role") == "tool":
                json.loads(json.loads(message["content"])["detail"])

    def test_read_only_mission_does_not_spawn_second_planner_loop(self):
        from jarvis_agent.agent_runtime import AgentTurnResult
        self.fixture.value = "Référence Tunis-42-é"
        registry, controller = self.registry, self.controller

        class Planner:
            calls = 0

            def run(self, text, **kwargs):
                self.calls += 1
                actions = (registry.execute("inspect_active_window", {}),)
                return AgentTurnResult(
                    controller.state.current.entities[0].value,
                    actions,
                )

        planner, logs = Planner(), []
        result = ComputerUseRuntime(planner, registry).run(
            "Relis le contenu du document ouvert.",
            log=logs.append,
        )

        self.assertIsNone(result.goal_completed)
        self.assertEqual(result.mission_status, "observed")
        self.assertEqual(result.text, "Référence Tunis-42-é")
        self.assertEqual(planner.calls, 1)
        self.assertEqual(self.fixture.deliveries, [])
        self.vision.assert_not_called()
        self.assertFalse(any("UI_ENGINE_REPAIR" in x for x in logs))

    def test_missing_goal_repair_is_bounded_and_never_delivers_a_compound_write(self):
        from jarvis_agent.agent_runtime import AgentTurnResult
        registry, fixture = self.registry, self.fixture
        class Planner:
            calls = 0
            def run(self, text, **kwargs):
                self.calls += 1
                observed = registry.execute("observe_ui", {})
                blocked = registry.execute("write_ui_element", {"ref": fixture.ref, "text": "bonjour"})
                return AgentTurnResult("Mission terminée", (observed, blocked))
        planner = Planner()
        result = ComputerUseRuntime(planner, registry).run("Ouvre un éditeur et écris bonjour.")
        self.assertFalse(result.goal_completed)
        self.assertEqual(planner.calls, 1)
        self.assertEqual(result.mission_status, "inconclusive")
        self.assertEqual(self.fixture.deliveries, [])
        self.assertEqual(self.fixture.value, "")

    def test_successful_launch_does_not_spawn_goal_repair_loop(self):
        from jarvis_agent.agent_runtime import AgentTurnResult
        registry = self.registry

        class Planner:
            calls = 0

            def run(inner, text, **kwargs):
                inner.calls += 1
                opened = registry.execute(
                    "open_file",
                    {"name": "CursorUserSetup", "within": "Téléchargements"},
                )
                return AgentTurnResult("Installateur ouvert.", (opened,))

        planner = Planner()
        self.stack.enter_context(
            patch.object(
                self.registry,
                "_execute_legacy",
                return_value=AgentActionResult(
                    "open_file",
                    True,
                    "Fichier ouvert.",
                    r"C:\Users\salah\Downloads\CursorUserSetup-x64.exe",
                ),
            )
        )
        result = ComputerUseRuntime(planner, registry).run(
            "Ouvre Cursor Setup dans Téléchargements."
        )

        self.assertEqual(planner.calls, 1)
        self.assertIsNone(result.goal_completed)
        self.assertEqual(result.mission_status, "delivered")

    def test_failed_launch_keeps_original_diagnostic(self):
        self.controller.begin("Copie les deux documents ouverts.")
        failed = AgentActionResult("open_file", False, "Fichier absent du dossier demandé.", "Source_V2.txt not found in Documents")
        result = self.controller.execute("open_file", {"name": "Source_V2.txt", "within": "Documents"}, lambda *args: failed)
        self.assertFalse(result.success)
        self.assertEqual(json.loads(result.detail)["execution_detail"], failed.detail)

    def test_native_receipt_feedback_remains_valid_json_when_large(self):
        from jarvis_agent.agent_runtime import GroqResponsesAgent
        original = {"verified": True, "value": "é"*6000, "mission_state": {"goal_completed": True}}
        result = AgentActionResult("write_ui_element", True, "Relu.", json.dumps(original, ensure_ascii=False))
        feedback = json.loads(GroqResponsesAgent._compact_tool_content(result.name, result))
        detail = json.loads(feedback["detail"])
        self.assertTrue(detail["verified"])
        self.assertLessEqual(len(feedback["detail"]), 3501)
        self.assertTrue(
            detail.get("planner_payload_reduced")
            or len(str(detail.get("value") or "")) < len(original["value"])
        )

    def test_partial_tree_hint_does_not_override_fused_readable_structure(self):
        from jarvis_agent.agent_runtime import _inspection_requests_visual_fallback
        result = AgentActionResult("inspect_active_window", True, "observed", json.dumps({
            "snapshot": {"semantic_coverage": "usable", "truncated": True, "vision_recommended": True},
            "ui_observation": {"coverage": {"structured": "usable", "mission_target": "unspecified"}},
            "perception": {"vision_attempted": False}}))
        self.assertFalse(_inspection_requests_visual_fallback(result))

    def test_missing_target_can_escalate_but_failed_vision_is_not_blindly_repeated(self):
        from jarvis_agent.agent_runtime import _inspection_requests_visual_fallback
        base = {"ui_observation": {"coverage": {"structured": "usable", "mission_target": "not_found"}},
                "perception": {"vision_attempted": False}}
        self.assertTrue(_inspection_requests_visual_fallback(AgentActionResult(
            "inspect_active_window", True, "observed", json.dumps(base))))
        base["perception"].update(vision_attempted=True, vision_success=False, vision_error="MODEL_NOT_INSTALLED")
        self.assertFalse(_inspection_requests_visual_fallback(AgentActionResult(
            "inspect_active_window", True, "observed", json.dumps(base))))


if __name__ == "__main__":
    unittest.main()
