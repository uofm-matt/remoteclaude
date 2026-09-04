"""The launcher page: the project list, the live dots, the search box and the launch/stop
taps. Data only — rc_sessions.page() fills the __PLACEHOLDER__s per request."""

from rc_templates import shared

PAGE = shared("""<!doctype html>
<html><head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>RC Launcher</title>
<style>
__THEME__
__BASE__
header{position:sticky;top:0;background:var(--bg);padding:14px 16px 8px;
border-bottom:1px solid var(--bd);z-index:2}
.htop{display:flex;align-items:baseline;justify-content:space-between;margin:0 0 10px}
h1{margin:0;font-size:13px;letter-spacing:.4px;color:var(--mut);
font-weight:600;text-transform:uppercase}
.auth{font-size:11px;letter-spacing:.3px;white-space:nowrap}
.hdr{display:flex;align-items:baseline;gap:12px}
.fileslink{color:var(--blue);font-size:11px;text-decoration:none;letter-spacing:.3px}
.auth.ok{color:var(--accent)}
.auth.warn{color:var(--mut)}
.auth.bad{color:#fecaca}
.authbar{display:none;margin:0 0 10px;padding:9px 12px;border-radius:9px;
background:var(--red);color:#fff;font-size:12px;line-height:1.35}
.authbar.show{display:block}
.qrow{display:flex;gap:8px;align-items:stretch}
#q{width:100%;flex:1;padding:13px 14px;border-radius:10px;border:1px solid var(--bd);
background:var(--panel);color:var(--fg);font:inherit;outline:none}
#q:focus{border-color:var(--blue)}
.newbtn{flex:0 0 auto;width:48px;border:1px solid var(--bd);border-radius:10px;
background:var(--panel);color:var(--accent);font:inherit;font-size:24px;line-height:1;
cursor:pointer;-webkit-tap-highlight-color:transparent}
.newbtn:active{background:var(--row2)}
.count{color:var(--mut);font-size:12px;margin:8px 2px 0}
.hint{color:var(--mut);font-size:11px;margin:5px 2px 0;opacity:.7}
.sect{color:var(--mut);font-size:11px;letter-spacing:.6px;text-transform:uppercase;
margin:14px 16px 4px}
li{display:flex;align-items:center;gap:12px;padding:14px;margin:6px 0;
border-radius:11px;background:var(--row);cursor:pointer;
-webkit-tap-highlight-color:transparent}
li:active{background:var(--row2)}
li.starting{opacity:.65}
.dot{width:9px;height:9px;border-radius:50%;background:transparent;
border:1.5px solid var(--bd);flex:0 0 auto}
.dot.on{background:var(--accent);border-color:var(--accent);
box-shadow:0 0 8px var(--accent)}
.dot.spin{border-color:var(--blue);border-top-color:transparent;box-shadow:none;
background:transparent;animation:sp .7s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.dot.work{animation:pulse 1.1s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 3px var(--accent)}50%{box-shadow:0 0 12px var(--accent)}}
.dot.wait{background:#f59e0b;border-color:#f59e0b;box-shadow:0 0 8px #f59e0b}
.tag.tagwait{color:#f59e0b}
.dot.desk{background:var(--blue);border-color:var(--blue);box-shadow:0 0 8px var(--blue)}
.tag.tagdesk{color:var(--blue)}
.nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--name)}
.git{color:var(--mut);font-size:10px;max-width:34vw;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap;flex:0 0 auto;letter-spacing:.2px}
.git.dirty{color:#f59e0b}
.tag{color:var(--accent);font-size:12px}
.tag.tagdesk{font-size:13px}
.x{appearance:none;border:none;background:transparent;color:var(--mut);
font:inherit;font-size:18px;line-height:1;padding:6px 10px;border-radius:8px;
flex:0 0 auto;cursor:pointer;-webkit-tap-highlight-color:transparent}
.x:active{background:#2a1620;color:#f87171}
.empty{color:var(--mut);text-align:center;padding:30px}
li.create{background:transparent;border:1px dashed var(--bd)}
li.create:active{background:var(--row)}
.plus{flex:0 0 auto;width:9px;text-align:center;color:var(--blue);font-size:20px;line-height:1}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);
background:var(--blue);color:#fff;padding:12px 18px;border-radius:10px;opacity:0;
transition:opacity .2s;pointer-events:none;font-size:14px;max-width:84vw;
text-align:center}
#toast.show{opacity:1}
@media (prefers-color-scheme:light){.auth.bad{color:#dc2626}.x:active{background:#f6dede;color:#dc2626}}
</style></head>
<body>
<header>
<div class=htop><h1>Remote Control &middot; __HOST__</h1><span class=hdr><a class=fileslink href="/files">files</a><span id=auth class=auth></span></span></div>
<div id=authbar class=authbar></div>
<div class=qrow><input id=q placeholder="filter projects&hellip;" autocomplete=off
 autocapitalize=off autocorrect=off spellcheck=false autofocus><button id=newbtn
 class=newbtn title="new project" aria-label="new project">+</button></div>
<div class=count id=count></div>
<div class=hint id=hint style=display:none>long-press a project to pin it</div>
</header>
<div id=pinnedWrap style=display:none><div class=sect>Pinned</div><ul id=pinned></ul></div>
<div id=recentWrap style=display:none><div class=sect>Recent</div><ul id=recent></ul></div>
<div class=sect>All projects</div>
<ul id=list></ul>
<div id=toast></div>
<script>
const PROJECTS=__PROJECTS__, RUNNING=new Set(__RUNNING__), STARTING=new Set();
let GITSTATES=__GITSTATES__;
const NAME_RE=/^[A-Za-z0-9._-]+$/;
let LOGIN=__LOGIN__, STATES=__STATES__, DESK=new Set(__DESK__), noTap=0;
const $=s=>document.querySelector(s), RK='rc_recent', PK='rc_pinned';
const esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const getRecent=()=>{try{return JSON.parse(localStorage.getItem(RK))||[]}catch(e){return[]}};
const pushRecent=n=>{let r=getRecent().filter(x=>x!==n);r.unshift(n);
  localStorage.setItem(RK,JSON.stringify(r.slice(0,6)));};
const getPinned=()=>{try{return JSON.parse(localStorage.getItem(PK))||[]}catch(e){return[]}};
const togglePin=n=>{const p=getPinned(),on=!p.includes(n);
  localStorage.setItem(PK,JSON.stringify(on?[...p,n].slice(0,20):p.filter(x=>x!==n)));
  noTap=Date.now()+600;render();toast(on?'\\u2605 pinned '+n:'unpinned '+n);};
function row(n){
  const li=document.createElement('li');li.dataset.n=n;
  const live=RUNNING.has(n), starting=STARTING.has(n), st=live?STATES[n]:'', g=GITSTATES[n], pin=getPinned().includes(n);
  const desk=!live&&!starting&&DESK.has(n);  // live at the desk (auto-paired) — a tap takes it over
  if(starting)li.className='starting';
  const dot=starting?'spin':st==='working'?'on work':st==='waiting'?'wait':live?'on':desk?'desk':'';
  // where the session lives: 📱 = launcher/tmux (phone), 🖥 = plain desk claude
  const tag=starting?'starting&hellip;':st==='working'?'\\uD83D\\uDCF1 working':st==='waiting'?'\\uD83D\\uDCF1 waiting':live?'\\uD83D\\uDCF1':desk?'\\uD83D\\uDDA5\\uFE0F':'';
  const git=g?'<span class="git'+(g.d?' dirty':'')+'" title="git branch">'+esc(g.b)+(g.d?' \\u25cf':'')+'</span>':'';
  li.innerHTML='<span class="dot'+(dot?' '+dot:'')+'"></span>'+
    '<span class=nm>'+(pin?'\\u2605 ':'')+n+'</span>'+git+
    '<span class="tag'+(st==='waiting'?' tagwait':desk?' tagdesk':'')+'"'+(desk?' title="live at the desk \\u2014 tapping takes it over"':'')+'>'+tag+'</span>'+
    ((live||desk)&&!starting?'<button class=x title="'+(desk?'close desk session':'close session')+'" aria-label="close '+n+'">&#10005;</button>':'');
  li.onclick=()=>go(n);
  if((live||desk)&&!starting)li.querySelector('.x').onclick=e=>{e.stopPropagation();stopSess(n,desk);};
  // long-press (touch or mouse) toggles the pin; togglePin sets noTap so the trailing
  // synthetic click that lands on the re-rendered row doesn't also launch the project.
  let t;const s0=()=>{t=setTimeout(()=>togglePin(n),550);},c0=()=>clearTimeout(t);
  li.addEventListener('touchstart',s0,{passive:true});li.addEventListener('touchend',c0);
  li.addEventListener('touchmove',c0,{passive:true});
  li.addEventListener('mousedown',s0);li.addEventListener('mouseup',c0);li.addEventListener('mouseleave',c0);
  li.addEventListener('contextmenu',e=>e.preventDefault());
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
  band('#pinnedWrap','#pinned',getPinned().filter(n=>PROJECTS.includes(n)),f);
  band('#recentWrap','#recent',getRecent().filter(n=>PROJECTS.includes(n)),f);
  $('#hint').style.display=(!getPinned().length&&!f&&PROJECTS.length>8)?'':'none';
}
function band(wrap,ul,names,f){
  const w=$(wrap),u=$(ul);u.innerHTML='';
  if(names.length&&!f){w.style.display='';names.forEach(n=>u.appendChild(row(n)));}
  else w.style.display='none';
}
function authBar(){
  const a=$('#auth'),b=$('#authbar');
  if(LOGIN==='ok'){a.className='auth ok';a.textContent='\\u25cf login ok';b.classList.remove('show');}
  else if(LOGIN==='loggedout'){a.className='auth bad';a.textContent='\\u2717 logged out';
    b.textContent='Claude is logged out on the Mac \\u2014 new sessions will fail. Run claude /login there.';b.classList.add('show');}
  else{a.className='auth warn';a.textContent='\\u2026 login ?';b.classList.remove('show');}
}
async function go(n){
  if(Date.now()<noTap||STARTING.has(n))return;
  if(RUNNING.has(n)){toast(n+' already live');return;}
  STARTING.add(n);render();
  try{
    const r=await fetch('/launch?json=1&proj='+encodeURIComponent(n));
    const j=await r.json();
    STARTING.delete(n);
    if(j.status==='failed'){render();toast('\\u2717 '+n+': '+(j.reason||'failed to start'));return;}
    RUNNING.add(n);pushRecent(n);render();
    toast(j.status==='already'?n+' already live':'\\u2713 launched '+n);
  }catch(e){STARTING.delete(n);render();toast('failed: '+n);}
}
async function stopSess(n,isDesk){
  toast('closing '+n+'\\u2026');
  try{
    const r=await fetch('/stop?json=1&proj='+encodeURIComponent(n)+(isDesk?'&desk=1':''));
    const j=await r.json();
    if(j.status==='failed'){render();toast('\\u2717 '+n+': '+(j.reason||'still running'));return;}
    if(isDesk)DESK.delete(n);else RUNNING.delete(n);
    render();
    toast(j.status==='idle'?n+' was already closed':'\\u2715 closed '+n);
  }catch(e){toast('failed to close '+n);}
}
async function createProj(n){
  if(STARTING.has(n))return;
  STARTING.add(n);if(!PROJECTS.includes(n))PROJECTS.push(n);
  $('#q').value=n;render();
  const drop=()=>{const i=PROJECTS.indexOf(n);if(i>=0)PROJECTS.splice(i,1);};
  try{
    const r=await fetch('/create?proj='+encodeURIComponent(n));
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
    const r=await fetch('/status');const j=await r.json();
    RUNNING.clear();j.running.forEach(n=>RUNNING.add(n));
    STATES=j.states||{};DESK=new Set(j.desk||[]);GITSTATES=j.git||GITSTATES;
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
if(location.search)history.replaceState({},'',location.pathname);
__PTR__
</script>
</body></html>""")
