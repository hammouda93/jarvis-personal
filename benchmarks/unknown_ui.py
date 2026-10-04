"""Local held-out GUI fixture. Contains no external messaging or site knowledge."""
from __future__ import annotations

import argparse
import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def generate_html(seed: int = 1, opaque: bool = False) -> str:
    rng = random.Random(seed)
    accent = rng.choice(("#174e69", "#693f80", "#24664e", "#824b2c"))
    order = rng.sample(["search", "contacts", "conversation"], 3)
    settings = json.dumps({"seed": seed, "opaque": opaque, "accent": accent, "order": order})
    return r'''<!doctype html><meta charset="utf-8"><title>Unknown UI laboratory</title>
<style>
body{font:18px system-ui;background:#eaf0f3;color:#172b38;margin:24px}
main{display:grid;grid-template-columns:repeat(3,minmax(220px,1fr));gap:18px}
section{background:white;padding:18px;border-radius:12px;min-height:160px}
input,textarea,button{font:inherit;border:1px solid #879cac;border-radius:5px;padding:10px;max-width:100%;box-sizing:border-box}
textarea{display:block;width:100%;margin:12px 0}button{cursor:pointer;margin:4px}
h1{font-size:22px}h2{font-size:20px}#oracle{display:none}canvas{max-width:100%;background:white;border-radius:12px}
</style><h1>Interface de test locale</h1><main></main>
<script>
const variant=SETTINGS;
const state={query:'',contact:'',draft:'',messages:[],sendCount:0};
window.fixtureState=()=>JSON.parse(JSON.stringify(state)); // Independent test oracle, never a planner tool.
const contacts=['Salah','Nour','Amira'];
const main=document.querySelector('main');
if(!variant.opaque){
 const panels={
 search:`<section><label for="query">Recherche</label><input id="query" type="search" autocomplete="off"></section>`,
 contacts:`<section id="contacts" aria-label="Contacts"></section>`,
 conversation:`<section id="conversation" aria-label="Conversation"><p>Choisissez un contact.</p></section>`};
 main.innerHTML=variant.order.map(k=>panels[k]).join('');
 const renderContacts=()=>{
   const results=contacts.filter(c=>!state.query||c.toLowerCase().includes(state.query.toLowerCase()));
   document.querySelector('#contacts').replaceChildren(...results.map(c=>{
     const b=document.createElement('button');b.textContent=c;b.onclick=()=>openContact(c);return b;
   }));
 };
 const openContact=c=>{
   state.contact=c;
   const panel=document.querySelector('#conversation');
   panel.innerHTML='<h2></h2><div role="log" aria-label="Messages"></div><label for="draft">Message</label><textarea id="draft"></textarea><button id="send">Envoyer</button>';
   panel.querySelector('h2').textContent=c;
   panel.querySelector('textarea').value=state.draft;
   panel.querySelector('textarea').oninput=e=>state.draft=e.target.value;
   panel.querySelector('#send').onclick=()=>{if(state.draft){state.messages.push(state.draft);state.sendCount++;
     const p=document.createElement('p');p.textContent=state.draft;panel.querySelector('[role=log]').append(p);
     state.draft='';panel.querySelector('textarea').value='';}};
 };
 document.querySelector('#query').oninput=e=>{state.query=e.target.value;renderContacts();};renderContacts();
}else{
 main.style.display='block';
 const canvas=document.createElement('canvas');canvas.width=1050;canvas.height=630;main.append(canvas);
 const ctx=canvas.getContext('2d'), boxes=[];
 const left=35+(variant.seed%4)*17, right=540+(variant.seed%3)*23;
 let editing='';
 const editor=document.createElement('textarea');editor.style.cssText='position:absolute;opacity:0;pointer-events:none;resize:none';
 editor.setAttribute('aria-hidden','true');document.body.append(editor);
 const draw=()=>{
   ctx.clearRect(0,0,1050,630);boxes.length=0;ctx.font='20px system-ui';ctx.fillStyle='#172b38';
   const box=(key,x,y,w,h,text)=>{ctx.strokeStyle=variant.accent;ctx.lineWidth=2;ctx.strokeRect(x,y,w,h);
     ctx.fillStyle='#172b38';ctx.fillText(text,x+12,y+32);boxes.push({key,x,y,w,h});};
   ctx.fillText('Recherche',left,40);box('search',left,55,390,48,state.query||'Chercher un contact');
   let y=145;for(const c of contacts.filter(c=>!state.query||c.toLowerCase().includes(state.query.toLowerCase()))){
     box('contact:'+c,left,y,390,52,c);y+=75;
   }
   ctx.fillText(state.contact||'Choisissez un contact',right,45);
   if(state.contact){
     ctx.fillText('Messages',right,95);let my=140;for(const msg of state.messages){ctx.fillText(msg,right,my);my+=35;}
     ctx.fillText('Message',right,390);box('composer',right,410,380,65,state.draft||'Écrire un message');
     box('send',right+230,510,150,52,'Envoyer');
   }
   if(editing){const b=boxes.find(b=>b.key===editing);if(b){ctx.fillText('|',b.x+15+ctx.measureText(editing==='search'?state.query:state.draft).width,b.y+32);}}
 };
 canvas.onclick=e=>{
   const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)*canvas.width/r.width,y=(e.clientY-r.top)*canvas.height/r.height;
   const b=boxes.find(b=>x>=b.x&&x<b.x+b.w&&y>=b.y&&y<b.y+b.h);if(!b)return;
   if(b.key==='search'||b.key==='composer'){
     editing=b.key;editor.value=b.key==='search'?state.query:state.draft;
     editor.style.left=(r.left+scrollX+b.x*r.width/canvas.width)+'px';
     editor.style.top=(r.top+scrollY+b.y*r.height/canvas.height)+'px';
     editor.style.width=(b.w*r.width/canvas.width)+'px';editor.style.height=(b.h*r.height/canvas.height)+'px';editor.focus();
   }else if(b.key.startsWith('contact:')){state.contact=b.key.slice(8);editing='';}
   else if(b.key==='send'&&state.draft){state.messages.push(state.draft);state.sendCount++;state.draft='';editing='';}
   draw();
 };
 editor.oninput=()=>{if(editing==='search')state.query=editor.value;else if(editing==='composer')state.draft=editor.value;draw();};
 draw();
}
</script>'''.replace("SETTINGS", settings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--opaque", action="store_true")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    data = generate_html(args.seed, args.opaque).encode("utf-8")
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{args.port} — seed={args.seed}, opaque={args.opaque}", flush=True)
    print("Mission: Cherche Salah et ouvre sa conversation. Puis écris un message de test unique et envoie-le.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
