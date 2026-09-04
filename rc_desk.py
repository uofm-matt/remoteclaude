"""Desk (non-remote) claude sessions: find them, badge them, close them.

A resuming remote session would be a second client on the thread a desk session already
holds, which is why the same scan feeds both the launcher's badge and its takeover.
Everything here is a process probe (pgrep / ps / lsof), so the badge path is TTL-cached and
the probes tolerate a missing binary.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator

import rc_config as cfg


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


def _desk_claude_pids() -> Iterator[tuple[int, str]]:
    """(pid, cwd) of every live plain (desk) claude — the ONE definition of "desk
    claude" (a claude-named process that is not a remote-control server), shared by
    the badge scan and the kill paths so their scopes cannot drift apart: a filter
    fixed in one copy but not the other would mean a badge advertising sessions the
    ✕/takeover can't close, or a takeover killing sessions the badge never showed."""
    for pid in _run([PGREP, "-f", "claude"]).split():
        comm = _run([PS, "-o", "comm=", "-p", pid]).strip()
        if os.path.basename(comm) != "claude":  # skip the launcher, grep, etc.
            continue
        if "remote-control" in _run([PS, "-o", "command=", "-p", pid]):
            continue  # an RC server — the launcher's own tmux dot shows it
        if cwd := _pid_cwd(pid):
            yield int(pid), cwd


def desktop_sessions(proj: str) -> list[int]:
    """PIDs of live desk claude sessions whose cwd is inside proj — the clients a
    resuming remote session would collide with. Scoped by cwd, so sessions for any
    other project are never touched."""
    root = os.path.join(cfg.PARENT, proj)
    return [
        pid
        for pid, cwd in _desk_claude_pids()
        if cwd == root or cwd.startswith(root + os.sep)
    ]


def _desk_scan() -> list[str]:
    """Projects with a live desk claude rooted inside them. Current Claude Code
    auto-pairs interactive sessions with the phone app, so these are phone-drivable —
    but invisible to the launcher's tmux-based dots. (bridge-pointer.json was rejected
    as the signal: live desk sessions don't reliably write one, and stale ones point
    at dead pids.)"""
    root = cfg.PARENT + os.sep
    return sorted(
        {
            cwd.removeprefix(root).split(os.sep)[0]
            for _, cwd in _desk_claude_pids()
            if cwd.startswith(root)
        }
    )


# cached so the 5s /status poll doesn't fork pgrep/ps/lsof per viewer per tick;
# .invalidate() is how desk_stop makes a just-closed session drop off the next poll.
desk_projects = cfg.ttl_cached(lambda: cfg.DESK_TTL)(_desk_scan)


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
