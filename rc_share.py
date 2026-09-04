"""The ~/rc-share file share: what a request is allowed to reach, how a directory is
rendered, and the sweep that reclaims abandoned uploads.

This is the only remotely-reachable code that resolves arbitrary paths, so the
confinement predicate lives here once and every read, write and listing goes through it —
a second definition is how a boundary drifts. The HTTP verbs themselves stay in
rc_launcher; this module never sees a request.
"""

import contextlib
import hashlib
import html
import os
import stat
import time
from datetime import datetime
from urllib.parse import quote, unquote

import rc_config as cfg
from rc_claude import MT
from rc_files_page import FILES_PAGE
from rc_templates import fill, js


def within_share(p: str) -> bool:
    """The single definition of 'this real path is inside the share', used by every
    read and write path so the confinement boundary can't drift between them."""
    return p == cfg.SHARE or p.startswith(cfg.SHARE + os.sep)


def share_target(rel: str) -> str | None:
    """Resolve a /files/<rel> request to a path confined to SHARE, or None.

    realpath collapses '..' and resolves symlinks in one shot, so a symlink
    inside the share pointing outside it lands out of the root and is rejected;
    http.server's own handler only blocks lexical '..', not symlink escape.
    """
    rel = unquote(rel)
    if "\x00" in rel:
        return None
    target = os.path.realpath(os.path.join(cfg.SHARE, rel.lstrip("/")))
    return target if within_share(target) else None


def part_paths(rel: str, rid: str = "") -> tuple[str | None, str]:
    """(target, tmp) for a /files write, both confined to SHARE, or (None, '').

    The .rcpart temp is keyed by the client's X-Rc-Id, so a stale partial left from a
    different file of the same name resolves to a *different* temp — the resume starts
    fresh instead of merging new bytes onto old ones and corrupting the result. That
    suffix is the share's own vocabulary: sweep_rcparts() reclaims it and rows_html()
    hides it, so all three live here.
    """
    target = share_target(rel)
    if target is None or target == cfg.SHARE or os.path.isdir(target):
        return None, ""
    # sha1 tags the temp by X-Rc-Id — a filename key, not a security digest, so
    # usedforsecurity=False (unchanged output, and it works on FIPS-restricted hosts).
    digest = hashlib.sha1(rid.encode(), usedforsecurity=False).hexdigest()[:12]
    return target, f"{target}{f'.{digest}' if rid else ''}.rcpart"


def have(tmp: str) -> int:
    """Bytes already on disk for a resumable upload's temp (0 if none)."""
    return os.path.getsize(tmp) if os.path.isfile(tmp) else 0


def share_page(target: str, rel: str) -> bytes:
    return fill(
        FILES_PAGE,
        {
            "__REL__": js(rel.rstrip("/")),
            "__HOST__": html.escape(cfg.HOST),
            "__CRUMB__": crumb_html(rel),
            "__ROWS__": rows_html(target, rel),
        },
    )


def crumb_html(rel: str) -> str:
    """rel arrives still percent-encoded (the raw URL remainder _files hands over).
    Decode each segment, then requote: quoting the encoded form doubled the escapes
    ("my file" -> href /files/my%2520file, a 404, labeled "my%20file")."""
    out = ['<a href="/files">rc-share</a>']
    acc = ""
    for seg in (s for s in rel.split("/") if s):
        seg_dec = unquote(seg)
        acc += "/" + quote(seg_dec)
        out.append(f'<a href="/files{acc}">{html.escape(seg_dec)}</a>')
    return "<span class=sep>/</span>".join(out)


def rows_html(target: str, rel: str) -> str:
    """One <li> per child, dirs first. Each row carries data-d/n/s/t (is-dir, lowercased
    name, size bytes, mtime) so the page can re-sort client-side without a round trip; the
    server default (name, dirs first) is the no-JS fallback. Symlinks whose real target
    escapes SHARE are never listed or linked — the same confinement share_target() enforces."""
    try:
        names = sorted(os.listdir(target))
    except OSError:
        return "<li class=empty>unreadable</li>"  # a permission failure is not "empty"
    base = rel.rstrip("/")
    dirs, files = [], []
    for name in names:
        if name.endswith(".rcpart"):  # in-progress/partial upload — hide it
            continue
        full = os.path.join(target, name)
        if not within_share(os.path.realpath(full)):
            continue
        try:
            st = os.stat(full)
        except OSError:
            continue
        href = f"/files{base}/{quote(name)}"
        # from the stat above; os.stat followed symlinks too
        is_dir = stat.S_ISDIR(st.st_mode)
        data = (
            f'data-d="{int(is_dir)}" data-n="{html.escape(name.lower(), quote=True)}" '
            f'data-s="{st.st_size}" data-t="{int(st.st_mtime)}"'
        )
        if is_dir:
            dirs.append(
                f'<li class=dir {data}><a href="{href}">'
                f"<span class=nm>{html.escape(name)}/</span></a></li>"
            )
        else:
            when = f"{datetime.fromtimestamp(st.st_mtime, MT):%m/%d %H:%M}"
            files.append(
                f'<li {data}><a href="{href}"><span class=nm>{html.escape(name)}'
                f"</span><span class=meta>{human_size(st.st_size)} &middot; "
                f"{when}</span></a></li>"
            )
    rows = dirs + files
    return "\n".join(rows) if rows else "<li class=empty>empty</li>"


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def sweep_rcparts() -> int:
    """Remove abandoned .rcpart temps under SHARE (an interrupted upload never resumed).
    Keyed on mtime, so an in-progress or actively-resuming upload — which keeps writing —
    is never swept. Returns how many were removed."""
    cutoff = time.time() - cfg.RCPART_TTL
    parts = (
        os.path.join(root, name)
        for root, _, files in os.walk(cfg.SHARE)
        for name in files
        if name.endswith(".rcpart")
    )
    n = 0
    for p in parts:
        with contextlib.suppress(OSError):
            if os.path.getmtime(p) < cutoff:
                os.unlink(p)
                n += 1
    return n


def sweep_loop() -> None:
    """The launcher's background sweeper thread."""
    while True:
        if swept := sweep_rcparts():
            cfg.log_event("sweep", "rcparts", str(swept))
        time.sleep(1800)
