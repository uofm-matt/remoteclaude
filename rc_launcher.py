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

import contextlib
import functools
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from rc_claude import CLAUDE, MT, auth_status
from rc_state import RANK as _RANK, STATE_DIR, valid_states
from rc_templates import FILES_PAGE, PAGE

PARENT = os.path.expanduser(os.environ.get("RC_PROJECTS_PARENT", "~/projects"))
TMUX = os.environ.get("RC_TMUX_BIN", "/opt/homebrew/bin/tmux")
GIT = os.environ.get("RC_GIT_BIN", "git")
TOKEN = os.environ.get("RC_LAUNCHER_TOKEN", "")
PORT = int(os.environ.get("RC_LAUNCHER_PORT", "8787"))
BIND = os.environ.get("RC_LAUNCHER_BIND", "0.0.0.0")
SPAWN = os.environ.get("RC_SPAWN", "same-dir")  # same-dir | worktree | session
RESUME = os.environ.get("RC_RESUME", "continue")  # continue | fork | off
TAKEOVER = os.environ.get("RC_TAKEOVER", "1") not in ("0", "off", "")
HOST = socket.gethostname().split(".")[0]
CLAUDE_JSON = os.path.expanduser("~/.claude.json")
SHARE = os.path.realpath(os.path.expanduser(os.environ.get("RC_SHARE_DIR", "~/rc-share")))
RCPART_TTL = 6 * 3600  # abandoned .rcpart uploads (no writes in this long) get swept
GIT_TTL = float(os.environ.get("RC_GIT_TTL", "15"))  # per-project git state is cached this long
GIT_STATUS_TIMEOUT = float(os.environ.get("RC_GIT_STATUS_TIMEOUT", "3"))  # cap a hung `git status`

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def log_event(action: str, proj: str, result: str) -> None:
    """One audit line per launch/stop to StandardOutPath (/tmp/rc-launcher.log)."""
    print(f"{datetime.now(MT):%Y-%m-%d %H:%M:%S} MT  {action:<6} {proj} -> {result}",
          flush=True)


@functools.lru_cache(maxsize=1)
def _login_status(_bucket: int) -> str:
    return auth_status()[0]  # shared probe; the badge needs only the state


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
        d = json.loads(Path(CLAUDE_JSON).read_text())
    except FileNotFoundError:
        return  # no ~/.claude.json yet — nothing to pre-trust
    except (OSError, json.JSONDecodeError) as e:
        log_event("trust", proj, f"skip: {e}")  # unreadable/corrupt: surface it, don't 500 the launch
        return
    entry = d.setdefault("projects", {}).setdefault(key, {})
    if entry.get("hasTrustDialogAccepted"):
        return
    entry.setdefault("allowedTools", [])
    entry.setdefault("mcpServers", {})
    entry["hasTrustDialogAccepted"] = True
    tmp = CLAUDE_JSON + ".rctmp"
    Path(tmp).write_text(json.dumps(d, indent=2))
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


_git_cache: dict[str, tuple[float, dict | None]] = {}
_git_lock = threading.Lock()


def _git_state(proj: str) -> dict | None:
    """{'b': branch, 'd': dirty} for proj's working tree, or None when it isn't a git repo.
    One `git status --porcelain=v2 --branch` yields both: the '# branch.head' header names the
    branch; any non-'#' line (a tracked change or an untracked file) means the tree is dirty."""
    path = os.path.join(PARENT, proj)
    try:
        out = subprocess.run([GIT, "-C", path, "status", "--porcelain=v2", "--branch"],
                             capture_output=True, text=True, timeout=GIT_STATUS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        # git missing under the minimal launchd PATH, or a hung/slow repo (index.lock, slow
        # disk): drop the badge rather than block a page() worker forever. TimeoutExpired is
        # NOT an OSError, so it must be named explicitly.
        return None
    if out.returncode:  # not a work tree (or a git error) — treat as "no git info"
        return None
    branch, dirty = "", False
    for ln in out.stdout.splitlines():
        if ln.startswith("# branch.head "):
            branch = ln.removeprefix("# branch.head ")
        elif ln and not ln.startswith("#"):
            dirty = True
    return {"b": branch, "d": dirty}


def _git_state_cached(proj: str) -> dict | None:
    now = time.monotonic()
    with _git_lock:
        if (hit := _git_cache.get(proj)) and hit[0] > now:
            return hit[1]
    state = _git_state(proj)
    with _git_lock:
        _git_cache[proj] = (now + GIT_TTL, state)
    return state


def git_states(projs: list[str] | None = None) -> dict[str, dict]:
    """{project: {'b': branch, 'd': dirty}} for every project that is a git repo, so the phone
    can show branch + dirty before you tap Launch — same-dir lands a remote turn on the very
    tree you edit locally, so knowing what's there first pairs with the RC_SNAPSHOT net.
    Fanned out across projects and cached (RC_GIT_TTL) so dozens of repos don't refork git on
    every page load / pull-to-refresh."""
    projs = projects() if projs is None else projs
    if not projs:
        return {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        return {p: s for p, s in zip(projs, ex.map(_git_state_cached, projs), strict=True) if s}


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
    for d in valid_states(STATE_DIR):
        proj, st = d.get("project") or "", d["state"]  # st is already a RANK key (valid_states)
        if proj and _RANK[st] > _RANK.get(out.get(proj, ""), 0):
            out[proj] = st
    return out


def death_reason(sess: str) -> str:
    """Why a just-launched RC session died, read from its dead pane."""
    out = subprocess.run([TMUX, "capture-pane", "-t", sess, "-p"],
                         capture_output=True, text=True).stdout
    last = next((s for ln in reversed(out.splitlines())
                 if (s := ln.strip()) and not s.startswith("Pane is dead")), "")
    low = last.lower()
    if "trust" in low:
        return "untrusted dir"
    if any(w in low for w in ("auth", "logged out", "log in", "login", "credential")):
        return "login expired — run `claude /login` on the Mac"
    return last[:80] or "exited immediately"


def snapshot(proj: str) -> str | None:
    """Non-destructive git checkpoint before a remote turn can touch the tree.

    Opt-in via RC_SNAPSHOT — same-dir means a phone-driven turn lands on the same
    working tree you edit locally, so this parks the current tree+index as a stash
    commit under refs/rc-snapshots/ (kept off `git stash list`). Recover with
    `git stash apply <ref>`. Returns the ref, or None if off / clean / not a repo.
    """
    if not os.environ.get("RC_SNAPSHOT"):
        return None
    path = os.path.join(PARENT, proj)
    if subprocess.run([GIT, "-C", path, "rev-parse", "--is-inside-work-tree"],
                      capture_output=True).returncode != 0:
        return None
    sha = subprocess.run([GIT, "-C", path, "stash", "create"],
                         capture_output=True, text=True).stdout.strip()
    if not sha:
        return None
    ref = f"refs/rc-snapshots/{proj}/{int(time.time())}"
    subprocess.run([GIT, "-C", path, "update-ref", "-m", f"rc-snapshot {proj}", ref, sha],
                   capture_output=True)
    return ref


def _tool(name: str, *fallbacks: str) -> str:
    """Absolute path to a helper binary. The service runs under a minimal
    launchd/systemd PATH that omits /usr/sbin, so a bare 'lsof' isn't found —
    resolve it up front and fall back to the known locations."""
    return shutil.which(name) or next((p for p in fallbacks if os.path.exists(p)), name)


LSOF = _tool("lsof", "/usr/sbin/lsof", "/usr/bin/lsof")
PGREP = _tool("pgrep", "/usr/bin/pgrep")
PS = _tool("ps", "/bin/ps", "/usr/bin/ps")


def _run(cmd: list[str]) -> str:
    """stdout of a helper tool, tolerating a missing binary so takeover degrades
    to a no-op instead of aborting the launch it guards."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except OSError:
        return ""


def _pid_cwd(pid: str) -> str | None:
    link = f"/proc/{pid}/cwd"  # Linux: read the cwd symlink; macOS falls to lsof
    if os.path.islink(link):
        with contextlib.suppress(OSError):
            return os.readlink(link)
        return None
    out = _run([LSOF, "-a", "-d", "cwd", "-p", pid, "-Fn"])
    return next((ln[1:] for ln in out.splitlines() if ln.startswith("n")), None)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def desktop_sessions(proj: str) -> list[int]:
    """PIDs of live *desktop* claude sessions (VS Code or terminal) whose cwd is
    inside proj — claude processes rooted there that aren't a remote-control
    server. These are the clients a resuming remote session would collide with.
    Scoped by cwd, so sessions for any other project are never touched."""
    root = os.path.join(PARENT, proj)
    pids = []
    for pid in _run([PGREP, "-f", "claude"]).split():
        comm = _run([PS, "-o", "comm=", "-p", pid]).strip()
        if os.path.basename(comm) != "claude":  # skip the launcher, grep, etc.
            continue
        if "remote-control" in _run([PS, "-o", "command=", "-p", pid]):
            continue  # an RC server, not a desktop client
        cwd = _pid_cwd(pid)
        if cwd and (cwd == root or cwd.startswith(root + os.sep)):
            pids.append(int(pid))
    return pids


def takeover(proj: str) -> list[int]:
    """Close desktop claude sessions for proj so a resuming remote session isn't
    a second client on the thread. SIGTERM first (graceful: lets each flush its
    transcript so --continue reads the latest), wait for exit, SIGKILL any
    straggler. Returns the pids acted on, for the audit log."""
    pids = desktop_sessions(proj)
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 5
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.15)
    for pid in pids:
        if _alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
    return pids


def fresh_cmd(proj: str) -> list[str]:
    # Subcommand form with an explicit --spawn: worktree/session are isolated by
    # design, and omitting --spawn here prompts for a mode and hangs headless.
    return [CLAUDE, "remote-control", "--name", proj, "--spawn", SPAWN]


def launch_cmd(proj: str) -> tuple[list[str], bool]:
    """The claude invocation for proj, and whether it resumes. Resume is the
    top-level flag form `claude --continue --remote-control <proj>` (the
    remote-control subcommand can't resume); it exists only for same-dir,
    doesn't take --spawn, and reloads the project's most recent thread so the
    phone opens where you left off. Otherwise launch fresh."""
    if RESUME in ("continue", "fork") and SPAWN == "same-dir":
        cmd = [CLAUDE, "--continue"]
        if RESUME == "fork":
            cmd.append("--fork-session")
        return [*cmd, "--remote-control", proj], True
    return fresh_cmd(proj), False


def _spawn(sess: str, proj: str, cmd: list[str], env_opts: list[str]) -> str:
    """Start cmd detached in tmux, rooted in proj. Returns '' if it's still alive
    after the startup window, else the death reason (and kills the session). RC
    dies within ~2s on any startup error — untrusted dir, expired login, or
    nothing to --continue — taking its tmux session with it; remain-on-exit
    holds the dead pane so death_reason can read WHY."""
    subprocess.run(
        [TMUX, "new-session", "-d", "-s", sess, *env_opts,
         "-c", os.path.join(PARENT, proj), " ".join(cmd)],
        check=False,
    )
    subprocess.run([TMUX, "set-option", "-t", sess, "remain-on-exit", "on"],
                   capture_output=True)
    time.sleep(3)
    dead = subprocess.run([TMUX, "list-panes", "-t", sess, "-F", "#{pane_dead}"],
                          capture_output=True, text=True).stdout.strip()
    if dead != "0":
        reason = death_reason(sess)
        subprocess.run([TMUX, "kill-session", "-t", sess], capture_output=True)
        return reason
    subprocess.run([TMUX, "set-option", "-t", sess, "remain-on-exit", "off"],
                   capture_output=True)
    return ""


def launch(proj: str) -> tuple[str, str | None]:
    sess = f"rc-{proj}"
    if subprocess.run([TMUX, "has-session", "-t", sess],
                      capture_output=True).returncode == 0:
        return "already", None
    ensure_trusted(proj)
    if snap := snapshot(proj):
        log_event("snap", proj, snap)
    # Tag the session env so the state hook fires for remote (phone-driven)
    # sessions only, not local desk ones. The sessions the RC server spawns
    # inherit this env, so rc_status.py can tell when a remote turn is live on
    # the shared working tree.
    env_opts = ["-e", f"RC_REMOTE={sess}", "-e", f"RC_PROJECT={proj}",
                "-e", f"RC_SHARE_DIR={SHARE}"]
    if os.environ.get("RC_STATE_DIR"):
        env_opts += ["-e", f"RC_STATE_DIR={os.environ['RC_STATE_DIR']}"]
    cmd, resuming = launch_cmd(proj)
    # Resume reopens the project's last thread, so the phone would be a second
    # client on it. Hand the project off first: close any live desktop (non-RC)
    # claude rooted inside it, letting it flush its transcript, then launch.
    if resuming and TAKEOVER and (killed := takeover(proj)):
        log_event("takeover", proj, ",".join(map(str, killed)))
    reason = _spawn(sess, proj, cmd, env_opts)
    # A brand-new / never-used project has no thread to --continue; the resume
    # form exits 1. Fall back to a plain fresh launch so create-and-start works.
    if reason and resuming:
        log_event("resume", proj, "no history; fresh launch")
        reason = _spawn(sess, proj, fresh_cmd(proj), env_opts)
    return ("failed", reason) if reason else ("launched", None)


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
    Path(path, "CLAUDE.md").write_text(f"# {proj}\n")
    return "created", None


def js(x: object) -> str:
    """JSON for splicing into a <script> body: escape '<' so a value containing
    '</script>' (a file/dir named that, dropped into SHARE over SMB) can't break out."""
    return json.dumps(x).replace("<", "\\u003c")


def _fill(template: str, values: dict[str, str]) -> bytes:
    """Fill __PLACEHOLDER__s in one pass, so injected data (a project dir named __LOGIN__,
    which NAME_RE permits) can't be re-scanned and rewritten by a later replacement."""
    pat = re.compile("|".join(map(re.escape, values)))
    return pat.sub(lambda m: values[m.group()], template).encode()


def page() -> bytes:
    projs = projects()  # computed once and shared with git_states so PARENT is scanned once
    return _fill(PAGE, {
        "__PROJECTS__": js(projs),
        "__RUNNING__": js(sorted(running())),
        "__STATES__": js(session_states()),
        "__GITSTATES__": js(git_states(projs)),
        "__LOGIN__": js(login_status()),
        "__HOST__": html.escape(HOST),
    })


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def crumb_html(rel: str) -> str:
    out = ['<a href="/files">rc-share</a>']
    acc = ""
    for seg in (s for s in rel.split("/") if s):
        acc += "/" + quote(seg)
        out.append(f'<a href="/files{acc}">{html.escape(seg)}</a>')
    return '<span class=sep>/</span>'.join(out)


def rows_html(target: str, rel: str) -> str:
    """One <li> per child, dirs first. Each row carries data-d/n/s/t (is-dir, lowercased
    name, size bytes, mtime) so the page can re-sort client-side without a round trip; the
    server default (name, dirs first) is the no-JS fallback. Symlinks whose real target
    escapes SHARE are never listed or linked — the same confinement share_target() enforces."""
    try:
        names = sorted(os.listdir(target))
    except OSError:
        return '<li class=empty>empty</li>'
    base = rel.rstrip("/")
    dirs, files = [], []
    for name in names:
        if name.endswith(".rcpart"):  # in-progress/partial upload — hide it
            continue
        full = os.path.join(target, name)
        real = os.path.realpath(full)
        if not within_share(real):
            continue
        try:
            st = os.stat(full)
        except OSError:
            continue
        href = f"/files{base}/{quote(name)}"
        is_dir = os.path.isdir(full)
        data = (f'data-d="{int(is_dir)}" data-n="{html.escape(name.lower(), quote=True)}" '
                f'data-s="{st.st_size}" data-t="{int(st.st_mtime)}"')
        if is_dir:
            dirs.append(f'<li class=dir {data}><a href="{href}">'
                        f'<span class=nm>{html.escape(name)}/</span></a></li>')
        else:
            when = f"{datetime.fromtimestamp(st.st_mtime, MT):%m/%d %H:%M}"
            files.append(f'<li {data}><a href="{href}"><span class=nm>{html.escape(name)}'
                         f'</span><span class=meta>{human_size(st.st_size)} &middot; '
                         f'{when}</span></a></li>')
    rows = dirs + files
    return "\n".join(rows) if rows else '<li class=empty>empty</li>'


def share_page(target: str, rel: str) -> bytes:
    return _fill(FILES_PAGE, {
        "__REL__": js(rel.rstrip("/")),
        "__HOST__": html.escape(HOST),
        "__CRUMB__": crumb_html(rel),
        "__ROWS__": rows_html(target, rel),
    })


def within_share(p: str) -> bool:
    """The single definition of 'this real path is inside the share', used by every
    read and write path so the confinement boundary can't drift between them."""
    return p == SHARE or p.startswith(SHARE + os.sep)


def share_target(rel: str) -> str | None:
    """Resolve a /files/<rel> request to a path confined to SHARE, or None.

    realpath collapses '..' and resolves symlinks in one shot, so a symlink
    inside the share pointing outside it lands out of the root and is rejected;
    http.server's own handler only blocks lexical '..', not symlink escape.
    """
    rel = unquote(rel)
    if "\x00" in rel:
        return None
    target = os.path.realpath(os.path.join(SHARE, rel.lstrip("/")))
    return target if within_share(target) else None


def sweep_rcparts() -> int:
    """Remove abandoned .rcpart temps under SHARE (an interrupted upload never resumed).
    Keyed on mtime, so an in-progress or actively-resuming upload — which keeps writing —
    is never swept. Returns how many were removed."""
    cutoff = time.time() - RCPART_TTL
    n = 0
    for root, _, files in os.walk(SHARE):
        for name in files:
            if not name.endswith(".rcpart"):
                continue
            p = os.path.join(root, name)
            with contextlib.suppress(OSError):
                if os.path.getmtime(p) < cutoff:
                    os.unlink(p)
                    n += 1
    return n


def sweep_loop() -> None:
    while True:
        if swept := sweep_rcparts():
            log_event("sweep", "rcparts", str(swept))
        time.sleep(1800)


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 keep-alive so a chunked upload reuses ONE connection (a single TCP
    # slow-start) instead of a fresh handshake + slow-start per chunk — the difference
    # between ~line-rate and a per-chunk ramp. Every response sets Content-Length, which
    # is what makes persistent connections framable. timeout reaps idle kept connections.
    protocol_version = "HTTP/1.1"
    timeout = 60

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8",
              set_cookie: bool = False, close: bool = False):
        if code >= 400:  # non-2xx used to be invisible (log_message is silenced) — trace it
            log_event("http", f"{self.command} {self.path}", str(code))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"rc_token={TOKEN}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000",
            )
        if close:  # a bail-out that never read the request body must end the connection,
            self.close_connection = True  # or that unread body desyncs the next request
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # advertise the close (set by close=, _guard_body, or a client Connection: close)
        # so the client won't try to reuse a socket we're about to drop.
        if self.close_connection:
            self.send_header("Connection", "close")
        super().end_headers()

    def _json(self, payload: dict):
        self._send(200, json.dumps(payload).encode(), "application/json")

    def _authed(self, q: dict) -> bool:
        """Token via ?token= (first contact / bookmark) or the rc_token cookie set
        on that first load, so the token stays out of later request URLs and logs."""
        if not TOKEN:
            return False
        if hmac.compare_digest(q.get("token", [""])[0], TOKEN):
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return "rc_token" in cookie and hmac.compare_digest(cookie["rc_token"].value, TOKEN)

    def _files(self, path: str):
        """Read-only browse/download under SHARE, behind the same token gate.
        share_target() resolves '..' and symlink escapes away, so this can only
        reach files inside SHARE (never ~/projects or $HOME)."""
        target = share_target(path[len("/files"):])
        if target is None:
            return self._send(404, b"not found")
        if os.path.isfile(target):
            return self._stream_file(target)
        if os.path.isdir(target) or target == SHARE:
            # set the cookie here too: loading /files directly (not via /) must still
            # authenticate the cookie-based upload/HEAD/download/delete requests it fires.
            return self._send(200, share_page(target, path[len("/files"):]), set_cookie=True)
        return self._send(404, b"not found")

    def _stream_file(self, target: str):
        self.send_response(200)
        self.send_header("Content-Type",
                         mimetypes.guess_type(target)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(os.path.getsize(target)))
        self.send_header("Content-Disposition",
                         f"inline; filename*=UTF-8''{quote(os.path.basename(target))}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError), open(target, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 65536)

    def _part(self, path: str, rid: str = "") -> tuple[str | None, str]:
        """(target, tmp) for a /files write, both confined to SHARE, or (None, '').
        The .rcpart temp is keyed by the client's X-Rc-Id, so a stale partial left from a
        different file of the same name resolves to a *different* temp — the resume starts
        fresh instead of merging new bytes onto old ones and corrupting the result."""
        target = share_target(path[len("/files"):])
        if target is None or target == SHARE or os.path.isdir(target):
            return None, ""
        # sha1 tags the .rcpart temp by X-Rc-Id — a filename key, not a security digest, so
        # usedforsecurity=False (unchanged output, and it works on FIPS-restricted hosts).
        tag = f".{hashlib.sha1(rid.encode(), usedforsecurity=False).hexdigest()[:12]}" if rid else ""
        return target, f"{target}{tag}.rcpart"

    @staticmethod
    def _uint(raw: str | None, default: int) -> int | None:
        """An optional non-negative-int header: the default when absent, None when
        present-but-invalid (negative, non-numeric, empty) so the caller can 400."""
        if raw is None:
            return default
        return int(raw) if raw.isdigit() else None

    @staticmethod
    def _have(tmp: str) -> int:
        """Bytes already on disk for a resumable upload's temp (0 if none)."""
        return os.path.getsize(tmp) if os.path.isfile(tmp) else 0

    def _upload(self, path: str):
        """Write or RESUME an upload into SHARE. Bytes stream into a .rcpart temp at
        X-Rc-Offset; the partial is KEPT across interruptions so a dropped upload
        resumes (via HEAD -> X-Rc-Have) instead of restarting. When the temp reaches
        X-Rc-Total it's atomically renamed to the final name. Confined like every path."""
        target, tmp = self._part(path, self.headers.get("X-Rc-Id", ""))
        if target is None:
            return self._send(403, b'{"error":"bad target"}', "application/json", close=True)
        if not within_share(os.path.dirname(target)) or not os.path.isdir(os.path.dirname(target)):
            return self._send(404, b'{"error":"no such folder"}', "application/json", close=True)
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            return self._send(411, b'{"error":"length required"}', "application/json", close=True)
        length = int(length)
        offset = self._uint(self.headers.get("X-Rc-Offset"), 0)
        if offset is None:
            return self._send(400, b'{"error":"bad offset"}', "application/json", close=True)
        total = self._uint(self.headers.get("X-Rc-Total"), offset + length)
        if total is None or total <= 0 or total < offset:
            return self._send(400, b'{"error":"bad total"}', "application/json", close=True)
        have = self._have(tmp)
        if offset > have:  # gap: client is ahead of us — tell it what we actually have
            return self._send(409, f'{{"error":"gap","have":{have}}}'.encode(),
                              "application/json", close=True)
        # Single-writer assumption: the sequential (await-per-file) browser/app clients never
        # run two PUTs to the same target+id concurrently, so the .rcpart needs no lock. An
        # overlap would corrupt only the partial (reclaimed by the sweep) — os.replace keeps the
        # finalized file atomic regardless.
        remaining = length
        try:
            with open(tmp, "r+b" if have else "wb") as f:
                f.seek(offset)
                f.truncate(offset)
                while remaining > 0 and (chunk := self.rfile.read(min(65536, remaining))):
                    f.write(chunk)
                    remaining -= len(chunk)
        except (ConnectionError, TimeoutError):
            pass  # link dropped/stalled mid-body: keep the partial for the next resume
        except OSError as e:  # a real disk error (ENOSPC/EACCES): surface it, keep the partial
            log_event("upload", os.path.basename(target), f"err {e}")
        if remaining:  # body not fully drained (drop or write error) — end the connection so
            self.close_connection = True  # its leftover bytes can't be read as a next request
        now = self._have(tmp)
        if now >= total:
            os.replace(tmp, target)
            log_event("upload", os.path.relpath(target, SHARE), "ok")
            return self._json({"ok": True, "done": True, "name": os.path.basename(target)})
        with contextlib.suppress(OSError):
            self._json({"ok": True, "done": False, "have": now})

    def _delete(self, path: str):
        """Delete a file inside SHARE. Same confinement as read/write; only regular
        files (never the root, never a directory)."""
        target = share_target(path[len("/files"):])
        if target is None or target == SHARE or not os.path.isfile(target):
            return self._send(403, b'{"error":"bad target"}', "application/json")
        os.unlink(target)
        log_event("delete", os.path.relpath(target, SHARE), "ok")
        self._json({"ok": True})

    def _guard_body(self) -> None:
        """GET/HEAD/DELETE never read a request body; under keep-alive an unread body would
        desync the next request on the socket, so close the connection if one was sent."""
        if self.headers.get("Content-Length") or self.headers.get("Transfer-Encoding"):
            self.close_connection = True

    def do_GET(self):
        self._guard_body()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._send(403, b"forbidden")
        if u.path == "/":
            return self._send(200, page(), set_cookie=True)
        if u.path == "/files" or u.path.startswith("/files/"):
            return self._files(u.path)
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

    def do_PUT(self):
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):  # PUT carries a body we won't read -> close
            return self._send(403, b"forbidden", close=True)
        if u.path == "/files" or u.path.startswith("/files/"):
            return self._upload(u.path)
        self._send(404, b"not found", close=True)

    def do_DELETE(self):
        self._guard_body()
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):
            return self._send(403, b"forbidden")
        if u.path == "/files" or u.path.startswith("/files/"):
            return self._delete(u.path)
        self._send(404, b"not found")

    def do_HEAD(self):
        """Report how many bytes of a resumable upload are already on disk, so a client
        can resume from there: X-Rc-Have = size of the target's .rcpart (0 if none)."""
        self._guard_body()
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):
            return self._send(403, b"forbidden")
        have = 0
        if u.path == "/files" or u.path.startswith("/files/"):
            _, tmp = self._part(u.path, self.headers.get("X-Rc-Id", ""))
            if tmp:
                have = self._have(tmp)
        self.send_response(200)
        self.send_header("X-Rc-Have", str(have))
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, format, *args):
        pass


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # a client RST / dropped connection mid-request is normal on a lossy link (Starlink):
        # keep it out of the error log (which never rotates). Only real errors get a traceback.
        if not isinstance(sys.exc_info()[1], (ConnectionError, BrokenPipeError)):
            super().handle_error(request, client_address)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("RC_LAUNCHER_TOKEN not set (the LaunchAgent supplies it)")
    print(f"rc-launcher on {BIND}:{PORT} parent={PARENT} spawn={SPAWN}")
    threading.Thread(target=sweep_loop, daemon=True).start()
    Server((BIND, PORT), Handler).serve_forever()
