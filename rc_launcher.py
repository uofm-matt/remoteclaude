#!/usr/bin/env python3
"""Remote Control launcher.

Tap a project on your phone -> this starts a Claude Code Remote Control
session on the Mac, rooted in that project's directory (so its CLAUDE.md,
.claude/ settings and project MCP load exactly like the VS Code extension).
Each session is held in a detached tmux session so it survives the HTTP
request returning and any SSH/terminal closing.

Config comes from the environment (set by the LaunchAgent); the defaults
match this machine. Refuses to start without RC_LAUNCHER_TOKEN.
"""

import functools
import html
import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

PARENT = os.path.expanduser(os.environ.get("RC_PROJECTS_PARENT", "~/projects"))
CLAUDE = os.path.expanduser(os.environ.get("RC_CLAUDE_BIN", "~/.local/bin/claude"))
TMUX = os.environ.get("RC_TMUX_BIN", "/opt/homebrew/bin/tmux")
GIT = os.environ.get("RC_GIT_BIN", "git")
TOKEN = os.environ.get("RC_LAUNCHER_TOKEN", "")
PORT = int(os.environ.get("RC_LAUNCHER_PORT", "8787"))
BIND = os.environ.get("RC_LAUNCHER_BIND", "0.0.0.0")
SPAWN = os.environ.get("RC_SPAWN", "same-dir")  # same-dir | worktree | session
HOST = socket.gethostname().split(".")[0]
CLAUDE_JSON = os.path.expanduser("~/.claude.json")
MT = ZoneInfo("America/Denver")

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

STATE_DIR = os.path.expanduser(os.environ.get("RC_STATE_DIR", "~/.cache/rc-state"))
STATE_TTL = float(os.environ.get("RC_STATE_TTL", "3600"))
_RANK = {"working": 3, "waiting": 2, "idle": 1}


def log_event(action: str, proj: str, result: str) -> None:
    """One audit line per launch/stop to StandardOutPath (/tmp/rc-launcher.log)."""
    print(f"{datetime.now(MT):%Y-%m-%d %H:%M:%S} MT  {action:<6} {proj} -> {result}",
          flush=True)


@functools.lru_cache(maxsize=1)
def _login_status(_bucket: int) -> str:
    try:
        out = subprocess.run([CLAUDE, "auth", "status"],
                             capture_output=True, text=True, timeout=15).stdout
        return "ok" if json.loads(out).get("loggedIn") else "loggedout"
    except (json.JSONDecodeError, subprocess.SubprocessError, OSError):
        return "unknown"


def login_status() -> str:
    """'ok' | 'loggedout' | 'unknown'. `claude auth status` spawns a process,
    so cache it per 60s bucket — the phone polls /status every few seconds."""
    return _login_status(int(time.monotonic() // 60))


def ensure_trusted(proj: str) -> None:
    """Pre-accept the workspace trust dialog for the project dir.

    `claude remote-control` refuses to start in an untrusted dir, exiting
    status 1 before it registers with the relay — so the app never sees the
    session and the phone tap silently does nothing. No interactive trust
    dialog is reachable from the phone, so we accept it here. Atomic replace,
    and we only write when the flag is missing, to avoid racing claude's own
    frequent writes to this file.
    """
    key = os.path.join(PARENT, proj)
    try:
        d = json.load(open(CLAUDE_JSON))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    entry = d.setdefault("projects", {}).setdefault(key, {})
    if entry.get("hasTrustDialogAccepted"):
        return
    entry.setdefault("allowedTools", [])
    entry.setdefault("mcpServers", {})
    entry["hasTrustDialogAccepted"] = True
    tmp = CLAUDE_JSON + ".rctmp"
    json.dump(d, open(tmp, "w"), indent=2)
    os.replace(tmp, CLAUDE_JSON)


def projects() -> list[str]:
    try:
        entries = os.listdir(PARENT)
    except FileNotFoundError:
        return []
    return sorted(
        e for e in entries
        if NAME_RE.match(e) and os.path.isdir(os.path.join(PARENT, e))
    )


def running() -> set[str]:
    try:
        out = subprocess.run(
            [TMUX, "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True,
        ).stdout
    except FileNotFoundError:  # tmux not installed yet; status is non-essential
        return set()
    return {line[3:] for line in out.splitlines() if line.startswith("rc-")}


def session_states() -> dict[str, str]:
    """{project: most-urgent turn state} from the files rc_state_hook.py writes,
    so the UI can show working/waiting, not just live. Stale files are ignored."""
    out: dict[str, str] = {}
    now = time.time()
    try:
        names = os.listdir(STATE_DIR)
    except FileNotFoundError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(STATE_DIR, n)))
        except (json.JSONDecodeError, OSError):
            continue
        st = d.get("state")
        proj = d.get("project") or ""
        if st in _RANK and proj and now - d.get("ts", 0) <= STATE_TTL \
                and _RANK[st] > _RANK.get(out.get(proj, ""), 0):
            out[proj] = st
    return out


def death_reason(sess: str) -> str:
    """Why a just-launched RC session died, read from its dead pane."""
    out = subprocess.run([TMUX, "capture-pane", "-t", sess, "-p"],
                         capture_output=True, text=True).stdout
    last = next((ln.strip() for ln in reversed(out.splitlines())
                 if ln.strip() and not ln.startswith("Pane is dead")), "")
    low = last.lower()
    if "trust" in low:
        return "untrusted dir"
    if any(w in low for w in ("auth", "logged out", "log in", "login", "credential")):
        return "login expired — run `claude /login` on the Mac"
    return last[:80] or "exited immediately"


def launch(proj: str) -> tuple[str, str | None]:
    sess = f"rc-{proj}"
    if subprocess.run([TMUX, "has-session", "-t", sess],
                      capture_output=True).returncode == 0:
        return "already", None
    ensure_trusted(proj)
    # Always pass --spawn explicitly: omitting it (even for the same-dir
    # default) makes `claude` prompt for spawn mode on a project's first run,
    # which hangs forever in a headless tmux session.
    cmd = [CLAUDE, "remote-control", "--name", proj, "--spawn", SPAWN]
    # Tag the session env so the state hook fires for remote (phone-driven)
    # sessions only, not local desk ones. The sessions the RC server spawns
    # inherit this env, so rc_status.py can tell when a remote turn is live on
    # the shared working tree.
    env_opts = ["-e", f"RC_REMOTE={sess}", "-e", f"RC_PROJECT={proj}"]
    if os.environ.get("RC_STATE_DIR"):
        env_opts += ["-e", f"RC_STATE_DIR={os.environ['RC_STATE_DIR']}"]
    subprocess.run(
        [TMUX, "new-session", "-d", "-s", sess, *env_opts,
         "-c", os.path.join(PARENT, proj), " ".join(cmd)],
        check=False,
    )
    # RC dies within ~2s on any startup error (untrusted dir, expired login),
    # taking its tmux session with it. Hold the dead pane so we can read WHY;
    # for a healthy session remain-on-exit does nothing until it later exits,
    # so we switch it back off once we've confirmed it's up.
    subprocess.run([TMUX, "set-option", "-t", sess, "remain-on-exit", "on"],
                   capture_output=True)
    time.sleep(3)
    dead = subprocess.run([TMUX, "list-panes", "-t", sess, "-F", "#{pane_dead}"],
                          capture_output=True, text=True).stdout.strip()
    if dead != "0":
        reason = death_reason(sess)
        subprocess.run([TMUX, "kill-session", "-t", sess], capture_output=True)
        return "failed", reason
    subprocess.run([TMUX, "set-option", "-t", sess, "remain-on-exit", "off"],
                   capture_output=True)
    return "launched", None


def stop(proj: str) -> tuple[str, str | None]:
    sess = f"rc-{proj}"
    # Graceful first: SIGINT the claude server (Ctrl-C to the pane's foreground
    # process) so it can deregister from Anthropic's relay. An abrupt
    # kill-session sends SIGHUP, which the relay can't tell apart from the Mac
    # dropping off the network — so the app keeps showing the session
    # "connected" until the relay's inactivity timeout (~10 min) evicts it.
    # Give claude a moment to disconnect, then hard-kill the session as fallback.
    subprocess.run([TMUX, "send-keys", "-t", sess, "C-c"], capture_output=True)
    time.sleep(2)
    subprocess.run([TMUX, "kill-session", "-t", sess], capture_output=True)
    return "stopped", None


def create(proj: str) -> tuple[str, str | None]:
    """Make a new project dir under PARENT, git-init it, drop a CLAUDE.md stub.

    NAME_RE keeps proj a single path segment, so it can't escape PARENT. git
    runs best-effort: if it's missing the dir and CLAUDE.md still stand and the
    session launches anyway. The route launches it after this returns 'created'.
    """
    if not NAME_RE.match(proj):
        return "badname", "letters, digits, dot, dash, underscore only"
    path = os.path.join(PARENT, proj)
    if os.path.exists(path):
        return "exists", None
    os.makedirs(path)
    subprocess.run([GIT, "init", "-q"], cwd=path, capture_output=True)
    with open(os.path.join(path, "CLAUDE.md"), "w") as f:
        f.write(f"# {proj}\n")
    return "created", None


PAGE = """<!doctype html>
<html><head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>RC Launcher</title>
<style>
:root{--bg:#0b0f14;--panel:#121821;--row:#161d28;--row2:#1b2330;--fg:#d7e0ea;
--mut:#7c8a9c;--accent:#22c55e;--blue:#2563eb;--red:#7f1d1d}
*{box-sizing:border-box}
body{margin:0;font:16px ui-monospace,SFMono-Regular,Menlo,monospace;
background:var(--bg);color:var(--fg)}
header{position:sticky;top:0;background:var(--bg);padding:14px 16px 8px;
border-bottom:1px solid #1f2935;z-index:2}
.htop{display:flex;align-items:baseline;justify-content:space-between;margin:0 0 10px}
h1{margin:0;font-size:13px;letter-spacing:.4px;color:var(--mut);
font-weight:600;text-transform:uppercase}
.auth{font-size:11px;letter-spacing:.3px;white-space:nowrap}
.auth.ok{color:var(--accent)}
.auth.warn{color:var(--mut)}
.auth.bad{color:#fecaca}
.authbar{display:none;margin:0 0 10px;padding:9px 12px;border-radius:9px;
background:var(--red);color:#fff;font-size:12px;line-height:1.35}
.authbar.show{display:block}
.qrow{display:flex;gap:8px;align-items:stretch}
#q{width:100%;flex:1;padding:13px 14px;border-radius:10px;border:1px solid #283445;
background:var(--panel);color:var(--fg);font:inherit;outline:none}
#q:focus{border-color:var(--blue)}
.newbtn{flex:0 0 auto;width:48px;border:1px solid #283445;border-radius:10px;
background:var(--panel);color:var(--accent);font:inherit;font-size:24px;line-height:1;
cursor:pointer;-webkit-tap-highlight-color:transparent}
.newbtn:active{background:var(--row2)}
.count{color:var(--mut);font-size:12px;margin:8px 2px 0}
.sect{color:var(--mut);font-size:11px;letter-spacing:.6px;text-transform:uppercase;
margin:14px 16px 4px}
ul{list-style:none;margin:0;padding:6px 10px 48px}
li{display:flex;align-items:center;gap:12px;padding:14px;margin:6px 0;
border-radius:11px;background:var(--row);cursor:pointer;
-webkit-tap-highlight-color:transparent}
li:active{background:var(--row2)}
li.starting{opacity:.65}
.dot{width:9px;height:9px;border-radius:50%;background:transparent;
border:1.5px solid #3a4757;flex:0 0 auto}
.dot.on{background:var(--accent);border-color:var(--accent);
box-shadow:0 0 8px var(--accent)}
.dot.spin{border-color:var(--blue);border-top-color:transparent;box-shadow:none;
background:transparent;animation:sp .7s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.dot.work{animation:pulse 1.1s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 3px var(--accent)}50%{box-shadow:0 0 12px var(--accent)}}
.dot.wait{background:#f59e0b;border-color:#f59e0b;box-shadow:0 0 8px #f59e0b}
.tag.tagwait{color:#f59e0b}
.nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tag{color:var(--accent);font-size:11px}
.x{appearance:none;border:none;background:transparent;color:var(--mut);
font:inherit;font-size:18px;line-height:1;padding:6px 10px;border-radius:8px;
flex:0 0 auto;cursor:pointer;-webkit-tap-highlight-color:transparent}
.x:active{background:#2a1620;color:#f87171}
.empty{color:var(--mut);text-align:center;padding:30px}
li.create{background:transparent;border:1px dashed #2f3e52}
li.create:active{background:var(--row)}
.plus{flex:0 0 auto;width:9px;text-align:center;color:var(--blue);font-size:20px;line-height:1}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);
background:var(--blue);color:#fff;padding:12px 18px;border-radius:10px;opacity:0;
transition:opacity .2s;pointer-events:none;font-size:14px;max-width:84vw;
text-align:center}
#toast.show{opacity:1}
</style></head>
<body>
<header>
<div class=htop><h1>Remote Control &middot; __HOST__</h1><span id=auth class=auth></span></div>
<div id=authbar class=authbar></div>
<div class=qrow><input id=q placeholder="filter projects&hellip;" autocomplete=off
 autocapitalize=off autocorrect=off spellcheck=false autofocus><button id=newbtn
 class=newbtn title="new project" aria-label="new project">+</button></div>
<div class=count id=count></div>
</header>
<div id=recentWrap style=display:none><div class=sect>Recent</div><ul id=recent></ul></div>
<div class=sect>All projects</div>
<ul id=list></ul>
<div id=toast></div>
<script>
const TOKEN=__TOKEN__, PROJECTS=__PROJECTS__, RUNNING=new Set(__RUNNING__),
  STARTING=new Set(), T=encodeURIComponent(TOKEN);
const NAME_RE=/^[A-Za-z0-9._-]+$/;
let LOGIN=__LOGIN__, STATES=__STATES__;
const $=s=>document.querySelector(s), RK='rc_recent';
const getRecent=()=>{try{return JSON.parse(localStorage.getItem(RK))||[]}catch(e){return[]}};
const pushRecent=n=>{let r=getRecent().filter(x=>x!==n);r.unshift(n);
  localStorage.setItem(RK,JSON.stringify(r.slice(0,6)));};
function row(n){
  const li=document.createElement('li');li.dataset.n=n;
  const live=RUNNING.has(n), starting=STARTING.has(n), st=live?STATES[n]:'';
  if(starting)li.className='starting';
  const dot=starting?'spin':st==='working'?'on work':st==='waiting'?'wait':live?'on':'';
  const tag=starting?'starting&hellip;':st==='working'?'working':st==='waiting'?'waiting':live?'live':'';
  li.innerHTML='<span class="dot'+(dot?' '+dot:'')+'"></span>'+
    '<span class=nm>'+n+'</span><span class="tag'+(st==='waiting'?' tagwait':'')+'">'+tag+'</span>'+
    (live&&!starting?'<button class=x title="close session" aria-label="close '+n+'">&#10005;</button>':'');
  li.onclick=()=>go(n);
  if(live&&!starting)li.querySelector('.x').onclick=e=>{e.stopPropagation();stopSess(n);};
  return li;
}
function createRow(n){
  const li=document.createElement('li');li.className='create';
  li.innerHTML='<span class=plus>+</span><span class=nm>create &amp; start \\u201c'+n+'\\u201d</span>';
  li.onclick=()=>createProj(n);
  return li;
}
function render(){
  const raw=$('#q').value.trim(), f=raw.toLowerCase();
  const hits=PROJECTS.filter(n=>n.toLowerCase().includes(f));
  const list=$('#list');list.innerHTML='';
  hits.forEach(n=>list.appendChild(row(n)));
  const canCreate=raw&&NAME_RE.test(raw)&&!PROJECTS.includes(raw);
  if(canCreate)list.appendChild(createRow(raw));
  if(!hits.length&&!canCreate)list.innerHTML='<div class=empty>no match</div>';
  $('#count').textContent=hits.length+' / '+PROJECTS.length;
  const rec=getRecent().filter(n=>PROJECTS.includes(n));
  const rw=$('#recentWrap'),ru=$('#recent');ru.innerHTML='';
  if(rec.length&&!f){rw.style.display='';rec.forEach(n=>ru.appendChild(row(n)));}
  else rw.style.display='none';
}
function authBar(){
  const a=$('#auth'),b=$('#authbar');
  if(LOGIN==='ok'){a.className='auth ok';a.textContent='\\u25cf login ok';b.classList.remove('show');}
  else if(LOGIN==='loggedout'){a.className='auth bad';a.textContent='\\u2717 logged out';
    b.textContent='Claude is logged out on the Mac \\u2014 new sessions will fail. Run claude /login there.';b.classList.add('show');}
  else{a.className='auth warn';a.textContent='\\u2026 login ?';b.classList.remove('show');}
}
async function go(n){
  if(STARTING.has(n))return;
  if(RUNNING.has(n)){toast(n+' already live');return;}
  STARTING.add(n);render();
  try{
    const r=await fetch('/launch?json=1&token='+T+'&proj='+encodeURIComponent(n));
    const j=await r.json();
    STARTING.delete(n);
    if(j.status==='failed'){render();toast('\\u2717 '+n+': '+(j.reason||'failed to start'));return;}
    RUNNING.add(n);pushRecent(n);render();
    toast(j.status==='already'?n+' already live':'\\u2713 launched '+n);
  }catch(e){STARTING.delete(n);render();toast('failed: '+n);}
}
async function stopSess(n){
  if(!confirm('Close session '+n+'?\\nThis ends the Claude session and its context.'))return;
  toast('closing '+n+'\\u2026');
  try{
    const r=await fetch('/stop?json=1&token='+T+'&proj='+encodeURIComponent(n));
    await r.json();
    RUNNING.delete(n);render();
    toast('\\u2715 closed '+n);
  }catch(e){toast('failed to close '+n);}
}
async function createProj(n){
  if(STARTING.has(n))return;
  STARTING.add(n);if(!PROJECTS.includes(n))PROJECTS.push(n);
  $('#q').value=n;render();
  const drop=()=>{const i=PROJECTS.indexOf(n);if(i>=0)PROJECTS.splice(i,1);};
  try{
    const r=await fetch('/create?json=1&token='+T+'&proj='+encodeURIComponent(n));
    const j=await r.json();
    STARTING.delete(n);
    if(j.status!=='created'&&j.status!=='exists'){
      drop();render();toast('\\u2717 '+n+': '+(j.reason||j.status||'create failed'));return;}
    if(j.launch==='launched'||j.launch==='already')RUNNING.add(n);
    pushRecent(n);PROJECTS.sort();$('#q').value='';render();
    if(j.status==='exists')toast(n+' already exists');
    else if(j.launch&&j.launch!=='launched'&&j.launch!=='already')
      toast('created '+n+', start failed: '+(j.launch_reason||j.launch));
    else toast('\\u2713 created & started '+n);
  }catch(e){STARTING.delete(n);drop();render();toast('failed: '+n);}
}
async function poll(){
  try{
    const r=await fetch('/status?token='+T);const j=await r.json();
    RUNNING.clear();j.running.forEach(n=>RUNNING.add(n));
    STATES=j.states||{};
    LOGIN=j.login;authBar();render();
  }catch(e){}
}
let tt;function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');
  clearTimeout(tt);tt=setTimeout(()=>t.classList.remove('show'),2600);}
$('#q').addEventListener('input',render);
$('#q').addEventListener('keydown',e=>{if(e.key!=='Enter')return;
  const first=$('#list li[data-n]');if(first){go(first.dataset.n);return;}
  const raw=$('#q').value.trim();
  if(raw&&NAME_RE.test(raw)&&!PROJECTS.includes(raw))createProj(raw);});
$('#newbtn').onclick=()=>{
  const n=(prompt('New project name',$('#q').value.trim())||'').trim();
  if(!n)return;
  if(!NAME_RE.test(n)){toast('\\u2717 bad name: letters, digits, . _ - only');return;}
  if(PROJECTS.includes(n)){toast(n+' already exists \\u2014 tap it to start');return;}
  createProj(n);};
authBar();render();setInterval(poll,5000);
</script>
</body></html>"""


def page() -> bytes:
    return (PAGE
            .replace("__TOKEN__", json.dumps(TOKEN))
            .replace("__PROJECTS__", json.dumps(projects()))
            .replace("__RUNNING__", json.dumps(sorted(running())))
            .replace("__STATES__", json.dumps(session_states()))
            .replace("__LOGIN__", json.dumps(login_status()))
            .replace("__HOST__", html.escape(HOST))).encode()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict):
        self._send(200, json.dumps(payload).encode(), "application/json")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not TOKEN or q.get("token", [""])[0] != TOKEN:
            return self._send(403, b"forbidden")
        if u.path == "/":
            return self._send(200, page())
        if u.path == "/status":
            return self._json({"running": sorted(running()), "login": login_status(),
                               "states": session_states()})
        if u.path == "/create":
            proj = q.get("proj", [""])[0]
            status, reason = create(proj)
            log_event("create", proj, status)
            payload = {"status": status, "proj": proj}
            if reason:
                payload["reason"] = reason
            if status == "created":
                lstatus, lreason = launch(proj)
                log_event("launch", proj, lstatus)
                payload["launch"] = lstatus
                if lreason:
                    payload["launch_reason"] = lreason
            return self._json(payload)
        if u.path in ("/launch", "/stop"):
            proj = q.get("proj", [""])[0]
            if proj not in projects():
                return self._send(404, b'{"error":"unknown project"}',
                                  "application/json")
            status, reason = launch(proj) if u.path == "/launch" else stop(proj)
            log_event(u.path[1:], proj, status)
            if q.get("json", [""])[0] == "1":
                payload = {"status": status, "proj": proj}
                if reason:
                    payload["reason"] = reason
                return self._json(payload)
            return self._send(200, page())
        self._send(404, b"not found")

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("RC_LAUNCHER_TOKEN not set (the LaunchAgent supplies it)")
    print(f"rc-launcher on {BIND}:{PORT} parent={PARENT} spawn={SPAWN}")
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
