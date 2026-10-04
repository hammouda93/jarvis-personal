"""Seeded, local three-surface workflow; no application-specific engine knowledge."""
from __future__ import annotations

import argparse
import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def generate_html(seed: int, view: str) -> str:
    rng = random.Random(seed)
    code = "REF-" + str(rng.randrange(100000, 999999)) + "-é"
    labels = rng.choice((
        {"source": "Dossier source", "review": "Révision", "code": "Référence", "open": "Vérifier",
         "confirm": "Confirmer", "dialog": "Confirmation", "done": "Confirmé"},
        {"source": "Source record", "review": "Review", "code": "Reference", "open": "Review details",
         "confirm": "Confirm", "dialog": "Confirmation", "done": "Confirmed"},
    ))
    columns = "row-reverse" if rng.randrange(2) else "row"
    settings = json.dumps({"seed": seed, "code": code, "labels": labels, "view": view}, ensure_ascii=False)
    return r'''<!doctype html><meta charset="utf-8"><title>Unknown workflow</title>
<style>body{font:18px system-ui;margin:28px;background:#eef2f5;color:#1b3040}
main{display:flex;flex-direction:DIRECTION;gap:24px}section{background:white;padding:24px;border-radius:10px;min-width:260px}
input,button{font:inherit;padding:12px;margin:8px;border:1px solid #8199a9;border-radius:5px}
h1{font-size:24px}h2{font-size:20px}</style><main></main>
<script>
const cfg=SETTINGS, L=cfg.labels, root=document.querySelector('main');
const state={code:cfg.code,value:'',confirmed:false,confirmCount:0};
window.fixtureState=()=>JSON.parse(JSON.stringify(state)); // Test oracle, never a planner input.
function heading(text){const h=document.createElement('h1');h.textContent=text;root.append(h);return h;}
function status(){state.confirmed=localStorage.getItem('workflow-'+cfg.seed)===cfg.code;
 document.querySelector('[role=status]')?.remove();const e=document.createElement('h2');e.setAttribute('role','status');
 e.textContent=state.confirmed?L.done:'Pending';root.append(e);}
if(cfg.view==='source'){
 document.title=L.source;heading(L.source);const section=document.createElement('section');
 const label=document.createElement('label');label.textContent=L.code;const input=document.createElement('input');
 input.readOnly=true;input.value=cfg.code;label.append(input);section.append(label);root.append(section);status();
 window.addEventListener('storage',status);
}else if(cfg.view==='review'){
 document.title=L.review;heading(L.review);const section=document.createElement('section');
 const label=document.createElement('label');label.textContent=L.code;const input=document.createElement('input');
 input.oninput=()=>state.value=input.value;label.append(input);section.append(label);
 const open=document.createElement('button');open.textContent=L.open;
 open.onclick=()=>{if(state.value===cfg.code)window.open('/confirmation?seed='+cfg.seed,'','width=540,height=420');};
 section.append(open);root.append(section);status();window.addEventListener('storage',status);
 window.addEventListener('message',event=>{if(event.origin===location.origin&&event.data==='workflow-confirmed')status();});
}else if(cfg.view==='confirmation'){
 document.title=L.dialog;heading(L.dialog);const b=document.createElement('button');b.textContent=L.confirm;
 b.onclick=()=>{state.confirmCount++;localStorage.setItem('workflow-'+cfg.seed,cfg.code);
 state.confirmed=true;b.disabled=true;window.opener?.postMessage('workflow-confirmed',location.origin);status();};
 root.append(b);status();
}
</script>'''.replace("SETTINGS", settings).replace("DIRECTION", columns)


def make_server(port: int = 0, seed: int = 1) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            view = parsed.path.strip("/") or "source"
            requested_seed = int(parse_qs(parsed.query).get("seed", [str(seed)])[0])
            if view not in {"source", "review", "confirmation"}:
                self.send_error(404)
                return
            data = generate_html(requested_seed, view).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    server = make_server(args.port, args.seed)
    origin = f"http://127.0.0.1:{server.server_port}"
    print(f"Ouvrir {origin}/source et {origin}/review dans deux onglets du même navigateur.", flush=True)
    print("Mission : recopie la référence du dossier source dans le formulaire de révision, "
          "ouvre la confirmation et confirme. Vérifie la confirmation dans les trois surfaces.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
