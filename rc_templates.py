"""HTML/CSS/JS templates for the launcher, kept out of rc_launcher.py so the backend
isn't buried under ~300 lines of embedded frontend. Placeholders (__X__) are filled at
request time by page()/share_page(). One file read happens at import: rc_upload.js
(the tested, DOM-free upload-resume policy) is spliced into FILES_PAGE like the
_PTR/_THEME constants."""

from pathlib import Path

_UPLOAD = (Path(__file__).parent / "rc_upload.js").read_text()

# shared by both pages (filled into __PTR__ below) — the pull-to-refresh overlay
_PTR = """(function(){var y0=0,a=false,dy=0,T=64,se=document.scrollingElement||document.documentElement;
var b=document.createElement('div');
b.style.cssText='position:fixed;left:0;right:0;top:0;height:0;overflow:hidden;display:flex;align-items:flex-end;justify-content:center;color:var(--mut);font:12px ui-monospace,monospace;padding-bottom:6px;z-index:9;pointer-events:none;transition:height .12s';
document.body.appendChild(b);
addEventListener('touchstart',function(e){if(se.scrollTop<=0){y0=e.touches[0].clientY;a=true;dy=0;}},{passive:true});
addEventListener('touchmove',function(e){if(!a)return;dy=e.touches[0].clientY-y0;
if(dy>0&&se.scrollTop<=0){b.style.height=Math.min(dy*0.5,46)+'px';b.textContent=dy>T?'\\u21bb release to refresh':'\\u21bb pull to refresh';}else{a=false;b.style.height='0';}},{passive:true});
addEventListener('touchend',function(){if(!a)return;a=false;if(dy>T){b.style.height='46px';b.textContent='\\u21bb refreshing\\u2026';location.reload();}else{b.style.height='0';}});})();"""

# shared color tokens + light-mode overrides, spliced into both pages (filled into __THEME__) so
# the palette can't drift between the launcher and the files page. FILES_PAGE also gets --panel
# and --red (unused there, harmless) rather than maintaining a second, divergent token list.
_THEME = """:root{--bg:#0b0f14;--panel:#121821;--row:#161d28;--row2:#1b2330;--fg:#d7e0ea;--mut:#7c8a9c;--accent:#22c55e;--blue:#2563eb;--name:#a9c4f0;--red:#7f1d1d;--bd:#243040}
@media (prefers-color-scheme:light){:root{--bg:#eef1f5;--panel:#fff;--row:#fff;--row2:#e6eaf0;--fg:#16202e;--mut:#5b6b7c;--accent:#16a34a;--name:#2b4c85;--red:#dc2626;--bd:#d7dee7}}"""

# base rules byte-identical in both pages (filled into __BASE__) — only these three;
# header/h1/.nm deliberately diverge between the pages and stay per-page.
_BASE = """*{box-sizing:border-box}
body{margin:0;font:16px ui-monospace,SFMono-Regular,Menlo,monospace;
background:var(--bg);color:var(--fg)}
ul{list-style:none;margin:0;padding:6px 10px 48px}"""



PAGE = """<!doctype html>
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
.nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--name)}
.git{color:var(--mut);font-size:10px;max-width:34vw;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap;flex:0 0 auto;letter-spacing:.2px}
.git.dirty{color:#f59e0b}
.tag{color:var(--accent);font-size:11px}
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
const GITSTATES=__GITSTATES__;
const NAME_RE=/^[A-Za-z0-9._-]+$/;
let LOGIN=__LOGIN__, STATES=__STATES__, noTap=0;
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
  if(starting)li.className='starting';
  const dot=starting?'spin':st==='working'?'on work':st==='waiting'?'wait':live?'on':'';
  const tag=starting?'starting&hellip;':st==='working'?'working':st==='waiting'?'waiting':live?'live':'';
  const git=g?'<span class="git'+(g.d?' dirty':'')+'" title="git branch">'+esc(g.b)+(g.d?' \\u25cf':'')+'</span>':'';
  li.innerHTML='<span class="dot'+(dot?' '+dot:'')+'"></span>'+
    '<span class=nm>'+(pin?'\\u2605 ':'')+n+'</span>'+git+
    '<span class="tag'+(st==='waiting'?' tagwait':'')+'">'+tag+'</span>'+
    (live&&!starting?'<button class=x title="close session" aria-label="close '+n+'">&#10005;</button>':'');
  li.onclick=()=>go(n);
  if(live&&!starting)li.querySelector('.x').onclick=e=>{e.stopPropagation();stopSess(n);};
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
async function stopSess(n){
  toast('closing '+n+'\\u2026');
  try{
    const r=await fetch('/stop?json=1&proj='+encodeURIComponent(n));
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
    const r=await fetch('/create?json=1&proj='+encodeURIComponent(n));
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
if(location.search)history.replaceState({},'',location.pathname);
__PTR__
</script>
</body></html>"""


FILES_PAGE = """<!doctype html>
<html><head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>rc-share</title>
<style>
__THEME__
__BASE__
header{position:sticky;top:0;background:var(--bg);padding:14px 16px 10px;
border-bottom:1px solid var(--bd);z-index:2}
h1{margin:0 0 8px;font-size:13px;letter-spacing:.4px;color:var(--mut);
font-weight:600;text-transform:uppercase}
.crumb{font-size:13px;word-break:break-all;line-height:1.6}
.crumb a{color:var(--blue);text-decoration:none}
.crumb .sep{color:var(--mut);margin:0 6px}
.up{display:inline-block;margin-top:10px;font-size:12px;color:var(--blue);
border:1px solid var(--bd);border-radius:8px;padding:6px 12px;cursor:pointer;
-webkit-tap-highlight-color:transparent}
.up:active{background:var(--row2)}
.sortbar{display:flex;gap:6px;align-items:center;margin-top:10px;flex-wrap:wrap}
.sortlbl{color:var(--mut);font-size:11px;letter-spacing:.3px;text-transform:uppercase}
.sortbtn{border:1px solid var(--bd);border-radius:8px;padding:5px 11px;font:inherit;
font-size:12px;color:var(--mut);background:var(--row);cursor:pointer;
-webkit-tap-highlight-color:transparent}
.sortbtn:active{background:var(--row2)}
.sortbtn.on{color:var(--fg);border-color:var(--blue)}
li{border-radius:11px;background:var(--row);margin:6px 0}
li a{display:flex;align-items:center;gap:12px;padding:14px;color:var(--fg);
text-decoration:none;-webkit-tap-highlight-color:transparent}
li a:active{background:var(--row2)}
.nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
li.dir .nm{color:var(--accent)}
.meta{color:var(--mut);font-size:11px;white-space:nowrap;flex:0 0 auto}
li.empty{background:none;color:var(--mut);text-align:center;padding:30px}
</style></head>
<body>
<header>
<h1>rc-share &middot; __HOST__</h1>
<div class=crumb>__CRUMB__</div>
<label class=up id=up>+ upload<input id=f type=file multiple hidden></label>
<div class=sortbar><span class=sortlbl>sort</span><button class=sortbtn data-k=n>name</button><button class=sortbtn data-k=s>size</button><button class=sortbtn data-k=t>date</button></div>
</header>
<ul>__ROWS__</ul>
<script>
var REL=__REL__,up=document.getElementById('up');
function hum(n){if(n<1024)return n+' B';var u=['KB','MB','GB','TB'],i=-1;do{n/=1024;i++;}while(n>=1024&&i<3);return n.toFixed(1)+' '+u[i];}
function sleep(ms){return new Promise(function(r){setTimeout(r,ms);});}
__UPLOAD__
function putSlice(url,fl,off,end,total,rid){return new Promise(function(res){
var x=new XMLHttpRequest();x.open('PUT',url);
x.setRequestHeader('X-Rc-Offset',off);x.setRequestHeader('X-Rc-Total',total);x.setRequestHeader('X-Rc-Id',rid);
x.upload.onprogress=function(e){up.textContent=fl.name+' '+Math.floor((off+e.loaded)*100/total)+'% '+hum(off+e.loaded)+'/'+hum(total);};
x.onload=function(){res(x.status===200);};x.onerror=function(){res(false);};x.onabort=function(){res(false);};
x.send(fl.slice(off,end));});}
async function uploadFile(fl){
var url='/files'+REL+'/'+encodeURIComponent(fl.name),total=fl.size;
var rid=fl.size+'-'+fl.lastModified,idh={'X-Rc-Id':rid};
async function probe(){
try{var h=await fetch(url,{method:'HEAD',headers:idh});
if(!h.ok)return -1;
return parseInt(h.headers.get('X-Rc-Have')||'0',10)||0;}catch(e){return -1;}}
function send(off,end){return putSlice(url,fl,off,end,total,rid);}
var r=await resumeUpload(total,probe,send,
{onRetry:async function(){up.textContent=fl.name+' dropped \\u2014 retrying\\u2026';await sleep(1500);}});
if(!r.ok)up.textContent='\\u2717 '+fl.name+' failed';
return r.ok;}
document.getElementById('f').addEventListener('change',async function(e){
var fs=[].slice.call(e.target.files);if(!fs.length)return;
var ok=0,bad=0;
for(var i=0;i<fs.length;i++){if(await uploadFile(fs[i]))ok++;else bad++;}
if(!bad){location.reload();return;}
e.target.value='';
up.textContent=ok+' uploaded, '+bad+' failed \\u2014 pull to refresh';});
document.querySelectorAll('li:not(.dir):not(.empty)').forEach(function(li){
var a=li.querySelector('a');if(!a)return;var lp=false,t;
function start(){lp=false;t=setTimeout(function(){lp=true;del(a);},550);}
function cancel(){clearTimeout(t);}
a.addEventListener('contextmenu',function(e){e.preventDefault();});
a.addEventListener('touchstart',start,{passive:true});
a.addEventListener('touchend',cancel);a.addEventListener('touchmove',cancel);
a.addEventListener('mousedown',start);a.addEventListener('mouseup',cancel);
a.addEventListener('mouseleave',cancel);
a.addEventListener('click',function(e){if(lp){e.preventDefault();lp=false;}});});
async function del(a){
if(!confirm('Delete '+a.querySelector('.nm').textContent+'?'))return;
try{await fetch(a.getAttribute('href'),{method:'DELETE'});location.reload();}catch(e){}}
var UL=document.querySelector('ul');
function getSort(){try{return JSON.parse(localStorage.getItem('rc_sort'))||{k:'t',d:-1}}catch(e){return{k:'t',d:-1}}}
function applySort(){
var s=getSort(),lis=[].slice.call(UL.querySelectorAll('li[data-n]'));
lis.sort(function(a,b){
var dd=(+b.dataset.d)-(+a.dataset.d);if(dd)return dd;  // directories always on top
var c=s.k==='n'?a.dataset.n.localeCompare(b.dataset.n):(+a.dataset[s.k])-(+b.dataset[s.k]);
if(c)return c*s.d;
return a.dataset.n.localeCompare(b.dataset.n);});  // tie-break by name, always A-to-Z
lis.forEach(function(li){UL.appendChild(li);});
document.querySelectorAll('.sortbtn').forEach(function(b){var on=b.dataset.k===s.k;
b.classList.toggle('on',on);b.textContent=b.dataset.lbl+(on?(s.d>0?' \\u2191':' \\u2193'):'');});}
document.querySelectorAll('.sortbtn').forEach(function(b){b.dataset.lbl=b.textContent;
b.addEventListener('click',function(){var s=getSort();
if(s.k===b.dataset.k)s.d=-s.d;else{s.k=b.dataset.k;s.d=b.dataset.k==='n'?1:-1;}
localStorage.setItem('rc_sort',JSON.stringify(s));applySort();});});
applySort();
__PTR__
</script>
</body></html>"""

PAGE = PAGE.replace("__THEME__", _THEME).replace("__BASE__", _BASE).replace("__PTR__", _PTR)
FILES_PAGE = (
    FILES_PAGE.replace("__THEME__", _THEME)
    .replace("__BASE__", _BASE)
    .replace("__UPLOAD__", _UPLOAD)
    .replace("__PTR__", _PTR)
)
