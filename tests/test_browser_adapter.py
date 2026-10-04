"""Real Chromium tests on a fresh fixture/profile, never a user's browser."""
import io
import json
import subprocess
import socket
import tempfile
import time
import threading
import unittest
from pathlib import Path

from benchmarks.unknown_ui import generate_html
from benchmarks.unknown_multi_surface import make_server
from jarvis_agent.browser_adapter import BrowserAdapter
from jarvis_agent.computer_use_controller import ComputerUseController
from jarvis_agent.perception_router import PerceptionManager
from jarvis_agent.ui_observation import UIEntity
from jarvis_agent.ui_observation import object_detail


class BrowserBoundaryTests(unittest.TestCase):
    def test_endpoint_must_be_explicit_and_valid(self):
        for value in ("", "file:///profile", "not a URL"):
            with self.assertRaises(ValueError):
                BrowserAdapter(value)


class ChromiumFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("Optional dependency: pip install -r requirements-browser.txt")
        with sync_playwright() as playwright:
            cls.binary = playwright.chromium.executable_path
        if not Path(cls.binary).is_file():
            raise unittest.SkipTest("Optional browser binary: python -m playwright install chromium")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = self.root / "fresh-profile"
        self.fixture = self.root / "fixture.html"
        self.fixture.write_text(generate_html(seed=47), encoding="utf-8")
        from playwright.sync_api import sync_playwright
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0))
            port=listener.getsockname()[1]
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(headless=True,
            args=["--no-sandbox", "--remote-debugging-port="+str(port)])
        self.addCleanup(self.stop_browser)
        self.page = self.browser.new_page()
        self.page.goto(self.fixture.as_uri())
        self.adapter = BrowserAdapter(f"http://127.0.0.1:{port}")
        self.addCleanup(self.adapter.close)
        self.page_ref = self.adapter.pages()[0]["page_ref"]

    def stop_browser(self):
        self.browser.close()  # Only the fresh browser created by this test.

    @staticmethod
    def find(snapshot, label):
        matches = [item for item in snapshot["controls"] if item["name"] == label]
        if len(matches) != 1:
            raise AssertionError(f"Expected one {label!r}, got {matches}")
        return matches[0]

    def test_unknown_dom_discovery_unicode_write_and_open_contact(self):
        snapshot = self.adapter.observe(self.page_ref)
        search = self.find(snapshot, "Recherche")
        result = self.adapter.act(self.page_ref, search["ref"], "write", {"text":"Salah"})
        self.assertTrue(result["verified"])
        snapshot = self.adapter.observe(self.page_ref)
        self.adapter.act(self.page_ref, self.find(snapshot, "Salah")["ref"], "click", {})
        snapshot = self.adapter.observe(self.page_ref)
        self.assertTrue(any(x["type"] == "heading" and x["name"] == "Salah" for x in snapshot["controls"]))
        message = "éàç — مرحبا 😀"
        result = self.adapter.act(self.page_ref, self.find(snapshot, "Message")["ref"], "write", {"text":message})
        self.assertTrue(result["verified"])
        self.assertEqual(result["value"], message)

    def test_new_observation_invalidates_old_opaque_browser_ref(self):
        old = self.find(self.adapter.observe(self.page_ref), "Recherche")["ref"]
        self.adapter.observe(self.page_ref)
        with self.assertRaisesRegex(ValueError, "stale_browser_ref"):
            self.adapter.act(self.page_ref, old, "write", {"text":"Must not be written"})
        self.assertEqual(self.find(self.adapter.observe(self.page_ref), "Recherche")["value"], "")

    def test_replaced_dom_node_is_not_silently_retargeted(self):
        snapshot = self.adapter.observe(self.page_ref)
        old = self.find(snapshot, "Salah")["ref"]
        # Writing triggers the fixture's DOM rerender, replacing identically labelled nodes.
        # Browser refs are invalidated after mutation, even when label/layout are identical.
        self.adapter.act(self.page_ref, self.find(snapshot, "Recherche")["ref"], "write", {"text":"Salah"})
        with self.assertRaisesRegex(ValueError, "stale_browser_ref"):
            self.adapter.act(self.page_ref, old, "click", {})

    def test_external_same_label_node_replacement_is_detected(self):
        snapshot=self.adapter.observe(self.page_ref)
        ref=self.find(snapshot,"Salah")["ref"]
        self.page.get_by_role("button",name="Salah",exact=True).evaluate("e=>e.replaceWith(e.cloneNode(true))")
        with self.assertRaisesRegex(ValueError,"browser_target_changed"):
            self.adapter.act(self.page_ref,ref,"click",{})

    def test_capture_uses_css_pixels_at_high_device_scale(self):
        cdp=self.page.context.new_cdp_session(self.page)
        cdp.send("Emulation.setDeviceMetricsOverride",{"width":800,"height":600,"deviceScaleFactor":2,"mobile":False})
        self.assertEqual(self.page.evaluate("devicePixelRatio"),2)
        image,metadata=self.adapter.capture(self.page_ref,crop=[0,0,500,500])
        from PIL import Image
        self.assertEqual(Image.open(io.BytesIO(image)).size,(400,300))
        self.assertEqual(metadata["coordinate_space"],"viewport_css")
        cdp.detach()

    def test_controller_send_has_message_proof_and_independent_fixture_oracle(self):
        from dataclasses import replace
        from unittest.mock import patch
        from jarvis_agent.config import settings
        snapshot=self.adapter.observe(self.page_ref)
        self.adapter.act(self.page_ref,self.find(snapshot,"Salah")["ref"],"click",{})
        with patch("jarvis_agent.computer_use_controller.settings",replace(settings,computer_use_enabled=True)), \
             patch("jarvis_agent.perception_router.settings",replace(settings,vision_enabled=False)):
            controller=ComputerUseController(perception=PerceptionManager(browser=self.adapter))
            controller.begin("Envoie hello-unique-47 à Salah")
            delegate=lambda *args:self.fail("Unexpected Windows execution")
            controller.execute("observe_ui",{"page_ref":self.page_ref},delegate)
            goal=[{"kind":"text_present","role":"content_title","value":"Salah"},
                  {"kind":"new_text","role":"message","region":"Messages","value":"hello-unique-47"}]
            self.assertTrue(controller.execute("define_ui_goal",{"conditions":goal},delegate).success)
            controller.execute("act_ui",{"operation":"write","target":{"role":"message_composer"},"text":"hello-unique-47",
                "expected":[{"kind":"value_equals","role":"message_composer","value":"hello-unique-47"}]},delegate)
            result=controller.execute("act_ui",{"operation":"click","target":{"role":"send_button"},"expected":[goal[1]]},delegate)
            self.assertTrue(json.loads(result.detail)["verified"])
            self.assertTrue(controller.summary()["goal_completed"])
            oracle=self.page.evaluate("fixtureState()")
            self.assertEqual(oracle["contact"],"Salah")
            self.assertEqual(oracle["messages"],["hello-unique-47"])
            self.assertEqual(oracle["sendCount"],1)

    def test_capture_and_crop_use_viewport_css_coordinates(self):
        from PIL import Image
        before = self.adapter.observe(self.page_ref)
        image, metadata = self.adapter.capture(self.page_ref, crop=[0, 0, 500, 500])
        size = Image.open(io.BytesIO(image)).size
        bounds = before["window"]["bounds"]
        self.assertLessEqual(abs(size[0]-bounds[2]/2), 1)
        self.assertEqual(metadata["page_ref"], self.page_ref)
        self.assertTrue(metadata["capture_id"])
        self.assertEqual(metadata["crop"], [0, 0, bounds[2]/2, bounds[3]/2])

    def test_controller_completes_multistep_goal_on_real_unknown_dom(self):
        from dataclasses import replace
        from unittest.mock import patch
        from jarvis_agent.config import settings
        with patch("jarvis_agent.computer_use_controller.settings", replace(settings, computer_use_enabled=True)), \
             patch("jarvis_agent.perception_router.settings", replace(settings, vision_enabled=False)):
            controller = ComputerUseController(perception=PerceptionManager(browser=self.adapter))
            controller.begin("Cherche Salah et ouvre sa conversation")
            delegate = lambda *args: self.fail("A browser mission must not use a Windows fallback")
            self.assertTrue(controller.execute("observe_ui", {"page_ref":self.page_ref}, delegate).success)
            self.assertTrue(controller.execute("define_ui_goal", {"conditions":[
                {"kind":"text_present", "role":"content_title", "value":"Salah"}]}, delegate).success)
            result = controller.execute("act_ui", {"operation":"write", "target":{"role":"search_input"}, "text":"Salah",
                "expected":[{"kind":"value_equals", "role":"search_input", "value":"Salah"}]}, delegate)
            self.assertTrue(json.loads(result.detail)["verified"])
            result = controller.execute("act_ui", {"operation":"click", "target":{"role":"button", "label":"Salah"},
                "expected":[{"kind":"text_present", "role":"content_title", "value":"Salah"}]}, delegate)
            self.assertTrue(json.loads(result.detail)["verified"])
            self.assertTrue(controller.summary()["goal_completed"])

    def test_visual_capture_is_invalidated_after_navigation(self):
        old, metadata = self.adapter.capture(self.page_ref)
        self.page.goto("about:blank")
        target = UIEntity("visual:v1", "", "vision", "button", bounds=(1,1,20,20), score=.99)
        with self.assertRaisesRegex(ValueError, "stale_browser_document"):
            self.adapter.act_visual(self.page_ref, target, "click", {}, metadata)

    def test_contenteditable_and_shadow_dom_use_exact_fill(self):
        self.page.set_content('''<div contenteditable="true" role="textbox" aria-label="Editor"></div>
                <div id="host"></div><script>document.querySelector('#host').attachShadow({mode:'open'}).innerHTML=
                '<label>Courriel<input type="email"></label>'</script>''')
        snapshot=self.adapter.observe(self.page_ref)
        result=self.adapter.act(self.page_ref, self.find(snapshot,"Editor")["ref"],"write",{"text":"سلام é 😀"})
        self.assertEqual(result["value"], "سلام é 😀")
        snapshot=self.adapter.observe(self.page_ref)
        result=self.adapter.act(self.page_ref,self.find(snapshot,"Courriel")["ref"],"write",{"text":"agent@example.test"})
        self.assertTrue(result["verified"])

    def start_workflow_server(self, seed):
        server = make_server(seed=seed)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def stop():
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_port}"

    def test_held_out_three_surface_workflow_has_independent_oracles(self):
        for seed in (72, 113, 287):
            with self.subTest(seed=seed):
                origin = self.start_workflow_server(seed)
                context = self.browser.new_context()
                self.addCleanup(context.close)
                source_page = context.new_page()
                source_page.goto(origin + "/source")
                review = context.new_page()
                review.goto(origin + "/review")
                controller = ComputerUseController(perception=PerceptionManager(browser=self.adapter))
                controller.begin("Recopie la référence source, ouvre la confirmation puis confirme et vérifie les trois surfaces.")
                delegate = lambda *_args: self.fail("A DOM workflow must keep the structured browser backend")
                pages = self.adapter.pages()
                source_ref = next(x["page_ref"] for x in pages if x["url"] == origin + "/source")
                review_ref = next(x["page_ref"] for x in pages if x["url"] == origin + "/review")
                execute = lambda name, args=None: controller.execute(name, args or {}, delegate)
                source = execute("observe_ui", {"page_ref": source_ref})
                entities = object_detail(source.detail)["ui_observation"]["entities"]
                reference = next(x for x in entities if x["technical_role"] == "textbox")
                code = reference["value"]  # Read through perception, never through fixtureState/source code.
                self.assertTrue(code)
                self.assertFalse(reference["writable"])
                self.assertTrue(execute("bind_ui_surface", {"name": "source"}).success)
                self.assertTrue(execute("observe_ui", {"page_ref": review_ref}).success)
                self.assertTrue(execute("bind_ui_surface", {"name": "review"}).success)
                # The user asks to confirm; the fixture localizes the observed status.
                done = "Confirmed" if any(x.label == "Review" for x in controller.state.current.entities) else "Confirmé"
                goals = [{"kind": "text_present", "value": done, "surface": name}
                         for name in ("source", "review", "confirmation")]
                defined = execute("define_ui_goal", {"conditions": goals, "future_surfaces": ["confirmation"]})
                self.assertTrue(defined.success, defined.detail)
                written = execute("act_ui", {"operation": "write", "surface": "review", "target": {
                    "role": "text_input", "label": reference["label"]}, "text": code,
                    "expected": [{"kind": "value_equals", "label": reference["label"], "value": code, "surface": "review"}]})
                self.assertTrue(object_detail(written.detail)["verified"], written.detail)
                self.assertTrue(execute("observe_ui", {"surface": "review"}).success)
                open_button = next(x for x in controller.state.current.entities if x.technical_role == "button")
                confirm_label = "Confirm" if done == "Confirmed" else "Confirmer"
                opened = execute("act_ui", {"operation": "click", "target": {"ref": open_button.ref},
                    "expected_transition": {"kind": "popup", "surface": "confirmation"},
                    "expected": [{"kind": "target_present", "label": confirm_label, "surface": "confirmation"}]})
                self.assertTrue(object_detail(opened.detail)["verified"], opened.detail)
                confirmation_ref = controller.surfaces.get("confirmation").identity.page_ref
                popup_metadata = next(x for x in self.adapter.pages() if x["page_ref"] == confirmation_ref)
                self.assertEqual(popup_metadata["opener_page_ref"], review_ref)
                confirmed = execute("act_ui", {"operation": "click", "surface": "confirmation", "target": {"label": confirm_label},
                    "expected": [goals[2]]})
                self.assertTrue(object_detail(confirmed.detail)["verified"], confirmed.detail)
                self.assertFalse(controller.summary()["goal_completed"])
                final = execute("verify_ui_goal")
                self.assertTrue(final.success, final.detail)
                proof = object_detail(final.detail)["ui_verification"]["predicates"]
                self.assertEqual({x["surface"] for x in proof}, {"source", "review", "confirmation"})
                # Oracles are read by the test only after the engine's final proof.
                source_page.evaluate("1")  # Flush events for the independent test client.
                popup = next(p for p in context.pages if p.url.startswith(origin + "/confirmation"))
                self.assertEqual(review.evaluate("fixtureState().value"), code)
                self.assertTrue(source_page.evaluate("fixtureState().confirmed"))
                self.assertTrue(review.evaluate("fixtureState().confirmed"))
                self.assertEqual(popup.evaluate("fixtureState().confirmCount"), 1)
                popup.close()
                review.close()
                source_page.close()

    def test_browser_surface_binding_survives_navigation_but_old_elements_do_not(self):
        controller = ComputerUseController(perception=PerceptionManager(browser=self.adapter))
        controller.begin("Navigue puis vérifie")
        delegate = lambda *_args: self.fail("Unexpected Windows fallback")
        controller.execute("observe_ui", {"page_ref": self.page_ref}, delegate)
        old_ref = controller.state.current.entities[0].native_ref
        self.assertTrue(controller.execute("bind_ui_surface", {"name": "workspace"}, delegate).success)
        old_identity = controller.surfaces.get("workspace").identity
        self.page.goto("about:blank")
        self.page.set_content('<h1>Destination</h1><input aria-label="Editor">')
        observed = controller.execute("observe_ui", {"surface": "workspace"}, delegate)
        self.assertTrue(observed.success, observed.detail)
        self.assertTrue(old_identity.same_binding(controller.state.current.scope))
        self.assertFalse(old_identity.same_surface(controller.state.current.scope))
        with self.assertRaisesRegex(ValueError, "stale_browser_ref"):
            self.adapter.act(self.page_ref, old_ref, "write", {"text": "wrong"})
        activated = controller.execute("switch_ui_surface", {"surface": "workspace"}, delegate)
        self.assertTrue(object_detail(activated.detail)["verified"], activated.detail)

    def test_real_two_popups_are_ambiguous_and_no_confirmation_is_clicked(self):
        origin = self.start_workflow_server(72)
        self.page.goto(origin + "/review")
        self.page.evaluate("""() => {
          const b=document.querySelector('button');
          b.onclick=()=>{window.open('/confirmation');window.open('/confirmation');};
        }""")
        controller = ComputerUseController(perception=PerceptionManager(browser=self.adapter))
        controller.begin("Ouvre la confirmation puis confirme")
        delegate = lambda *_args: self.fail("Unexpected Windows fallback")
        controller.execute("observe_ui", {"page_ref": self.page_ref}, delegate)
        goal = {"kind": "text_present", "value": "Confirmé", "surface": "confirmation"}
        self.assertTrue(controller.execute("define_ui_goal", {"conditions": [goal], "future_surfaces": ["confirmation"]}, delegate).success)
        button = next(x for x in controller.state.current.entities if x.technical_role == "button")
        result = controller.execute("act_ui", {"operation": "click", "target": {"ref": button.ref},
            "expected_transition": {"kind": "popup", "surface": "confirmation"},
            "expected": [{"kind": "target_present", "label": "Confirmer", "surface": "confirmation"}]}, delegate)
        self.assertFalse(object_detail(result.detail)["verified"])
        self.assertIn("AMBIGUOUS_SURFACE_TRANSITION", result.detail)
        self.assertTrue(controller.pending_verification())
        self.assertNotIn("confirmation", controller.surfaces.bindings)
        self.assertFalse(controller.execute("switch_ui_surface", {"surface": "default"}, delegate).success)
        self.page.evaluate("1")  # The adapter and test observer have separate protocol clients.
        popups = [p for p in self.page.context.pages if p.url.startswith(origin + "/confirmation")]
        self.assertEqual(len(popups), 2)
        self.assertEqual([p.evaluate("fixtureState().confirmCount") for p in popups], [0, 0])

    def test_opening_existing_messages_cannot_prove_a_send_goal(self):
        self.page.set_content('''<button aria-label="Salah" onclick="document.querySelector('main').innerHTML=
            '<h1>Salah</h1><div role=log><p>already-existing-unique-message</p></div>'">Salah</button><main></main>''')
        controller = ComputerUseController(perception=PerceptionManager(browser=self.adapter))
        controller.begin("Envoie already-existing-unique-message à Salah")
        delegate = lambda *_args: self.fail("Unexpected Windows fallback")
        controller.execute("observe_ui", {"page_ref": self.page_ref}, delegate)
        goal = [{"kind": "text_present", "role": "content_title", "value": "Salah"},
                {"kind": "new_text", "role": "message", "value": "already-existing-unique-message"}]
        self.assertTrue(controller.execute("define_ui_goal", {"conditions": goal}, delegate).success)
        result = controller.execute("act_ui", {"operation": "click", "target": {"label": "Salah"}, "expected": goal}, delegate)
        self.assertTrue(object_detail(result.detail)["verified"], result.detail)
        self.assertFalse(controller.summary()["goal_completed"])
        self.assertFalse(controller.execute("verify_ui_goal", {}, delegate).success)
        self.assertEqual(controller.goal_verdict.reason, "NEW_TEXT_ACTION_PROOF_REQUIRED")


if __name__ == "__main__":
    unittest.main()
