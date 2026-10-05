"""Unit/replay checks; desktop behavior requires the separate Windows gates."""
from __future__ import annotations

import gc
import io
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from jarvis_agent.browser_core import BrowserCore, NativeBrowserTransport, read_packet, write_packet
from jarvis_agent.browser_native_host import NativeHost
from jarvis_agent.computer_grounding import ComputerGrounding
from jarvis_agent.foundation_tools import FoundationToolAdapter
from jarvis_agent.memory_core_store import MemoryCoreStore
from jarvis_agent.memory_retrieval import relevance, search
from jarvis_agent.memory_router import MemoryRouter, MemoryRoutingRuntime
from jarvis_agent.foundation_tools import FoundationRuntime


class NoLLM:
    calls = 0
    def run(self, *args, **kwargs):
        self.calls += 1
        from jarvis_agent.agent_runtime import AgentTurnResult
        return AgentTurnResult("session conversation")
    def reset(self): pass
    def warm_up(self, **kwargs): pass
    def record_external_turn(self, *args, **kwargs): pass


class MemoryCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = MemoryCoreStore(Path(self.tmp.name)/"memory.sqlite3")
        self.llm = NoLLM()
        self.tools = FoundationToolAdapter(None, memory=self.memory)
        self.runtime = MemoryRoutingRuntime(self.llm, self.tools)

    def tearDown(self):
        gc.collect()
        self.tmp.cleanup()

    def test_explicit_write_is_persisted_without_model(self):
        for phrase in ["Mémorise que mon film test est Arrival", "garde en tête que j'ai une réunion vendredi",
                       "Retiens que mon adresse est Tunis", "Keep in mind that my movie is Inception",
                       "Mémorise:mon sport est tennis"]:
            with self.subTest(phrase=phrase):
                result = self.runtime.run(phrase)
                self.assertEqual([a.name for a in result.actions], ["remember_information"])
                self.assertTrue(result.actions[0].success)
        self.assertEqual(self.llm.calls,0)
        self.assertEqual(search(self.memory,"Quel est le nom de mon film ?")[0].content, "my movie is Inception")

    def test_negation_quoting_procedural_questions_do_not_persist(self):
        for phrase in ["Ne mémorise pas mon film", "Il a dit ‘Mémorise que mon film est Arrival’",
                       "Traduis ‘remember my name’", "Comment retrouver mon fichier ?",
                       "Ouvre mon navigateur", "Je me souviens de mon film", "Remember that my film is Arrival?"]:
            with self.subTest(phrase=phrase):
                kind = MemoryRouter().decide(phrase).kind
                self.assertNotEqual(kind,"write")
        self.tools.begin_turn("Ne mémorise pas mon film")
        self.assertFalse(self.tools.execute("remember_information",{"content":"injected"}).success)
        self.assertEqual(search(self.memory,"injected"),[])
        for phrase in ["Dis-moi comment ouvrir mon navigateur", "Peux-tu me dire comment retrouver mon fichier",
                       "rappelle-moi d'acheter du café pour mon bureau"]:
            self.assertEqual(MemoryRouter().decide(phrase).kind,"pass")

    def test_session_before_persistent_memory(self):
        self.memory.remember("Mon film est Arrival")
        self.runtime.run("Mon film est Inception")
        result = self.runtime.run("Quel est le nom de mon film ?")
        self.assertIn("Inception",result.text)
        self.assertEqual(result.actions,())

    def test_unknown_personal_question_recalls_then_clarifies(self):
        result = self.runtime.run("Quel est mon restaurant préféré ?")
        self.assertEqual([a.name for a in result.actions],["recall_information"])
        self.assertIn("préciser",result.text)
        self.assertEqual(self.llm.calls,0)

    def test_connectors_after_empty_persistent_and_only_when_needed(self):
        calls = []
        self.runtime.connector_resolver = lambda text: calls.append(text) or ["Mon bureau est Tunis"]
        result = self.runtime.run("Quel est mon bureau ?")
        self.assertIn("Tunis",result.text)
        self.assertEqual(len(calls),1)
        self.memory.remember("Mon bureau est Sousse")
        result = self.runtime.run("Quel est mon bureau ?")
        self.assertIn("Sousse",result.text)
        self.assertEqual(len(calls),1)

    def test_retrieval_paraphrases_accents_plural_and_typos(self):
        self.memory.remember("Mon film test est Arrival")
        self.memory.remember("J'ai une réunion vendredi")
        for query in ["Quel est le nom de mon film ?", "Tu te souviens de mon film préféré ?",
                      "What is my favorite movie?", "Quels films avais-je déjà mémorisés ?"]:
            with self.subTest(query=query):
                self.assertEqual(search(self.memory,query)[0].content,"Mon film test est Arrival")
        for query in ["Quand est ma prochaine réunion ?", "ma reunoin", "my meeting"]:
            with self.subTest(query=query):
                self.assertIn("vendredi",search(self.memory,query)[0].content)

    def test_unknown_entities_do_not_match_common_topic(self):
        self.memory.remember("Projet Atlas : espace client")
        self.memory.remember("Mon film est Arrival")
        for query in ["Atlas Nova", "Mon film Titanic", "mon restaurant", "Atlas 999"]:
            self.assertEqual(search(self.memory,query),[])
        self.assertEqual(relevance("film","profil"),0)

    def test_conflicting_facts_require_clarification(self):
        self.memory.remember("Mon film est Arrival")
        self.memory.remember("Mon film est Inception")
        self.assertIn("Laquelle",self.runtime.run("Quel est mon film ?").text)

    def test_real_process_restart_and_router_tool_trace(self):
        db = str(Path(self.tmp.name)/"restart.sqlite3")
        def turn(phrase):
            result = subprocess.run([sys.executable,"-m","jarvis_agent.memory_core_cli","--db",db,phrase],
                capture_output=True,text=True,encoding="utf-8",timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            return json.loads(result.stdout)
        saved = turn("Mémorise que mon film test est Inception")
        self.assertEqual(saved["actions"][0]["tool"],"remember_information")
        for query in ["Quel est le nom de mon film ?", "Tu te rappelles de mon film préféré ?",
                      "C'est quoi mon film", "Peux-tu me rappeler le nom de mon film", "Tu connais mon film préféré"]:
            recovered = turn(query)
            self.assertEqual(recovered["actions"][0]["tool"],"recall_information")
            self.assertIn("Inception",recovered["text"])
        turn("garde en tête que j'ai une réunion vendredi")
        self.assertIn("vendredi",turn("Quand est ma prochaine réunion ?")["text"])


class BrowserContractTests(unittest.TestCase):
    def test_framing_unicode_and_partial_stream(self):
        stream = io.BytesIO()
        write_packet(stream,{"text":"été عربي"})
        stream.seek(0)
        self.assertEqual(read_packet(stream),{"text":"été عربي"})

    def test_framing_rejects_size_nonobject_and_truncation(self):
        for payload in [struct.pack("=I",1_000_001),struct.pack("=I",2)+b"[]",struct.pack("=I",8)+b"{}"]:
            with self.assertRaises((ValueError,EOFError)):
                read_packet(io.BytesIO(payload))

    def test_browser_rejects_unbound_writes_and_unsafe_urls(self):
        class Transport:
            def request(self,op,args): raise AssertionError("must not reach transport")
        core = BrowserCore(Transport())
        for op,args in [("write",{"tab_id":1,"text":"hello"}),
                        ("click",{"ref":"observed"}), ("verify",{"tab_id":1}),
                        ("navigate",{"url":"file:///C:/secret"}),
                        ("download",{"url":"https://user:password@example.com"})]:
            with self.assertRaises(ValueError): core.call(op,**args)

    def test_browser_transport_reports_unavailable_before_dispatch(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "bridge.json"
            config.write_text(
                json.dumps(
                    {
                        "port": 47653,
                        "token": "test-token",
                        "extension_id": "a" * 32,
                    }
                ),
                encoding="utf-8",
            )
            transport = NativeBrowserTransport(config, timeout_s=0.2)
            with patch(
                "jarvis_agent.browser_core.socket.create_connection",
                side_effect=ConnectionRefusedError("offline"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "browser_bridge_unavailable",
                ):
                    transport.request("list_tabs", {})

    def test_native_host_auth_expiry_and_no_replay(self):
        host = NativeHost({"token":"test"},io.BytesIO(),io.BytesIO())
        host.ready.set()
        request = {"id":"x","version":1,"token":"wrong","operation":"write","arguments":{},"deadline_ms":1}
        self.assertEqual(host.dispatch(request)["error"],"bridge_authentication_failed")
        request["token"]="test"
        self.assertEqual(host.dispatch(request)["error"],"expired_or_invalid_request")
        self.assertEqual(host.output.getvalue(),b"")

    def test_real_native_host_subprocess_socket_stdio_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            with socket.socket() as s:
                s.bind(("127.0.0.1",0)); port = s.getsockname()[1]
            config = Path(folder)/"bridge.json"
            config.write_text(json.dumps({"port":port,"token":"test-token","extension_id":"a"*32}))
            process = subprocess.Popen([sys.executable,"-m","jarvis_agent.browser_native_host","--config",str(config),
                                      "chrome-extension://"+"a"*32+"/"],stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                write_packet(process.stdin,{"hello":1})
                def extension_peer():
                    request = read_packet(process.stdout)
                    self.assertNotIn("token",request)
                    write_packet(process.stdin,{"id":request["id"],"ok":True,
                                               "result":[{"tab_id":7,"title":"normal profile"}]})
                peer = threading.Thread(target=extension_peer)
                peer.start()
                for _ in range(40):
                    try:
                        with socket.create_connection(("127.0.0.1",port),0.1): break
                    except OSError: time.sleep(0.05)
                result = BrowserCore(NativeBrowserTransport(config)).list_tabs()
                self.assertEqual(result[0]["tab_id"],7)
                peer.join(2)
                self.assertFalse(peer.is_alive())
            finally:
                process.terminate(); process.communicate(timeout=5)


class ReplayDesktop:
    """Synthetic desktop fixture; explicitly not desktop acceptance evidence."""
    def __init__(self):
        self.window = {"window_id":"101","pid":42,"bounds":[0,0,300,200]}
        self.foreground = "101"
        self.controls = []
        self.actions = []
    def identity(self,window_id): return dict(self.window)
    def observe(self,window_id):
        return {"controls":self.controls,"snapshot":{"semantic_coverage":"usable" if self.controls else "insufficient"}}
    def require_foreground(self,window_id):
        if self.foreground != window_id: raise RuntimeError("foreground_window_changed")
    def capture(self,window_id):
        from PIL import Image
        from jarvis_agent.fast_grounding import png_from_image
        self.require_foreground(window_id)
        return png_from_image(Image.new("RGB",(300,200),"white"))
    def focus_probe(self,window_id,e):
        self.actions.append((e,"focus_probe",{}))
        self.controls = [{
            "ref": "[9, 9, 9]",
            "name": "Message",
            "type": "Edit",
            "writable": True,
            "actionable": True,
            "focused": True,
            "bounds": [10,120,290,180],
            "value": "",
        }]
        return {
            "writable": True,
            "native_ref": "[9, 9, 9]",
            "text": "Message",
            "type": "Edit",
            "bbox": [10,120,290,180],
            "value": "",
            "focused": True,
        }

    def focused_editable(self,window_id):
        if not self.controls:
            return {"writable":False}
        item = self.controls[0]
        return {
            "writable": bool(item.get("writable")),
            "native_ref": item.get("ref",""),
            "text": item.get("name",""),
            "type": item.get("type",""),
            "bbox": item.get("bounds",[]),
            "value": item.get("value"),
            "focused": bool(item.get("focused")),
        }

    def act(self,window_id,e,operation,**args):
        self.actions.append((e,operation,args))
        if operation == "write":
            if self.controls:
                self.controls[0]["value"] = args["text"]
        return True


class GroundingReplayTests(unittest.TestCase):
    def setUp(self):
        self.backend = ReplayDesktop()
        self.ocr = lambda png,remaining: {"provider":"windows_ocr","elements":[
            {"text":"Person One","bbox":[10,10,120,40],"type":"text","confidence":0.75},
            {"text":"Person One Other","bbox":[10,60,170,90],"type":"text","confidence":0.75}]}
        self.core = ComputerGrounding(self.backend,visual=self.ocr)

    def test_opaque_objects_and_exact_selection_not_substring(self):
        observation = self.core.observe("101")
        self.assertTrue(all({"text","bbox","type","confidence","ref"} <= set(e) for e in observation["elements"]))
        self.assertEqual(len(self.core.find(text="Person One")["matches"]),1)
        self.assertFalse(self.core.find(text="Person",exact=False)["unique"])

    def test_ocr_label_cannot_be_used_as_composer(self):
        e = self.core.observe("101")["elements"][0]
        with self.assertRaisesRegex(RuntimeError,"editable_target_not_proven"):
            self.core.act(e["ref"],"write",text="hello")
        self.assertEqual(self.backend.actions,[])

    def test_visual_label_requires_focus_probe_before_write(self):
        observed = self.core.observe("101")["elements"][0]

        with self.assertRaisesRegex(RuntimeError,"editable_target_not_proven"):
            self.core.act(observed["ref"],"write",text="hello")

        # A separate focus probe may promote the visual label only after the
        # backend proves that Windows focused a genuine editable control.
        promoted = self.core.focus_probe(
            self.core.observe("101")["elements"][0]["ref"]
        )
        self.assertTrue(promoted["verified"])
        edit = promoted["element"]
        self.assertEqual(edit["sensor"],"uia_focus")
        self.assertTrue(edit["writable"])

        written = self.core.act(
            edit["ref"],
            "write",
            text="hello",
        )
        self.assertTrue(written["verified"])
        self.assertEqual(self.backend.controls[0]["value"],"hello")

    def test_promoted_editor_survives_exact_header_context_verification(self):
        original_focus_probe = self.backend.focus_probe

        def focus_with_header(window_id, element):
            result = original_focus_probe(window_id, element)
            self.backend.controls.append(
                {
                    "ref": "header",
                    "name": "Exact Person",
                    "type": "Heading",
                    "writable": False,
                    "actionable": False,
                    "focused": False,
                    "bounds": [10,10,180,40],
                    "value": None,
                    "region": "header",
                }
            )
            return result

        self.backend.focus_probe = focus_with_header
        visual_ref = self.core.observe("101")["elements"][0]["ref"]
        promoted = self.core.focus_probe(visual_ref)["element"]

        result = self.core.act(
            promoted["ref"],
            "write",
            text="private draft",
            context={"text":"Exact Person","region":"header"},
        )

        self.assertTrue(result["verified"])
        self.assertEqual(self.backend.controls[0]["value"],"private draft")

    def test_visual_focus_probe_refuses_unproven_editability(self):
        self.backend.focus_probe = lambda *args, **kwargs: {
            "writable": False,
            "type": "Text",
            "text": "Message",
            "bbox": [10,120,290,180],
        }
        ref = self.core.observe("101")["elements"][0]["ref"]

        with self.assertRaisesRegex(
            RuntimeError,
            "focused_editable_control_not_proven",
        ):
            self.core.focus_probe(ref)

    def test_focus_change_prevents_visual_action(self):
        e = self.core.observe("101")["elements"][0]
        self.backend.foreground="202"
        with self.assertRaisesRegex(RuntimeError,"foreground_window_changed"):
            self.core.act(e["ref"],"click")
        self.assertEqual(self.backend.actions,[])

    def test_window_geometry_change_expires_reference(self):
        e = self.core.observe("101")["elements"][0]
        self.backend.window["bounds"]=[0,0,600,400]
        with self.assertRaisesRegex(RuntimeError,"grounding_window_changed"):
            self.core.act(e["ref"],"click")

    def test_action_dispatch_is_not_semantic_success(self):
        e = self.core.observe("101")["elements"][0]
        result = self.core.act(e["ref"],"click",expected={"text":"Exact header"})
        self.assertTrue(result["dispatched"])
        self.assertFalse(result["verified"])
        with self.assertRaisesRegex(RuntimeError,"stale_grounding_ref"):
            self.core.act(e["ref"],"click")

    def test_uia_write_reinspection_verifies_value(self):
        self.backend.controls=[{"ref":"uia-ref","name":"Message","type":"Edit","writable":True,
                               "bounds":[10,120,290,180],"value":""}]
        e = self.core.observe("101")["elements"][0]
        self.assertTrue(self.core.act(e["ref"],"write",text="Unicode été عربي")["verified"])

    def test_value_pattern_distinguishes_empty_value_readonly_and_missing_provider(self):
        from types import SimpleNamespace
        from jarvis_agent.grounding_uia_worker import edit_state
        pattern=SimpleNamespace(CurrentValue="",CurrentIsReadOnly=False)
        self.assertEqual(edit_state(SimpleNamespace(iface_value=pattern)),{"writable":True,"value":""})
        pattern.CurrentIsReadOnly=True
        pattern.CurrentValue="  literal spaces  "
        self.assertEqual(edit_state(SimpleNamespace(iface_value=pattern)),
                         {"writable":False,"value":"  literal spaces  "})
        self.assertEqual(edit_state(SimpleNamespace()),{"writable":False,"value":None})

    def test_unknown_postcondition_rejected(self):
        self.core.observe("101")
        with self.assertRaises(ValueError): self.core.verify({})

    def test_visual_provider_failure_reports_insufficient_not_success(self):
        self.core.visual=lambda *args: (_ for _ in ()).throw(RuntimeError("grounding_latency_budget_exceeded"))
        obs = self.core.observe("101")
        self.assertEqual(obs["elements"],[])
        self.assertIn("grounding_latency_budget_exceeded",obs["errors"])

    def test_out_of_image_and_low_confidence_objects_cannot_be_acted(self):
        self.core.visual=lambda *args: {"provider":"model","elements":[
            {"text":"bad","bbox":[-10,0,2000,1000],"type":"button","confidence":1},
            {"text":"guess","bbox":[10,10,30,30],"type":"button","confidence":0.2}]}
        obs = self.core.observe("101")
        self.assertEqual(len(obs["elements"]),1)
        with self.assertRaisesRegex(RuntimeError,"confidence_too_low"):
            self.core.act(obs["elements"][0]["ref"],"click")

    def test_foundation_tools_expose_focus_probe_when_computer_core_exists(self):
        class Delegate:
            def ollama_tools(self):
                return []

            @staticmethod
            def _ollama(name, description, properties, required):
                return {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }

        adapter = FoundationToolAdapter(Delegate(), computer=self.core)
        names = {
            item["function"]["name"]
            for item in adapter.ollama_tools()
        }
        self.assertIn("computer_focus_probe", names)

    def test_browser_scope_blocks_windows_input(self):
        class Forbidden:
            def execute(self,*args,**kwargs): raise AssertionError("OS path must not run")
        adapter = FoundationToolAdapter(Forbidden(),browser=object())
        adapter.begin_turn("ouvre un onglet Chrome")
        for name in ("type_text_active_window","press_key","write_ui_element","computer_write"):
            result = adapter.execute(name,{"text":"never in other app"})
            self.assertFalse(result.success)
            self.assertIn("tab_scoped",result.detail)
        adapter.begin_turn("Ouvre Bloc-notes")
        self.assertFalse(adapter.browser_mode)

    def test_wrong_view_header_blocks_write_before_dispatch(self):
        self.backend.controls=[{"ref":"composer","name":"Message","type":"Edit","writable":True,
                               "bounds":[10,120,290,180],"value":""},
                              {"ref":"header","name":"Different Person","type":"Heading",
                               "bounds":[10,10,200,40],"region":"header"}]
        ref = self.core.observe("101")["elements"][0]["ref"]
        with self.assertRaisesRegex(RuntimeError,"view_context_not_verified"):
            self.core.act(ref,"write",text="private draft",context={"text":"Exact Person","region":"header"})
        self.assertEqual(self.backend.actions,[])

    def test_contact_elsewhere_is_not_header_proof(self):
        self.backend.controls=[{"ref":"contact","name":"Exact Person","type":"Text",
                               "bounds":[10,10,200,40],"region":"navigation"}]
        self.core.observe("101")
        self.assertFalse(self.core.verify({"text":"Exact Person","region":"header"}))

    def test_incomplete_observation_cannot_prove_text_absent(self):
        self.core.observe("101")
        self.assertFalse(self.core.verify({"absent_text":"unobserved draft"}))

    def test_partial_desktop_action_marks_unknown_and_prevents_repeat(self):
        def lost_action(*args,**kwargs):
            self.backend.actions.append("sent before connection loss")
            raise RuntimeError("provider_disconnected")
        self.backend.act=lost_action
        adapter=FoundationToolAdapter(None,computer=self.core)
        ref=self.core.observe("101")["elements"][0]["ref"]
        result=adapter.execute("computer_click",{"ref":ref})
        self.assertFalse(result.success)
        self.assertIn("computer_outcome_unknown",result.detail)
        self.assertEqual(adapter.pending_verification,{("computer","101")})
        fresh_ref=self.core.observe("101")["elements"][0]["ref"]
        result=adapter.execute("computer_click",{"ref":fresh_ref})
        self.assertIn("verification_before",result.detail)
        self.assertEqual(len(self.backend.actions),1)


class RuntimePreflightTests(unittest.TestCase):
    def test_live_preflight_rejects_codex_dependency_overlay_paths(self):
        from unittest.mock import patch
        from jarvis_agent.runtime_preflight import _live_path_contamination

        with patch(
            "jarvis_agent.runtime_preflight.sys.path",
            [
                r"D:\\Django_Projects\\jarvis-main\\jarvis-main",
                r"D:\\Django_Projects\\jarvis-main\\jarvis-main\\.cache\\foundation-test-deps",
                r"D:\\Django_Projects\\jarvis-main\\jarvis-main\\.venv\\Lib\\site-packages",
            ],
        ):
            contaminated = _live_path_contamination()

        self.assertEqual(
            contaminated,
            [
                r"D:\\Django_Projects\\jarvis-main\\jarvis-main\\.cache\\foundation-test-deps"
            ],
        )


    def test_windows_automation_python_gate_allows_memory_only_legacy_runtime(self):
        from jarvis_agent.runtime_preflight import (
            _windows_automation_python_compatible,
        )

        ok, reason = _windows_automation_python_compatible((3, 9, 0))

        self.assertFalse(ok)
        self.assertIn("Python >=3.10.10", reason)

    def test_windows_automation_python_gate_accepts_modern_runtime(self):
        from jarvis_agent.runtime_preflight import (
            _windows_automation_python_compatible,
        )

        ok, reason = _windows_automation_python_compatible((3, 12, 0))

        self.assertTrue(ok)
        self.assertEqual(reason, "")


class FoundationPromptTests(unittest.TestCase):
    def test_browser_core_prompt_uses_only_current_browser_primitives(self):
        from unittest.mock import patch
        from jarvis_agent.agent_runtime import _effective_system_instructions
        with patch.dict(
            os.environ,
            {
                "JARVIS_BROWSER_CORE_ENABLED": "1",
                "JARVIS_COMPUTER_CORE_ENABLED": "0",
            },
            clear=False,
        ):
            prompt = _effective_system_instructions()

        self.assertIn("browser_list_tabs", prompt)
        self.assertIn("browser_observe_dom", prompt)
        self.assertIn("browser_verify", prompt)
        self.assertIn("Ne substitue jamais une", prompt)

    def test_computer_core_prompt_requires_focus_probe_before_opaque_write(self):
        from unittest.mock import patch
        from jarvis_agent.agent_runtime import _effective_system_instructions
        with patch.dict(
            os.environ,
            {
                "JARVIS_BROWSER_CORE_ENABLED": "0",
                "JARVIS_COMPUTER_CORE_ENABLED": "1",
            },
            clear=False,
        ):
            prompt = _effective_system_instructions()

        self.assertIn("computer_focus_probe", prompt)
        self.assertIn("vrai contrôle UIA focalisé", prompt)
        self.assertIn("n'affirme jamais qu'un message a été envoyé", prompt)


class FoundationRuntimeTests(unittest.TestCase):
    def test_dispatch_is_successful_tool_but_final_requires_proof(self):
        class Browser:
            def call(self,operation,**args):
                return {"dispatched":True,"verified":False} if operation == "click" else {"verified":True}
        adapter = FoundationToolAdapter(None,browser=Browser())
        result = adapter.execute("browser_click",{"tab_id":7,"ref":"observed"})
        self.assertTrue(result.success)
        runtime = FoundationRuntime(NoLLM(),adapter)
        self.assertIn("reste à vérifier",runtime.run("continue").text)
        adapter.execute("browser_verify",{"tab_id":8,"text":"other tab"})
        self.assertTrue(adapter.pending_verification)
        adapter.execute("browser_verify",{"tab_id":7,"text":"correct tab"})
        self.assertFalse(adapter.pending_verification)

    def test_unknown_browser_action_outcome_cannot_be_announced_as_completed(self):
        class Browser:
            calls=0
            def call(self,operation,**kwargs):
                self.calls+=1
                if operation=="verify": return {"verified":True}
                raise RuntimeError("browser_outcome_unknown_do_not_retry")
        adapter = FoundationToolAdapter(None,browser=Browser())
        self.assertFalse(adapter.execute("browser_click",{"tab_id":7,"ref":"observed"}).success)
        self.assertTrue(adapter.pending_verification)
        self.assertIn("verification_before",adapter.execute("browser_click",{"tab_id":7,"ref":"fresh"}).detail)
        self.assertEqual(adapter.browser.calls,1)
        adapter.execute("browser_verify",{"tab_id":8,"text":"unrelated proof"})
        self.assertEqual(adapter.uncertain_scopes,{("browser",7)})
        adapter.execute("browser_verify",{"tab_id":7,"text":"expected outcome"})
        self.assertFalse(adapter.uncertain_scopes)

    def test_chrome_activation_proof_has_browser_tab_scope(self):
        class Browser:
            def get_active_tab(self): return {"tab_id":7}
            def activate_tab(self,tab_id): return {"tab":{"tab_id":tab_id},"verified":True}
        adapter=FoundationToolAdapter(None,browser=Browser())
        result=adapter.execute("open_application",{"name":"chrome"})
        self.assertEqual(json.loads(result.detail)["scope"],["browser",7])

    def test_runtime_completed_contracts_require_core_proof(self):
        from jarvis_agent.agent_runtime import _completed_action_capabilities,_actions_have_verified_proof
        from jarvis_agent.native_tools import AgentActionResult
        def action(name,**payload):
            return AgentActionResult(name,True,"done",json.dumps(payload))
        actions=[action("browser_write",verified=True),action("browser_press",dispatched=True,verified=False)]
        self.assertIn("write_ui",_completed_action_capabilities(actions))
        self.assertNotIn("site_search",_completed_action_capabilities(actions))
        self.assertFalse(_actions_have_verified_proof(actions))
        actions.append(action("browser_verify",verified=True))
        self.assertIn("site_search",_completed_action_capabilities(actions))
        self.assertTrue(_actions_have_verified_proof(actions))

    def test_memory_router_is_in_factory_for_every_provider(self):
        from unittest.mock import patch
        from dataclasses import replace
        from jarvis_agent.config import settings
        from jarvis_agent.agent_runtime import build_agent_runtime
        for provider,cls in [("ollama","OllamaToolAgent"),("openai","OpenAIResponsesAgent"),
                             ("groq","GroqResponsesAgent"),("cerebras","CerebrasResponsesAgent")]:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as folder:
                memory = MemoryCoreStore(Path(folder)/"factory.sqlite3")
                adapter = FoundationToolAdapter(None,memory=memory)
                with patch.dict(os.environ,{"JARVIS_MEMORY_CORE_ENABLED":"1","JARVIS_BROWSER_CORE_ENABLED":"0",
                                           "JARVIS_COMPUTER_CORE_ENABLED":"0"}), \
                     patch("jarvis_agent.agent_runtime.settings",replace(settings,agent_provider=provider,structured_tracing_enabled=False)), \
                     patch("jarvis_agent.agent_runtime."+cls,return_value=NoLLM()), \
                     patch("jarvis_agent.foundation_tools.build_foundation_tools",return_value=adapter):
                    runtime = build_agent_runtime()
                    self.assertTrue(runtime.run("Mémorise que mon film est Arrival").actions[0].success)
                    runtime.reset()
                    result = runtime.run("Quel est le nom de mon film ?")
                    self.assertEqual(result.actions[0].name,"recall_information")
                    self.assertIn("Arrival",result.text)


if __name__ == "__main__":
    unittest.main()
