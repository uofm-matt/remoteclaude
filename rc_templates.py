"""The chunks both launcher pages share, and the two helpers that fill a page in.

Splitting the pages out (rc_page, rc_files_page) left the parts that must NOT diverge —
the colour tokens, the base rules, the pull-to-refresh overlay, the tested upload-resume
policy — here, spliced into each page once at import by shared(). fill() and js() do the
per-request half: the placeholders a page still carries (__PROJECTS__, __ROWS__, ...) are
filled with live data on the way out.
"""

import json
import re
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


_CHUNKS = {
    "__THEME__": _THEME,
    "__BASE__": _BASE,
    "__PTR__": _PTR,
    "__UPLOAD__": _UPLOAD,
}


def shared(template: str) -> str:
    """Splice the shared chunks into a page template — once, at import. A placeholder a
    page doesn't carry is simply absent: only the files page has an upload widget."""
    for placeholder, chunk in _CHUNKS.items():
        template = template.replace(placeholder, chunk)
    return template


def js(x: object) -> str:
    """JSON for splicing into a <script> body: escape '<' so a value containing
    '</script>' (a file/dir named that, dropped into SHARE over SMB) can't break out."""
    return json.dumps(x).replace("<", "\\u003c")


def fill(template: str, values: dict[str, str]) -> bytes:
    """Fill __PLACEHOLDER__s in one pass, so injected data (a project dir named __LOGIN__,
    which NAME_RE permits) can't be re-scanned and rewritten by a later replacement."""
    # longest key first: re alternation takes the first match, so a key that prefixes
    # another (a future __HOST__/__HOSTS__ pair) must not shadow the longer one.
    pat = re.compile("|".join(map(re.escape, sorted(values, key=len, reverse=True))))
    return pat.sub(lambda m: values[m.group()], template).encode()
