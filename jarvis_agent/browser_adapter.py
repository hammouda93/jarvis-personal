"""Optional Playwright/CDP adapter for an explicitly configured browser session.

All Playwright objects live on one worker thread. No browser/profile is launched,
copied or closed. Opaque refs pin a document, frame and observed DOM node.
"""
from __future__ import annotations

import concurrent.futures
import io
import queue
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from .ui_geometry import CaptureGeometry, intersection_over_union
from .ui_observation import UIEntity, utc_now

SELECTOR = ("input:not([type=hidden]),textarea,select,button,a[href],[role],"
            "[contenteditable=true],h1,h2,h3,[aria-live],[role=log] > *,summary,[tabindex]:not([tabindex='-1']),[onclick],canvas")
SNAPSHOT_JS = r"""els => {
  const key = Symbol.for("personal-ai-agent.observation");
  let s = window[key];
  if (!s) {
    s = {nodes:new WeakMap(), next:1, document:Math.random().toString(36).slice(2)};
    Object.defineProperty(window,key,{value:s});
  }
  return els.slice(0,200).map((e,index) => {
    if (!s.nodes.has(e)) s.nodes.set(e,s.next++);
    const r=e.getBoundingClientRect(), style=getComputedStyle(e);
    const labelBy=(e.getAttribute("aria-labelledby")||"").split(/\s+/)
      .map(id=>document.getElementById(id)?.textContent||"").join(" ").trim();
    const label=e.getAttribute("aria-label") || labelBy ||
      [...(e.labels||[])].map(x=>x.textContent).join(" ").trim() ||
      e.getAttribute("placeholder") || e.getAttribute("title") ||
      e.querySelector("svg title")?.textContent || e.querySelector("img")?.alt ||
      ((e.tagName==="INPUT") ? "" : (e.innerText||""));
    let role=e.getAttribute("role");
    if (!role) role=({BUTTON:"button",A:"link",TEXTAREA:"textbox",SELECT:"combobox",
      H1:"heading",H2:"heading",H3:"heading",SUMMARY:"button",CANVAS:"canvas"})[e.tagName] ||
      (e.tagName==="INPUT" ? ({search:"searchbox",checkbox:"checkbox",radio:"radio",
       button:"button",submit:"button"})[e.type]||"textbox" : e.isContentEditable?"textbox":"generic");
    const writable=(["INPUT","TEXTAREA"].includes(e.tagName)||e.isContentEditable)&&!e.readOnly;
    const actionable=writable || ["button","link","checkbox","radio","tab","menuitem",
      "option","combobox"].includes(role) || e.hasAttribute("onclick");
    const log=e.closest('[role=log]');
    return {index,node_id:s.document+":"+s.nodes.get(e),id:e.id||"",role,
      semantic_role:log&&log!==e&&!actionable?'message':'',
      label:label.trim().slice(0,500),value:writable ? (e.type==="password"?"":e.value??e.innerText??"") : null,
      writable,actionable,enabled:!e.disabled && e.getAttribute("aria-disabled")!=="true",
      selected:e.getAttribute("aria-selected")==="true"||e.checked===true,
      focused:document.activeElement===e,
      visible:r.width>0&&r.height>0&&style.visibility!=="hidden"&&style.display!=="none"&&style.opacity!=="0",
      bounds:[r.left,r.top,r.right,r.bottom],
      region:log?(log.getAttribute('aria-label')||'messages'):e.closest("[role=dialog]")?"dialog":e.closest("nav,[role=navigation]")?"navigation":
        e.closest("form")?"form":e.closest("main,[role=main]")?"content":""};
  });
}"""


class BrowserAdapter:
    def __init__(self, endpoint: str, *, timeout_s: float = 20.0, connection_factory=None):
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
            raise ValueError("Configure a valid CDP endpoint explicitly")
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.connection_factory = connection_factory
        self._tasks: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def _call(self, method: str, *args):
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._worker, daemon=True, name="jarvis-browser")
                self._thread.start()
        future = concurrent.futures.Future()
        self._tasks.put((future, method, args))
        try:
            return future.result(timeout=self.timeout_s)
        except concurrent.futures.TimeoutError:
            cancelled = future.cancel()
            raise RuntimeError("browser_call_timeout_before_start" if cancelled else "browser_action_outcome_unknown")

    def _worker(self):
        browser = playwright = None
        pages: dict[str, Any] = {}
        generations: dict[str, int] = {}
        targets: dict[str, dict[str, Any]] = {}
        captures: dict[str, Any] = {}
        sequence = 0
        try:
            while True:
                job = self._tasks.get()
                if job is None:
                    break
                future, method, args = job
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    if browser is None:
                        if self.connection_factory:
                            browser = self.connection_factory()
                        else:
                            from playwright.sync_api import sync_playwright
                            playwright = sync_playwright().start()
                            browser = playwright.chromium.connect_over_cdp(self.endpoint, timeout=8000)
                    current_pages = [p for context in browser.contexts for p in context.pages if not p.is_closed()]
                    for page in current_pages:
                        if page not in pages.values():
                            ref = "page_" + uuid.uuid4().hex[:12]
                            pages[ref] = page
                            generations[ref] = 0
                            def navigated(frame, page_ref=ref):
                                generations[page_ref] += 1
                                for key in list(targets):
                                    if targets[key]["page_ref"] == page_ref:
                                        del targets[key]
                            page.on("framenavigated", navigated)
                    pages = {ref:p for ref,p in pages.items() if p in current_pages}
                    if method == "pages":
                        future.set_result([{"page_ref":ref,"title":p.title(),"url":p.url,
                                            "document_generation":generations[ref]} for ref,p in pages.items()])
                        continue
                    page_ref = str(args[0] or "")
                    if not page_ref:
                        if len(pages) != 1:
                            raise ValueError("Select a unique page_ref from list_browser_pages")
                        page_ref = next(iter(pages))
                    if page_ref not in pages:
                        raise ValueError("stale_or_unknown_page")
                    page = pages[page_ref]
                    # A synchronous read flushes queued navigation events before the freshness check.
                    viewport = page.evaluate("({width:innerWidth,height:innerHeight})")
                    window = {"title":page.title(),"page_ref":page_ref,"url":page.url,
                              "document_generation":generations[page_ref],
                              "bounds":[0,0,viewport["width"],viewport["height"]]}
                    if method == "observe":
                        sequence += 1
                        observation_id = f"bobs{sequence}"
                        for key in list(targets):
                            if targets[key]["page_ref"] == page_ref:
                                del targets[key]
                        controls = []
                        truncated = len(page.frames) > 12
                        for frame_index, frame in enumerate(page.frames[:12]):
                            locator = frame.locator(SELECTOR)
                            items = locator.evaluate_all(SNAPSHOT_JS)
                            count = locator.count()
                            truncated = truncated or count > 200
                            ox = oy = 0
                            if frame != page.main_frame:
                                frame_element = frame.frame_element()
                                bounds = frame_element.bounding_box()
                                if not bounds:
                                    continue
                                inset = frame_element.evaluate("(e)=>({x:e.clientLeft,y:e.clientTop})")
                                ox, oy = bounds["x"]+inset["x"], bounds["y"]+inset["y"]
                            for item in items:
                                if len(controls) >= 140:
                                    truncated = True
                                    break
                                if not item["visible"]:
                                    continue
                                bounds = item["bounds"]
                                bounds = [bounds[0]+ox,bounds[1]+oy,bounds[2]+ox,bounds[3]+oy]
                                if bounds[2] <= 0 or bounds[3] <= 0 or bounds[0] >= viewport["width"] or bounds[1] >= viewport["height"]:
                                    continue
                                ref = f"{observation_id}:e{len(controls)+1}"
                                semantic_locator = None
                                if item["label"] and item["role"] in {"button","textbox","searchbox","link","checkbox",
                                                                     "radio","tab","menuitem","combobox"}:
                                    candidate = frame.get_by_role(item["role"], name=item["label"], exact=True)
                                    if candidate.count() == 1:
                                        semantic_locator = candidate
                                if semantic_locator is None and item["label"] and item["writable"]:
                                    candidate = frame.get_by_label(item["label"], exact=True)
                                    if candidate.count() == 1:
                                        semantic_locator = candidate
                                chosen = semantic_locator or locator.nth(item["index"])
                                targets[ref] = {"locator":chosen,"frame":frame,"item":item,"page_ref":page_ref,
                                                "generation":generations[page_ref]}
                                controls.append({
                                    "ref":ref,"type":item["role"],"name":item["label"],"id":f"frame{frame_index}:{item['node_id']}",
                                    "semantic_role":item["semantic_role"],
                                    "value":item["value"],"writable":item["writable"],"actionable":item["actionable"],
                                    "enabled":item["enabled"],"selected":item["selected"],"focused":item["focused"],
                                    "visible":True,"bounds":bounds,"region":item["region"],
                                    "frame_ref":f"{page_ref}:frame{frame_index}",
                                })
                        ax = []
                        try:
                            cdp = page.context.new_cdp_session(page)
                            raw_ax = cdp.send("Accessibility.getFullAXTree")
                            ax = [{"role":x.get("role",{}).get("value"),"name":x.get("name",{}).get("value"),
                                   "backend_node_id":x.get("backendDOMNodeId")}
                                  for x in raw_ax.get("nodes",[]) if not x.get("ignored")][:180]
                            cdp.detach()
                        except Exception:
                            pass
                        payload = {
                            "observation_id":observation_id,"captured_at":utc_now(),"window":window,
                            "browser":True,"sensor":"dom","controls":controls,"accessibility_tree":ax,
                            "capabilities":{"writable":[{"ref":x["ref"]} for x in controls if x["writable"]],
                                            "actionable":[{"ref":x["ref"]} for x in controls if x["actionable"]]},
                            "snapshot":{"semantic_coverage":"usable" if controls else "insufficient",
                                        "truncated":truncated,"tree_complete":False},
                        }
                        # Text snapshot is bounded, read-only, and treated as page data.
                        payload["visible_text"] = page.locator("body").inner_text(timeout=2000)[:8000].splitlines()[:100]
                        future.set_result(payload)
                    elif method == "capture":
                        from PIL import Image
                        image = Image.open(io.BytesIO(page.screenshot(type="png", scale="css"))).convert("RGB")
                        crop_arg = args[1]
                        geometry = CaptureGeometry(tuple(window["bounds"]), image.width, image.height)
                        if crop_arg:
                            physical = geometry.box_to_screen(crop_arg)
                            image = image.crop(tuple(int(round(x)) for x in physical))
                            geometry = CaptureGeometry(tuple(window["bounds"]), image.width, image.height, physical)
                        capture_id = "browser_capture_" + uuid.uuid4().hex[:12]
                        captures[capture_id] = (image.copy(), window.copy(), geometry)
                        if len(captures) > 4:
                            del captures[next(iter(captures))]
                        output = io.BytesIO()
                        image.save(output,format="PNG")
                        future.set_result((output.getvalue(),{**window,"captured_width":image.width,
                                            "captured_height":image.height,"capture_id":capture_id,
                                            "coordinate_space":"viewport_css",
                                            "captured_at":utc_now(),"monotonic_at":time.monotonic(),"crop":list(geometry.crop) if geometry.crop else None}))
                    elif method == "act":
                        ref, operation, options = args[1:]
                        if ref not in targets:
                            raise ValueError("stale_browser_ref")
                        record = targets[ref]
                        if record["page_ref"] != page_ref or record["generation"] != generations[page_ref]:
                            raise ValueError("stale_browser_document")
                        locator, original = record["locator"], record["item"]
                        if locator.count() != 1:
                            raise ValueError("browser_target_not_unique")
                        check = locator.evaluate("(e)=>("+SNAPSHOT_JS+ ")([e])[0]")
                        if check["node_id"] != original["node_id"] or check["role"] != original["role"] or check["label"] != original["label"]:
                            raise ValueError("browser_target_changed")
                        if not check["visible"] or not check["enabled"]:
                            raise ValueError("browser_target_not_actionable")
                        verified = False
                        value = None
                        if operation == "write":
                            if not check["writable"]:
                                raise ValueError("browser_target_not_editable")
                            text = str(options.get("text") or "")
                            mode = options.get("mode") or "replace"
                            previous = check["value"] or ""
                            if mode == "replace":
                                locator.fill(text,timeout=5000)
                            elif mode == "append":
                                locator.fill(previous+text,timeout=5000)
                            elif mode == "insert":
                                locator.focus()
                                page.keyboard.insert_text(text)
                            else:
                                raise ValueError("invalid_write_mode")
                            value = locator.evaluate("(e)=>e.value??e.innerText??''")
                            verified = value == text if mode == "replace" else value == previous+text if mode == "append" else text in value
                        elif operation == "click":
                            locator.click(timeout=5000)
                        elif operation == "scroll":
                            locator.hover(timeout=5000)
                            page.mouse.wheel(0,-500 if options.get("direction")=="up" else 500)
                        elif operation == "key":
                            locator.press(str(options.get("key") or ""),timeout=5000)
                        else:
                            raise ValueError("unsupported_browser_action")
                        for key in list(targets):
                            if targets[key]["page_ref"] == page_ref:
                                del targets[key]
                        future.set_result({"verified":verified,"value":value,"source_ref":ref,
                                           "page_ref":page_ref,"document_generation":generations[page_ref]})
                    elif method == "act_visual":
                        target, operation, options, metadata = args[1:]
                        capture_id = metadata.get("capture_id")
                        if capture_id not in captures:
                            raise ValueError("stale_browser_capture")
                        old, captured_window, geometry = captures[capture_id]
                        if window["page_ref"] != captured_window["page_ref"] or window["document_generation"] != captured_window["document_generation"]:
                            raise ValueError("stale_browser_document")
                        if window["bounds"] != captured_window["bounds"]:
                            raise ValueError("browser_viewport_changed")
                        from PIL import Image,ImageChops,ImageStat
                        fresh = Image.open(io.BytesIO(page.screenshot(type="png", scale="css"))).convert("RGB")
                        bounds = target.bounds
                        offset = geometry.physical_crop
                        old_box = (bounds[0]-offset[0],bounds[1]-offset[1],bounds[2]-offset[0],bounds[3]-offset[1])
                        previous_target = old.crop(tuple(int(round(x)) for x in old_box))
                        fresh_target = fresh.crop(tuple(int(round(x)) for x in bounds))
                        if previous_target.size != fresh_target.size:
                            raise ValueError("stale_browser_target")
                        difference = sum(ImageStat.Stat(ImageChops.difference(previous_target,fresh_target)).mean)/(3*255)
                        if difference >= .12:
                            raise ValueError("browser_target_pixels_changed")
                        x,y=(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2
                        if operation=="scroll":
                            page.mouse.move(x,y)
                            page.mouse.wheel(0,-500 if options.get("direction")=="up" else 500)
                        elif operation=="click":
                            page.mouse.click(x,y)
                        elif operation=="write":
                            page.mouse.click(x,y)
                            focus = page.evaluate("""() => {
                              const e=document.activeElement,r=e?.getBoundingClientRect();
                              return {editable:!!e&&(e.isContentEditable||["INPUT","TEXTAREA"].includes(e.tagName)),
                                bounds:r?[r.left,r.top,r.right,r.bottom]:[]};
                            }""")
                            if not focus["editable"] or intersection_over_union(focus["bounds"],bounds)<.5:
                                raise ValueError("browser_editable_focus_not_proven")
                            if options.get("mode","replace")=="replace":
                                page.keyboard.press("ControlOrMeta+A")
                            elif options.get("mode")=="append":
                                page.keyboard.press("ControlOrMeta+End")
                            page.keyboard.insert_text(str(options.get("text") or ""))
                        else:
                            raise ValueError("unsupported_visual_browser_action")
                        targets.clear()
                        captures.clear()
                        future.set_result({"verified":False,"page_ref":page_ref,"backend":"browser_visual"})
                    else:
                        raise ValueError("Unknown browser adapter operation")
                except Exception as exc:
                    future.set_exception(exc)
        finally:
            if playwright:
                playwright.stop()  # Disconnect our client; never call browser.close().

    def pages(self) -> list[dict[str, Any]]:
        return self._call("pages")

    def observe(self, page_ref: str = "") -> dict[str, Any]:
        return self._call("observe", page_ref)

    def capture(self, page_ref: str, *, crop=None):
        return self._call("capture", page_ref, crop)

    def act(self, page_ref: str, ref: str, operation: str, options: dict[str, Any]):
        return self._call("act", page_ref, ref, operation, options)

    def act_visual(self, page_ref: str, target: UIEntity, operation: str, options: dict[str, Any], metadata: dict[str, Any]):
        return self._call("act_visual", page_ref, target, operation, options, metadata)

    def close(self) -> None:
        self._tasks.put(None)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
