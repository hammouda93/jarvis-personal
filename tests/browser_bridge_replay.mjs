/* Synthetic Chrome API/DOM replays. These are not real Chrome acceptance. */
import {test, beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';

globalThis.crypto ??= webcrypto;
const tabs = new Map();
const operations = [];
let domItems = [];
let observed = new Map();
let failMouseRelease = false;
let includeNullFrame = false;
let invalidPointer = false;
let pointerPrepError = "";
let navigationAfterInputUrl = "";
let historyBackUrl = "";
let historyForwardUrl = "";
let nullDomMutationResult = false;
let domFallbackNavigationUrl = "";
let clearFocusedValueAfterInput = false;
let structuralControlsAfterInput = null;
let preparedRichWrite = null;
const event = () => {
  const listeners = [];
  return {
    addListener(fn){listeners.push(fn)},
    dispatch(...args){for(const fn of listeners) fn(...args)}
  };
};
const tabRemovedEvent = event();
const tabUpdatedEvent = event();
globalThis.chrome = {
  tabs:{
    async query(filter){return [...tabs.values()].filter(t=>!filter.active || t.active)},
    async get(id){if(!tabs.has(id)) throw Error('unknown_tab');return {...tabs.get(id)}},
    async create({url}){const tab={id:100+tabs.size,windowId:9,active:true,title:'New',url};tabs.set(tab.id,tab);return tab},
    async update(id,change){Object.assign(tabs.get(id),change); operations.push(['tab_update',id,change]); return tabs.get(id)},
    async remove(id){operations.push(['remove',id]);tabs.delete(id)},
    async goBack(id){
      operations.push(['back',id]);
      if(historyBackUrl) tabs.get(id).url=historyBackUrl;
    },
    async goForward(id){
      operations.push(['forward',id]);
      if(historyForwardUrl) tabs.get(id).url=historyForwardUrl;
    },
    onRemoved:tabRemovedEvent,onUpdated:tabUpdatedEvent
  },
  windows:{async update(id,args){operations.push(['window_update',id,args])},async get(id){return{id,focused:true}}},
  scripting:{async executeScript(options){
    operations.push(['scripting',options.target]);
    if(options.files) return [];
    const [method,args] = options.args;
    if(method === 'observe'){
      const controls=domItems.map(item=>({...item,ref:crypto.randomUUID()}));
      observed = new Map(controls.map(c=>[c.ref,c]));
      const frames=[{documentId:'doc-'+options.target.tabId,frameId:0,result:{controls,visible_text:'Observed header',truncated:false}}];
      if(includeNullFrame) frames.push({documentId:'opaque-frame',frameId:7,result:null});
      return frames;
    }
    if(method === 'prepare'){
      if(!observed.has(args[0])) throw Error('stale_browser_ref');
      return [{result:{type:'textbox'}}];
    }
    if(method === 'prepareWrite'){
      if(!observed.has(args[0])) throw Error('stale_browser_ref');
      const item=observed.get(args[0]);
      preparedRichWrite={
        mode:String(args[1]||'replace'),
        previous:String(item.value??'')
      };
      return [{result:{
        type:item.type||'textbox',
        editable_kind:item.editable_kind||'contenteditable',
        value:String(item.value??'')
      }}];
    }
    if(method === 'preparePointer'){
      if(!observed.has(args[0])) throw Error('stale_browser_ref');
      if(pointerPrepError) throw Error(pointerPrepError);
      return [{result:invalidPointer?{}:{x:40,y:15}}];
    }
    if(method === 'invalidate'){observed.clear();return []}
    if(method === 'act'){
      if(!observed.has(args[0])) throw Error('stale_browser_ref');
      operations.push(['dom_action',options.target.tabId,args]);
      if(domFallbackNavigationUrl && args[1] === 'click')
        tabs.get(options.target.tabId).url=domFallbackNavigationUrl;
      observed.clear();
      if(nullDomMutationResult) return [{result:null}];
      return [{result:{
        dispatched:true,
        verified:['write','select'].includes(args[1]),
        ...(args[1] === 'select' ? {
          postcondition:'selected_value',
          value:String(args[2]?.text ?? ''),
          selected_text:String(args[2]?.text ?? ''),
        } : {})
      }}];
    }
    throw Error('unexpected_dom_method');
  }},
  debugger:{async attach(target){operations.push(['attach',target])},
    async sendCommand(target,method,args){
      operations.push(['cdp',target,method,args]);
      if(failMouseRelease && args.type==='mouseReleased') throw Error('transport_lost');
      if(
        navigationAfterInputUrl &&
        (
          args.type==='mouseReleased' ||
          (args.type==='keyUp' && args.key==='Enter')
        )
      ) {
        tabs.get(target.tabId).url=navigationAfterInputUrl;
      }
      if(
        clearFocusedValueAfterInput &&
        (
          args.type==='mouseReleased' ||
          (args.type==='keyUp' && args.key==='Enter')
        )
      ) {
        domItems=domItems.map(item =>
          item.focused && item.writable ? {...item,value:''} : item
        );
      }
      if(method==='Input.insertText' && preparedRichWrite){
        const text=String(args.text??'');
        domItems=domItems.map(item =>
          item.editable_kind==='contenteditable'
            ? {
                ...item,
                value:preparedRichWrite.mode==='append'
                  ? preparedRichWrite.previous+text
                  : text
              }
            : item
        );
      }
      if(
        structuralControlsAfterInput &&
        (
          args.type==='mouseReleased' ||
          (args.type==='keyUp' && args.key==='Enter')
        )
      ) {
        domItems=structuralControlsAfterInput.map(item=>({...item}));
      }
    },
    async detach(target){operations.push(['detach',target])}},
  downloads:{async download(args){operations.push(['download',args]);return 77},
    async search({id}){return id===77?[{id,state:'complete'}]:[]}},
  action:{onClicked:event()},runtime:{onStartup:event(),onInstalled:event()}
};
const source = fs.readFileSync('extensions/personal-ai-browser-bridge/service_worker.js','utf8');
const {action,OPERATIONS} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
function request(operation,args={}){return action({id:crypto.randomUUID(),version:1,operation,arguments:args,deadline_ms:Date.now()+5000})}
beforeEach(()=>{
  // Simulate Chrome tearing down the tabs from the previous test so the
  // service worker invalidates its persistent refs/snapshots too.
  for (const id of [...tabs.keys()]) tabRemovedEvent.dispatch(id,{});
  tabs.clear();operations.length=0;observed.clear();
  failMouseRelease=false;
  includeNullFrame=false;
  invalidPointer=false;
  pointerPrepError="";
  navigationAfterInputUrl="";
  historyBackUrl="";
  historyForwardUrl="";
  nullDomMutationResult=false;
  domFallbackNavigationUrl="";
  clearFocusedValueAfterInput=false;
  structuralControlsAfterInput=null;
  preparedRichWrite=null;
  tabs.set(1,{id:1,windowId:9,active:true,title:'Browser One',url:'https://one.example/'});
  tabs.set(2,{id:2,windowId:10,active:false,title:'Browser Two',url:'https://two.example/'});
  domItems=[{text:'Search',type:'searchbox',writable:true,bbox:[1,2,80,30]},
            {text:'Exact Person',type:'link',bbox:[1,40,80,60]},
            {text:'Exact Person Other',type:'link',bbox:[1,70,100,90]}];
});
test('all required generic primitives are present',()=>{
  assert.equal(OPERATIONS.size,15);
});
test('several tabs and activate target in different window',async()=>{
  assert.equal((await request('list_tabs')).length,2);
  assert.equal((await request('get_active_tab')).tab_id,1);
  const result=await request('activate_tab',{tab_id:2});
  assert.equal(result.verified,true);
  assert(operations.some(x=>x[0]==='window_update'&&x[1]===10));
});
test('exact find avoids contact/result prefix confusion',async()=>{
  const result=await request('find',{tab_id:1,text:'Exact Person',exact:true});
  assert.equal(result.matches.length,1);
  assert.equal(result.matches[0].text,'Exact Person');
});
test('find filters the current snapshot without invalidating its refs',async()=>{
  const observation=await request('observe_dom',{tab_id:1});
  const ref=observation.controls[1].ref;
  const found=await request('find',{tab_id:1,text:'Exact Person',exact:true});
  assert.equal(found.observation_id,observation.observation_id);
  assert.equal(found.matches[0].ref,ref);
  const click=await request('click',{tab_id:1,ref});
  assert.equal(click.dispatched,true);
});
test('find can filter observed links by href semantics',async()=>{
  domItems=[
    {text:'Result A',name:'Result A',type:'link',
     href:'https://one.example/watch?id=123',bbox:[1,2,120,30]},
    {text:'Result B',name:'Result B',type:'link',
     href:'https://one.example/article?id=456',bbox:[1,40,120,70]}
  ];
  const result=await request('find',{
    tab_id:1,
    text:'watch',
    type:'link',
    exact:false
  });
  assert.equal(result.matches.length,1);
  assert.equal(result.matches[0].text,'Result A');
});
test('find can filter observed select options without site rules',async()=>{
  domItems=[{
    text:'Status',name:'Status',type:'combobox',tag:'select',
    selectable:true,actionable:true,value:'pending',
    options:[
      {text:'Pending',value:'pending',selected:true,disabled:false},
      {text:'Livré',value:'delivered',selected:false,disabled:false}
    ],
    bbox:[1,2,120,30]
  }];
  const result=await request('find',{
    tab_id:1,
    text:'Livré',
    type:'combobox',
    exact:true
  });
  assert.equal(result.matches.length,1);
  assert.equal(result.matches[0].name,'Status');
});

test('partial or opaque frames do not invalidate the whole DOM observation',async()=>{
  includeNullFrame=true;
  const result=await request('observe_dom',{tab_id:1});
  assert.equal(result.controls.length,3);
  assert.equal(result.frames_seen,2);
  assert.equal(result.frames_observed,1);
  assert.equal(result.frames_skipped,1);
});
test('generic input alias can find an observed writable field without site rules',async()=>{
  const result=await request('find',{tab_id:1,type:'input'});
  assert.equal(result.matches.length,1);
  assert.equal(result.matches[0].type,'searchbox');
});
test('cross-tab ref is rejected before any mutation',async()=>{
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  await assert.rejects(request('write',{tab_id:2,ref,text:'wrong'}),/cross_tab/);
  assert(!operations.some(x=>x[0]==='dom_action'));
});
test('read-only reobservation reuses snapshot; mutation expires old refs',async()=>{
  const first=await request('observe_dom',{tab_id:1});
  const ref=first.controls[0].ref;
  const second=await request('observe_dom',{tab_id:1});
  assert.equal(second.observation_id,first.observation_id);
  assert.equal(second.controls[0].ref,ref);
  const write=await request('write',{tab_id:1,ref,text:'hello'});
  assert.equal(write.verified,true);
  await assert.rejects(request('write',{tab_id:1,ref,text:'again'}),/cross_tab/);
  assert.notEqual(write.post_observation.observation_id,first.observation_id);
});
test('Chrome Input is targeted by tabId regardless of nonbrowser OS focus',async()=>{
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('press',{tab_id:1,ref,key:'Enter'});
  assert.equal(result.verified,false);
  assert(result.post_observation);
  const inputs=operations.filter(x=>x[0]==='cdp');
  assert.equal(inputs.length,2);
  assert(inputs.every(x=>x[1].tabId===1));
});
test('Space is a generic tab-scoped browser key',async()=>{
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('press',{tab_id:1,ref,key:'Space'});
  assert.equal(result.dispatched,true);
  const inputs=operations.filter(x=>x[0]==='cdp');
  assert.equal(inputs.length,2);
  assert.equal(inputs[0][3].code,'Space');
  assert.equal(inputs[0][3].key,' ');
});
test('top frame click uses trusted Chrome Input bound to the observed tab',async()=>{
  const ref=(await request('observe_dom',{tab_id:1})).controls[1].ref;
  const result=await request('click',{tab_id:1,ref});
  assert.equal(result.trusted,true);
  assert.equal(result.verified,false);
  assert(result.post_observation);
  assert.equal(result.post_observation.tab.tab_id,1);
  const inputs=operations.filter(x=>x[0]==='cdp');
  assert.deepEqual(inputs.map(x=>x[3].type),['mousePressed','mouseReleased']);
  assert(inputs.every(x=>x[1].tabId===1));
  assert(!operations.some(x=>x[0]==='dom_action'));
});
test('invalid pointer prep falls back to bounded DOM click without site rules',async()=>{
  invalidPointer=true;
  const ref=(await request('observe_dom',{tab_id:1})).controls[1].ref;
  const result=await request('click',{tab_id:1,ref});
  assert.equal(result.dispatched,true);
  assert.equal(result.trusted,false);
  assert.equal(result.dispatch_method,'dom_click_fallback');
  assert(result.post_observation);
  assert(operations.some(x=>x[0]==='dom_action'&&x[2][1]==='click'));
  assert.equal(operations.filter(x=>x[0]==='cdp').length,0);
});
test('lost DOM click result is still verified when fresh navigation proves it',async()=>{
  invalidPointer=true;
  nullDomMutationResult=true;
  domFallbackNavigationUrl='https://one.example/dom-fallback-target';
  const ref=(await request('observe_dom',{tab_id:1})).controls[1].ref;
  const result=await request('click',{tab_id:1,ref});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,true);
  assert.equal(result.postcondition,'navigation_observed');
  assert.equal(result.dispatch_method,'dom_click_fallback');
  assert.equal(result.post_observation.tab.url,domFallbackNavigationUrl);
});
test('lost DOM click result stays explicit unknown when no effect can be proved',async()=>{
  invalidPointer=true;
  nullDomMutationResult=true;
  const ref=(await request('observe_dom',{tab_id:1})).controls[1].ref;
  const result=await request('click',{tab_id:1,ref});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,false);
  assert.equal(result.outcome_unknown,true);
  assert.equal(result.postcondition,'click_outcome_unknown_requires_verify');
});

test('changed or stale link target never falls through to observed href navigation',async()=>{
  pointerPrepError='browser_target_changed';
  domItems=[{
    text:'Observed result',name:'Observed result',type:'link',tag:'a',
    href:'https://one.example/old-target',actionable:true,
    bbox:[1,2,180,30]
  }];
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;

  await assert.rejects(
    request('click',{tab_id:1,ref}),
    /browser_target_changed/
  );
  assert.equal(tabs.get(1).url,'https://one.example/');
  assert(!operations.some(x =>
    x[0]==='tab_update' &&
    x[2]?.url==='https://one.example/old-target'
  ));
});

test('unavailable pointer on an observed link navigates only to its observed href',async()=>{
  invalidPointer=true;
  domItems=[{
    text:'Observed result',name:'Observed result',type:'link',tag:'a',
    href:'https://one.example/observed-target',actionable:true,
    bbox:[1,2,180,30]
  }];
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('click',{tab_id:1,ref});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,true);
  assert.equal(result.postcondition,'navigation_observed');
  assert.equal(result.dispatch_method,'observed_href_navigation');
  assert.equal(tabs.get(1).url,'https://one.example/observed-target');
  assert(!operations.some(x=>x[0]==='dom_action'));
});
test('same-URL SPA transition is verified only with a strong structural delta',async()=>{
  domItems=[
    {text:'Search',name:'Search',placeholder:'Search',type:'textbox',tag:'input',
     writable:true,actionable:true,focused:true,value:'person',region:'content',
     bbox:[10,10,200,40]}
  ];
  structuralControlsAfterInput=[
    {text:'Search',name:'Search',placeholder:'Search',type:'textbox',tag:'input',
     writable:true,actionable:true,focused:false,value:'person',region:'content',
     bbox:[10,10,200,40]},
    {text:'Message',name:'Message',placeholder:'Message',type:'textbox',tag:'div',
     editable_kind:'contenteditable',writable:true,actionable:true,focused:false,
     value:'',region:'content',bbox:[250,500,650,550]},
    {text:'Send',name:'Send',type:'button',tag:'button',
     writable:false,actionable:true,focused:false,value:null,region:'content',
     bbox:[660,500,710,550]}
  ];
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('press',{tab_id:1,ref,key:'Enter'});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,true);
  assert.equal(result.postcondition,'structural_transition_observed');
  assert.equal(result.structural_delta.new_writable,1);
  assert.equal(tabs.get(1).url,'https://one.example/');
});
test('small dynamic DOM noise is not accepted as a structural transition',async()=>{
  domItems=[
    {text:'Search',name:'Search',placeholder:'Search',type:'textbox',tag:'input',
     writable:true,actionable:true,focused:true,value:'person',region:'content',
     bbox:[10,10,200,40]}
  ];
  structuralControlsAfterInput=[
    {text:'Search',name:'Search',placeholder:'Search',type:'textbox',tag:'input',
     writable:true,actionable:true,focused:true,value:'person',region:'content',
     bbox:[10,10,200,40]},
    {text:'Loading',name:'Loading',type:'button',tag:'button',
     writable:false,actionable:true,region:'content',bbox:[300,10,360,40]}
  ];
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('press',{tab_id:1,ref,key:'Enter'});
  assert.equal(result.verified,false);
  assert.equal(result.postcondition,'key_dispatched_requires_verify');
});

test('fresh URL change is strong generic proof for click navigation',async()=>{
  navigationAfterInputUrl='https://one.example/opened-result';
  const ref=(await request('observe_dom',{tab_id:1})).controls[1].ref;
  const result=await request('click',{tab_id:1,ref});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,true);
  assert.equal(result.postcondition,'navigation_observed');
  assert.equal(result.post_observation.tab.url,navigationAfterInputUrl);
});
test('fresh URL change is strong generic proof for Enter submission',async()=>{
  navigationAfterInputUrl='https://one.example/results?q=test';
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('press',{tab_id:1,ref,key:'Enter'});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,true);
  assert.equal(result.postcondition,'navigation_observed');
  assert.equal(result.post_observation.tab.url,navigationAfterInputUrl);
});
test('Enter is verified when the observed writable target is consumed',async()=>{
  clearFocusedValueAfterInput=true;
  domItems=[
    {text:'Message',name:'Message',type:'textbox',tag:'div',writable:true,
     actionable:true,focused:true,value:'draft',bbox:[1,2,180,30]}
  ];
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('press',{tab_id:1,ref,key:'Enter'});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,true);
  assert.equal(result.postcondition,'editable_value_cleared');
  assert.equal(result.target_after.value,'');
});
test('button click is verified when the focused writable field is consumed',async()=>{
  clearFocusedValueAfterInput=true;
  domItems=[
    {text:'Message',name:'Message',type:'textbox',tag:'div',writable:true,
     actionable:true,focused:true,value:'draft',bbox:[1,2,180,30]},
    {text:'Send',name:'Send',type:'button',tag:'button',writable:false,
     actionable:true,focused:false,value:null,bbox:[190,2,240,30]}
  ];
  const observation=await request('observe_dom',{tab_id:1});
  const ref=observation.controls[1].ref;
  const result=await request('click',{tab_id:1,ref});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,true);
  assert.equal(result.postcondition,'focused_editable_value_cleared');
  assert.equal(result.submitted_field_after.value,'');
});
test('in-place action remains unverified when no observable state changes',async()=>{
  domItems=[
    {text:'Message',name:'Message',type:'textbox',tag:'div',writable:true,
     actionable:true,focused:true,value:'draft',bbox:[1,2,180,30]}
  ];
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('press',{tab_id:1,ref,key:'Enter'});
  assert.equal(result.verified,false);
  assert.equal(result.postcondition,'key_dispatched_requires_verify');
});

test('history navigation is verified only when the observed URL changes',async()=>{
  historyBackUrl='https://one.example/previous';
  const back=await request('back',{tab_id:1});
  assert.equal(back.verified,true);
  assert.equal(back.postcondition,'navigation_observed');
  historyForwardUrl='';
  const forward=await request('forward',{tab_id:1});
  assert.equal(forward.verified,false);
  assert.equal(forward.postcondition,'history_navigation_pending');
});

test('partial pointer failure reports unknown outcome and consumes reference',async()=>{
  const ref=(await request('observe_dom',{tab_id:1})).controls[1].ref;
  failMouseRelease=true;
  await assert.rejects(request('click',{tab_id:1,ref}),/outcome_unknown/);
  await assert.rejects(request('click',{tab_id:1,ref}),/cross_tab/);
  assert.equal(operations.filter(x=>x[0]==='cdp').length,2);
});
test('expired action never starts',async()=>{
  await assert.rejects(action({version:1,operation:'close_tab',arguments:{tab_id:1},deadline_ms:0}),/expired/);
  assert(tabs.has(1));
});
test('close only selected tab; history and verification are explicit',async()=>{
  await request('back',{tab_id:1});await request('forward',{tab_id:1});
  const proof=await request('verify',{tab_id:1,text:'missing header'});
  assert.equal(proof.verified,false);
  const closed=await request('close_tab',{tab_id:1});
  assert.equal(closed.verified,true);assert(tabs.has(2));
  await assert.rejects(request('verify',{tab_id:2}),/postcondition/);
});
test('internal and credentialed URLs are refused',async()=>{
  for(const url of ['file:///C:/private','chrome://settings','https://user:secret@example.com'])
    await assert.rejects(request('navigate',{url}),/http_https/);
});
test('download dispatch requires separate completion proof',async()=>{
  const start=await request('download',{url:'https://one.example/file.txt'});
  assert.equal(start.verified,false);
  assert.equal((await request('verify',{download_id:77})).verified,true);
  assert.equal((await request('verify',{download_id:99})).verified,false);
});
test('targeted verify follows a fresh ref and checks the control value',async()=>{
  domItems=[{
    text:'Message',name:'Message',placeholder:'Message',type:'textbox',tag:'div',
    editable_kind:'contenteditable',writable:true,actionable:true,focused:false,
    value:'draft',region:'content',bbox:[10,20,250,60]
  }];
  const observation=await request('observe_dom',{tab_id:1});
  const ref=observation.controls[0].ref;

  domItems=domItems.map(item=>({...item,value:'expected text'}));
  const proof=await request('verify',{
    tab_id:1,
    ref,
    expected_value:'expected text'
  });

  assert.equal(proof.verified,true);
  assert.equal(proof.postcondition,'target_value');
  assert.equal(proof.value,'expected text');
  assert(proof.target_after);
  assert.notEqual(proof.target_after.ref,ref);
});
test('targeted verify rejects a different control value',async()=>{
  domItems=[{
    text:'Message',name:'Message',placeholder:'Message',type:'textbox',tag:'div',
    editable_kind:'contenteditable',writable:true,actionable:true,focused:false,
    value:'draft',region:'content',bbox:[10,20,250,60]
  }];
  const observation=await request('observe_dom',{tab_id:1});
  const ref=observation.controls[0].ref;

  const proof=await request('verify',{
    tab_id:1,
    ref,
    expected_value:'something else'
  });

  assert.equal(proof.verified,false);
  assert.equal(proof.postcondition,'target_value');
  assert.equal(proof.value,'draft');
});

test('missing DOM write result never becomes silent success',async()=>{
  nullDomMutationResult=true;
  const ref=(await request('observe_dom',{tab_id:1})).controls[0].ref;
  const result=await request('write',{tab_id:1,ref,text:'hello'});
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,false);
  assert.equal(result.outcome_unknown,true);
  assert.equal(result.postcondition,'write_outcome_unknown_requires_verify');
  assert(result.post_observation);
});
test('verified select is a first-class generic browser mutation',async()=>{
  domItems=[{
    text:'Status',name:'Status',type:'combobox',tag:'select',
    selectable:true,actionable:true,value:'pending',
    options:[
      {text:'Pending',value:'pending',selected:true,disabled:false},
      {text:'Delivered',value:'delivered',selected:false,disabled:false}
    ],
    bbox:[1,2,120,30]
  }];
  const observation=await request('observe_dom',{tab_id:1});
  const result=await request('select',{
    tab_id:1,
    ref:observation.controls[0].ref,
    text:'delivered'
  });
  assert.equal(result.dispatched,true);
  assert.equal(result.verified,true);
  assert(result.post_observation);
  assert(operations.some(x=>x[0]==='dom_action'&&x[2][1]==='select'));
});
test('top-frame contenteditable write uses Chrome Input and verifies exact target value',async()=>{
  domItems=[{
    text:'Message',name:'Message',placeholder:'Message',type:'textbox',tag:'div',
    editable_kind:'contenteditable',writable:true,actionable:true,focused:false,
    value:'old',region:'content',bbox:[10,20,250,60]
  }];
  const observation=await request('observe_dom',{tab_id:1});
  const result=await request('write',{
    tab_id:1,
    ref:observation.controls[0].ref,
    text:'fresh message',
    mode:'replace'
  });
  assert.equal(result.dispatched,true);
  assert.equal(result.trusted,true);
  assert.equal(result.dispatch_method,'cdp_insert_text');
  assert.equal(result.verified,true);
  assert.equal(result.value,'fresh message');
  assert.equal(result.target_after.editable_kind,'contenteditable');
  assert(operations.some(x=>x[0]==='cdp'&&x[2]==='Input.insertText'));
  assert(!operations.some(x=>x[0]==='dom_action'&&x[2][1]==='write'));
});
test('verified write returns a fresh post-observation snapshot',async()=>{
  const observation=await request('observe_dom',{tab_id:1});
  const ref=observation.controls[0].ref;
  const result=await request('write',{tab_id:1,ref,text:'hello'});
  assert.equal(result.verified,true);
  assert(result.post_observation);
  assert.notEqual(result.post_observation.observation_id,observation.observation_id);
  assert.equal(result.post_observation.tab.tab_id,1);
  assert(result.target_after);
  assert.notEqual(result.target_after.ref,ref);
});

function fakeDOM(){
  class Input {
    constructor(){this.tagName='INPUT';this.type='search';this.attributes={'aria-label':'Search','placeholder':'Search videos'};this.rect={left:5,top:5,right:100,bottom:30,width:95,height:25};
      this.labels=[];this.isConnected=true;this.readOnly=false;this.disabled=false;this._value='';this.events=[];}
    getAttribute(key){return this.attributes[key]??null} querySelector(){return null}
    getBoundingClientRect(){return this.rect} matches(){return true}
    contains(other){return this===other} getRootNode(){return document}
    focus(){document.activeElement=this} click(){this.clicked=true}
    dispatchEvent(e){this.events.push(e.type)}
    get value(){return this._value} set value(v){this._value=v}
  }
  const input=new Input();
  const document={activeElement:null,body:{innerText:'visible'},querySelectorAll(){return[input]},
    getElementById(){return null},elementFromPoint(){return input}};
  const sandbox={document,crypto:webcrypto,HTMLInputElement:Input,HTMLTextAreaElement:class{},innerWidth:300,innerHeight:200,
    getComputedStyle(){return{visibility:'visible',display:'block',opacity:'1'}},Event:class{constructor(type){this.type=type}},
    InputEvent:class{constructor(type){this.type=type}}};
  vm.runInNewContext(fs.readFileSync('extensions/personal-ai-browser-bridge/dom_bridge.js','utf8'),sandbox);
  return{bridge:sandbox.__personalAIBridge,input,document};
}
test('DOM references expose accessible semantics and bind the actual node',()=>{
  const {bridge}=fakeDOM();const item=bridge.observe().controls[0];
  assert.equal(item.type,'searchbox');
  assert.equal(item.semantic_role,'searchbox');
  assert.equal(item.aria_label,'Search');
  assert.equal(item.placeholder,'Search videos');
  assert.equal(item.input_type,'search');
  assert.equal(item.writable,true);
  assert.equal(item.visual_index,1);
  assert.equal(item.dom_index,1);
});
test('bounded DOM activation remains available when pointer hit testing is unavailable',()=>{
  const {bridge,input,document}=fakeDOM();
  document.elementFromPoint=()=>({notTheTarget:true});
  const ref=bridge.observe().controls[0].ref;
  assert.throws(()=>bridge.preparePointer(ref),/occluded/);
  const result=bridge.act(ref,'click',{});
  assert.equal(result.dispatched,true);
  assert.equal(result.trusted,false);
  assert.equal(result.dispatch_method,'dom_click');
  assert.equal(input.clicked,true);
});

test('DOM references bind actual node; write verifies Unicode and expires',()=>{
  const {bridge,input}=fakeDOM();const ref=bridge.observe().controls[0].ref;
  assert.equal(bridge.act(ref,'write',{text:'été عربي'}).verified,true);
  assert.equal(input.value,'été عربي');assert(input.events.includes('input'));
  assert.throws(()=>bridge.act(ref,'write',{text:'late'}),/stale/);
});
function fakeContentEditable(){
  class Editor {
    constructor(){
      this.tagName='DIV';
      this.type=undefined;
      this.attributes={'role':'textbox','aria-label':'Message'};
      this.rect={left:10,top:20,right:250,bottom:60,width:240,height:40};
      this.isConnected=true;this.readOnly=false;this.disabled=false;
      this.isContentEditable=true;this.innerText='Ancien texte';
      this.textContent='Ancien texte';this.events=[];
    }
    getAttribute(key){return this.attributes[key]??null}
    querySelector(){return null}
    getBoundingClientRect(){return this.rect}
    matches(){return true}
    contains(other){return this===other}
    getRootNode(){return document}
    focus(){document.activeElement=this}
    click(){this.clicked=true}
    dispatchEvent(e){this.events.push(e.type);return true}
  }
  const editor=new Editor();
  const selection={
    ranges:[],
    removeAllRanges(){this.ranges=[]},
    addRange(range){this.ranges=[range]}
  };
  const document={
    activeElement:null,
    body:{innerText:'visible'},
    querySelectorAll(){return[editor]},
    getElementById(){return null},
    elementFromPoint(){return editor},
    createRange(){
      return {
        selected:null,collapsed:null,
        selectNodeContents(node){this.selected=node},
        collapse(value){this.collapsed=value},
        deleteContents(){},
        insertNode(){},
        setStartAfter(){}
      };
    },
    execCommand(command,_ui,value){
      if(command!=='insertText') return false;
      this.activeElement.innerText=String(value);
      this.activeElement.textContent=String(value);
      return true;
    }
  };
  const sandbox={
    document,crypto:webcrypto,HTMLInputElement:class{},HTMLTextAreaElement:class{},
    innerWidth:400,innerHeight:300,
    getSelection(){return selection},
    getComputedStyle(){return{visibility:'visible',display:'block',opacity:'1'}},
    Event:class{constructor(type){this.type=type}},
    InputEvent:class{constructor(type){this.type=type}}
  };
  vm.runInNewContext(fs.readFileSync('extensions/personal-ai-browser-bridge/dom_bridge.js','utf8'),sandbox);
  return{bridge:sandbox.__personalAIBridge,editor,selection};
}
function fakeNestedContentEditable(){
  class Placeholder {
    constructor(){this.attributes={'data-placeholder':'Entrer un Message'};this.textContent='';}
    getAttribute(key){return this.attributes[key]??null}
  }
  class RootEditor {
    constructor(){
      this.tagName='DIV';this.type=undefined;
      this.attributes={'contenteditable':'true'};
      this.rect={left:20,top:200,right:360,bottom:250,width:340,height:50};
      this.isConnected=true;this.readOnly=false;this.disabled=false;
      this.isContentEditable=true;this.innerText='draft';this.textContent='draft';
      this.parentElement=null;
    }
    getAttribute(key){return this.attributes[key]??null}
    querySelector(){return null}
    getBoundingClientRect(){return this.rect}
    matches(){return true}
    contains(other){return other===this||other===child}
    getRootNode(){return document}
    focus(){document.activeElement=this}
    click(){}
    dispatchEvent(){return true}
  }
  class ChildTextbox {
    constructor(root){
      this.tagName='P';this.type=undefined;
      this.attributes={'role':'textbox'};
      this.rect={left:22,top:202,right:358,bottom:248,width:336,height:46};
      this.isConnected=true;this.readOnly=false;this.disabled=false;
      this.isContentEditable=true;this.innerText='draft';this.textContent='draft';
      this.parentElement=root;
    }
    getAttribute(key){return this.attributes[key]??null}
    querySelector(){return null}
    getBoundingClientRect(){return this.rect}
    matches(){return true}
    contains(other){return other===this}
    getRootNode(){return document}
    focus(){document.activeElement=this}
    click(){}
    dispatchEvent(){return true}
  }
  const placeholder=new Placeholder();
  const root=new RootEditor();
  const child=new ChildTextbox(root);
  const container={
    isContentEditable:false,
    querySelector(selector){
      return selector.includes('placeholder') ? placeholder : null;
    }
  };
  root.parentElement=container;
  const document={
    activeElement:null,
    body:{innerText:'visible'},
    querySelectorAll(){return[root,child]},
    getElementById(){return null},
    elementFromPoint(){return root}
  };
  const sandbox={
    document,crypto:webcrypto,HTMLInputElement:class{},HTMLTextAreaElement:class{},
    innerWidth:500,innerHeight:400,
    getComputedStyle(){return{visibility:'visible',display:'block',opacity:'1'}},
    Event:class{constructor(type){this.type=type}},
    InputEvent:class{constructor(type){this.type=type}}
  };
  vm.runInNewContext(fs.readFileSync('extensions/personal-ai-browser-bridge/dom_bridge.js','utf8'),sandbox);
  return{bridge:sandbox.__personalAIBridge,root,child};
}
test('nested contenteditable descendants collapse to one stable rich-editor root',()=>{
  const {bridge}=fakeNestedContentEditable();
  const controls=bridge.observe().controls;
  assert.equal(controls.length,1);
  assert.equal(controls[0].editable_kind,'contenteditable');
  assert.equal(controls[0].placeholder,'Entrer un Message');
  assert.equal(controls[0].name,'Entrer un Message');
  assert.equal(controls[0].value,'draft');
});

function fakeSelectDOM(){
  class Option {
    constructor(text,value,selected=false){
      this.textContent=text;this.value=value;this.selected=selected;this.disabled=false;
    }
  }
  class Select {
    constructor(){
      this.tagName='SELECT';this.type='select-one';
      this.attributes={'aria-label':'Status'};
      this.rect={left:10,top:10,right:200,bottom:42,width:190,height:32};
      this.labels=[];this.isConnected=true;this.disabled=false;this.readOnly=false;
      this.events=[];this.options=[
        new Option('Pending','pending',true),
        new Option('Livré','delivered',false)
      ];
    }
    getAttribute(key){return this.attributes[key]??null}
    querySelector(){return null}
    getBoundingClientRect(){return this.rect}
    matches(){return true}
    contains(other){return this===other}
    getRootNode(){return document}
    focus(){document.activeElement=this}
    click(){this.clicked=true}
    dispatchEvent(e){this.events.push(e.type);return true}
    get value(){return this.options.find(x=>x.selected)?.value ?? ''}
    set value(v){
      for(const option of this.options) option.selected=option.value===v;
    }
  }
  const select=new Select();
  const document={
    activeElement:null,
    body:{innerText:'Status'},
    querySelectorAll(){return[select]},
    getElementById(){return null},
    elementFromPoint(){return select}
  };
  const sandbox={
    document,crypto:webcrypto,HTMLInputElement:class{},HTMLTextAreaElement:class{},
    innerWidth:400,innerHeight:300,
    getComputedStyle(){return{visibility:'visible',display:'block',opacity:'1'}},
    Event:class{constructor(type){this.type=type}},
    InputEvent:class{constructor(type){this.type=type}}
  };
  vm.runInNewContext(fs.readFileSync('extensions/personal-ai-browser-bridge/dom_bridge.js','utf8'),sandbox);
  return{bridge:sandbox.__personalAIBridge,select};
}
test('native select exposes options and verifies selected value',()=>{
  const {bridge,select}=fakeSelectDOM();
  const observed=bridge.observe().controls[0];
  assert.equal(observed.type,'combobox');
  assert.equal(observed.selectable,true);
  assert.equal(observed.options.length,2);
  assert.equal(observed.value,'pending');
  const result=bridge.act(observed.ref,'select',{text:'Livré'});
  assert.equal(result.verified,true);
  assert.equal(result.value,'delivered');
  assert.equal(result.selected_text,'Livré');
  assert.equal(select.value,'delivered');
  assert(select.events.includes('change'));
});

test('prepareWrite selects the stable rich-editor root for replacement',()=>{
  const {bridge,editor,selection}=fakeContentEditable();
  const ref=bridge.observe().controls[0].ref;
  const prepared=bridge.prepareWrite(ref,'replace');
  assert.equal(prepared.editable_kind,'contenteditable');
  assert.equal(prepared.value,'Ancien texte');
  assert.equal(selection.ranges.length,1);
  assert.equal(selection.ranges[0].selected,editor);
  assert.equal(selection.ranges[0].collapsed,null);
});
test('contenteditable write replaces and verifies rich-editor text',()=>{
  const {bridge,editor}=fakeContentEditable();
  const ref=bridge.observe().controls[0].ref;
  const result=bridge.act(ref,'write',{text:'Nouveau message',mode:'replace'});
  assert.equal(result.verified,true);
  assert.equal(result.value,'Nouveau message');
  assert.equal(editor.innerText,'Nouveau message');
});
test('DOM relocation and disabled controls block mutations',()=>{
  const {bridge,input}=fakeDOM();let ref=bridge.observe().controls[0].ref;
  input.rect.left+=20;assert.throws(()=>bridge.act(ref,'click',{}),/changed/);
  ref=bridge.observe().controls[0].ref;input.disabled=true;
  assert.throws(()=>bridge.act(ref,'write',{text:'no'}),/changed/);
});
