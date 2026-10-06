export const OPERATIONS = new Set(["list_tabs","get_active_tab","activate_tab","navigate",
  "observe_dom","find","click","write","press","back","forward","close_tab","download","verify"]);
let port;
const refs = new Map();
const snapshots = new Map();
let sequence = Promise.resolve();
function checkDeadline(request) {
  if (!Number.isFinite(request.deadline_ms) || Date.now() >= request.deadline_ms)
    throw Error("expired_browser_request");
}
function httpURL(value) {
  const url = new URL(value);
  if (!["http:","https:"].includes(url.protocol) || url.username || url.password)
    throw Error("only_http_https_without_credentials");
  return url.href;
}
const cleanTab = tab => ({tab_id:tab.id, window_id:tab.windowId, active:tab.active,
  title:tab.title || "", url:tab.url || "", status:tab.status});
async function tabOf(id) {
  if (!Number.isInteger(id) || id < 0) throw Error("explicit_tab_id_required");
  return chrome.tabs.get(id);
}
async function injected(tabId) {
  const tab = await tabOf(tabId);
  httpURL(tab.url);
  await chrome.scripting.executeScript({target:{tabId, allFrames:true}, files:["dom_bridge.js"], world:"ISOLATED"});
}
async function domCall(tabId, method, args=[], documentId) {
  return chrome.scripting.executeScript({target:documentId ? {tabId, documentIds:[documentId]} : {tabId, allFrames:true},
    world:"ISOLATED", func:(method,args) => globalThis.__personalAIBridge[method](...args), args:[method,args]});
}
function clearRefs(tabId) { for (const [ref,item] of refs) if (item.tabId === tabId) refs.delete(ref); }
function invalidateSnapshot(tabId) {
  clearRefs(tabId);
  snapshots.delete(tabId);
}
async function observe(tabId) {
  await injected(tabId);
  clearRefs(tabId);
  const frames = await domCall(tabId,"observe");
  const validFrames = (frames || []).filter(frame =>
    frame?.result && Array.isArray(frame.result.controls)
  );
  const controls = [];
  outer: for (const frame of validFrames.slice(0,12)) {
    for (const item of frame.result.controls) {
      if (controls.length >= 400) break outer;
      refs.set(item.ref,{tabId, documentId:frame.documentId, frameId:frame.frameId});
      controls.push({...item, document_id:frame.documentId, frame_id:frame.frameId,
        coordinate_space:"frame_css"});
    }
  }
  const tab = await tabOf(tabId);
  const observation = {observation_id:crypto.randomUUID(), tab:cleanTab(tab), sensor:"dom", controls,
    visible_text:validFrames.map(f=>String(f.result.visible_text || "")).join("\n").slice(0,20000),
    truncated:(frames || []).length>12 || controls.length>=400 ||
      validFrames.some(f=>f.result.truncated),
    tree_complete:false,
    frames_seen:(frames || []).length,
    frames_observed:validFrames.length,
    frames_skipped:Math.max(0,(frames || []).length-validFrames.length)};
  snapshots.set(tabId,observation);
  return observation;
}
async function action(request) {
  const a = request.arguments || {}, op = request.operation;
  checkDeadline(request);
  if (request.version !== 1 || !OPERATIONS.has(op)) throw Error("unsupported_bridge_operation");
  if (op === "list_tabs") return (await chrome.tabs.query({})).map(cleanTab);
  if (op === "get_active_tab") {
    const tabs = await chrome.tabs.query({active:true,lastFocusedWindow:true});
    if (tabs.length !== 1) throw Error("active_tab_not_unique");
    return cleanTab(tabs[0]);
  }
  if (op === "navigate") {
    const url = httpURL(a.url);
    if (a.tab_id !== undefined) await tabOf(a.tab_id);
    checkDeadline(request);
    const tab = a.tab_id === undefined ? await chrome.tabs.create({url}) : await chrome.tabs.update(a.tab_id,{url});
    invalidateSnapshot(tab.id);
    return {tab:cleanTab(tab), dispatched:true, verified:false, postcondition:"navigation_pending"};
  }
  if (op === "download") {
    const url = httpURL(a.url);
    checkDeadline(request);
    return {download_id:await chrome.downloads.download({url, saveAs:false}), dispatched:true,
      verified:false, postcondition:"download_started"};
  }
  if (op === "verify" && Number.isInteger(a.download_id)) {
    const items = await chrome.downloads.search({id:a.download_id});
    return {verified:items.length === 1 && items[0].state === "complete" && !items[0].error,
      download_id:a.download_id, state:items[0]?.state, postcondition:"download_complete"};
  }
  const tab = await tabOf(a.tab_id);
  if (op === "activate_tab") {
    checkDeadline(request);
    await chrome.tabs.update(tab.id,{active:true});
    await chrome.windows.update(tab.windowId,{focused:true});
    const [after,win] = await Promise.all([chrome.tabs.get(tab.id), chrome.windows.get(tab.windowId)]);
    return {tab:cleanTab(after), verified:after.active && win.focused, postcondition:"tab_focus"};
  }
  if (op === "close_tab") {
    checkDeadline(request);
    await chrome.tabs.remove(tab.id); invalidateSnapshot(tab.id);
    const all = await chrome.tabs.query({});
    return {verified:!all.some(t=>t.id === tab.id), postcondition:"tab_absent", tabs:all.map(cleanTab)};
  }
  if (["back","forward"].includes(op)) {
    checkDeadline(request);
    await (op === "back" ? chrome.tabs.goBack(tab.id) : chrome.tabs.goForward(tab.id));
    invalidateSnapshot(tab.id);
    return {dispatched:true, verified:false, postcondition:"history_navigation_pending"};
  }
  if (["observe_dom","find","verify"].includes(op)) {
    const observation = (
      op === "verify"
        ? await observe(tab.id)
        : (snapshots.get(tab.id) || await observe(tab.id))
    );
    if (op === "observe_dom") return observation;
    if (op === "verify") {
      if (!["url","title","text"].some(k => Object.hasOwn(a,k))) throw Error("explicit_postcondition_required");
      const checks = Object.entries(a).filter(([k])=>["url","title","text"].includes(k)).map(([k,v]) =>
        k === "text" ? observation.visible_text.includes(String(v)) : observation.tab[k] === String(v));
      return {verified:checks.every(Boolean), postcondition:{url:a.url,title:a.title,text:a.text}, observation};
    }
    if (!String(a.text || "").trim() && !a.type) throw Error("find_requires_text_or_type");
    const norm = text => String(text).normalize("NFKC").trim().toLocaleLowerCase();
    const matches = observation.controls.filter(e => {
      const requestedType = norm(a.type || "");
      const typeMatches = !requestedType || norm(e.type) === requestedType ||
        (requestedType === "input" && Boolean(e.writable));
      const names = [e.text,e.name,e.placeholder,e.aria_label,e.value]
        .map(norm).filter(Boolean);
      const wanted = norm(a.text || "");
      const textMatches = !wanted || (
        a.exact !== false
          ? names.some(value => value === wanted)
          : names.some(value => value.includes(wanted))
      );
      return typeMatches && textMatches;
    });
    return {matches, unique:matches.length === 1, observation_id:observation.observation_id};
  }
  const target = refs.get(a.ref);
  if (!target || target.tabId !== tab.id) throw Error("stale_or_cross_tab_browser_ref");
  if (op === "click" && target.frameId === 0) {
    await chrome.debugger.attach({tabId:tab.id},"1.3");
    let attempted = false;
    try {
      const result = await domCall(tab.id,"preparePointer",[a.ref],target.documentId);
      const point = result[0]?.result;
      if (!Number.isFinite(point?.x) || !Number.isFinite(point?.y)) throw Error("invalid_browser_pointer_target");
      checkDeadline(request);
      invalidateSnapshot(tab.id);
      await domCall(tab.id,"invalidate",[],target.documentId);
      attempted = true;
      await chrome.debugger.sendCommand({tabId:tab.id},"Input.dispatchMouseEvent",
        {type:"mousePressed",button:"left",clickCount:1,...point});
      await chrome.debugger.sendCommand({tabId:tab.id},"Input.dispatchMouseEvent",
        {type:"mouseReleased",button:"left",clickCount:1,...point});
      return {dispatched:true,verified:false,trusted:true,dispatch_method:"cdp_pointer",
        postcondition:"click_dispatched_requires_verify"};
    } catch(error) {
      if (attempted) throw Error("browser_outcome_unknown_do_not_retry: " + error.message);
      throw error;
    } finally { await chrome.debugger.detach({tabId:tab.id}).catch(()=>{}); }
  }
  if (op === "press") {
    const keys = {Enter:{key:"Enter",code:"Enter",windowsVirtualKeyCode:13},
      Tab:{key:"Tab",code:"Tab",windowsVirtualKeyCode:9},
      Escape:{key:"Escape",code:"Escape",windowsVirtualKeyCode:27},
      ArrowDown:{key:"ArrowDown",code:"ArrowDown",windowsVirtualKeyCode:40},
      ArrowUp:{key:"ArrowUp",code:"ArrowUp",windowsVirtualKeyCode:38}};
    if (!keys[a.key]) throw Error("unsupported_browser_key");
    // debugger attach can show a Chrome permission bar. Never fall back to OS keys.
    await chrome.debugger.attach({tabId:tab.id},"1.3");
    let attempted = false;
    try {
      await domCall(tab.id,"prepare",[a.ref],target.documentId);
      checkDeadline(request);
      invalidateSnapshot(tab.id);
      await domCall(tab.id,"invalidate",[],target.documentId);
      attempted = true;
      await chrome.debugger.sendCommand({tabId:tab.id},"Input.dispatchKeyEvent",{type:"keyDown",...keys[a.key]});
      await chrome.debugger.sendCommand({tabId:tab.id},"Input.dispatchKeyEvent",{type:"keyUp",...keys[a.key]});
      return {dispatched:true, verified:false, postcondition:"key_dispatched_requires_verify"};
    } catch(error) {
      if (attempted) throw Error("browser_outcome_unknown_do_not_retry: " + error.message);
      throw error;
    } finally { await chrome.debugger.detach({tabId:tab.id}).catch(()=>{}); }
  }
  checkDeadline(request);
  const results = await domCall(tab.id,"act",[a.ref,op,{...a,deadline_ms:request.deadline_ms}],target.documentId);
  invalidateSnapshot(tab.id);
  const result = results[0].result;
  if (op === "write" && result?.verified === true) {
    result.post_observation = await observe(tab.id);
  }
  return result;
}
export {action};
function connect() {
  if (port) return;
  const connection = chrome.runtime.connectNative("com.jarvis.personal_ai_browser_bridge");
  port = connection;
  connection.postMessage({hello:1, version:1});
  connection.onMessage.addListener(request => {
    sequence = sequence.then(async () => {
      let reply;
      try { reply = {id:request.id,ok:true,result:await action(request)}; }
      catch (error) { reply = {id:request.id,ok:false,error:String(error.message || error)}; }
      if (port === connection) connection.postMessage(reply);
    }).catch(error => console.error("Bridge protocol",error));
  });
  connection.onDisconnect.addListener(() => {
    console.warn("Native host disconnected",chrome.runtime.lastError?.message);
    if (port === connection) port = undefined;
    refs.clear();
    snapshots.clear();
    // User clicks the extension to reconnect; pending mutations are never retried.
  });
}
chrome.action.onClicked.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.tabs.onRemoved.addListener(invalidateSnapshot);
chrome.tabs.onUpdated.addListener((id,change)=> {
  if (change.status === "loading" || change.url) invalidateSnapshot(id);
});
