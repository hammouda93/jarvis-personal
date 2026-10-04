"""Real Chromium tests on a fresh fixture/profile, never a user's browser."""
import io
import json
import subprocess
import socket
import tempfile
import time
import unittest
from pathlib import Path

from benchmarks.unknown_ui import generate_html
from jarvis_agent.browser_adapter import BrowserAdapter
from jarvis_agent.computer_use_controller import ComputerUseController
from jarvis_agent.perception_router import PerceptionManager
from jarvis_agent.ui_observation import UIEntity


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


if __name__ == "__main__":
    unittest.main()
