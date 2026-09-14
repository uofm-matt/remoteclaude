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


def _claude_pids() -> Iterator[tuple[int, str, bool]]:
    """(pid, cwd, is_rc) of every live claude — is_rc marks a remote-control server. One
    scan feeds every consumer (the desk badge/takeover take the non-RC subset, the
    external-RC badge/stop the RC subset), so their notion of "a claude" can't drift apart:
    a filter fixed in one place but not another would badge sessions the ✕ can't close."""
    for pid in _run([PGREP, "-f", "claude"]).split():
        comm = _run([PS, "-o", "comm=", "-p", pid]).strip()
        if os.path.basename(comm) != "claude":  # skip the launcher, tmux, grep, etc.
            continue
        is_rc = "remote-control" in _run([PS, "-o", "command=", "-p", pid])
        if cwd := _pid_cwd(pid):
            yield int(pid), cwd, is_rc


def _sessions(proj: str, rc: bool) -> list[int]:
    """Live claude pids rooted in proj of the desk (rc=False) or remote-control (rc=True)
    kind. Scoped by cwd, so another project's sessions are never touched."""
    root = cfg.project_dir(proj)
    return [
        pid
        for pid, cwd, is_rc in _claude_pids()
        if is_rc is rc and (cwd == root or cwd.startswith(root + os.sep))
    ]


def desktop_sessions(proj: str) -> list[int]:
    """Desk (non-RC) claude in proj — the clients a resuming remote session would collide
    with, and what takeover closes."""
    return _sessions(proj, rc=False)


def remote_sessions(proj: str) -> list[int]:
    """Remote-control claude in proj started outside the launcher (a launcher tmux rc-
    session's project shows as running() instead) — what the external-RC ✕ closes."""
    return _sessions(proj, rc=True)


def _rel_project(rel: str) -> str:
    """The project a cwd belongs to: "group/name" when the first path segment is a
    category (matching projects()' shape), else the first segment."""
    parts = rel.split(os.sep)
    if parts[0] in cfg.GROUPS and len(parts) > 1:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _scan(rc: bool) -> list[str]:
    """Projects with a live desk (rc=False) or remote-control (rc=True) claude rooted in
    them, keyed as projects() shapes names. Current Claude Code auto-pairs interactive desk
    sessions with the phone, and an RC session started in a terminal is phone-drivable too —
    both are invisible to the launcher's tmux dots, which is exactly what these badges add."""
    added = [(rp + os.sep, label) for label, rp in cfg.extra_roots().items()]
    parent = cfg.PARENT + os.sep
    out = set()
    for _, cwd, is_rc in _claude_pids():
        if is_rc is not rc:
            continue
        for pre, label in added:
            if cwd.startswith(pre):
                out.add(f"{label}/{cwd.removeprefix(pre).split(os.sep)[0]}")
                break
        else:
            if cwd.startswith(parent):
                out.add(_rel_project(cwd.removeprefix(parent)))
    return sorted(out)


# cached so the 5s /status poll doesn't fork pgrep/ps/lsof per viewer per tick;
# .invalidate() makes a just-closed session drop off the next poll.
desk_projects = cfg.ttl_cached(lambda: cfg.DESK_TTL)(lambda: _scan(rc=False))
rc_projects = cfg.ttl_cached(lambda: cfg.DESK_TTL)(lambda: _scan(rc=True))


def _kill_pids(pids: list[int]) -> list[int]:
    """SIGTERM, wait, SIGKILL any straggler — graceful so each claude flushes its transcript
    and deregisters before dying; the thread stays resumable. Returns the pids acted on."""
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    # monotonic: an NTP/DST step must not shorten the SIGKILL grace
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.15)
    for pid in pids:
        if _alive(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
    return pids


def takeover(proj: str) -> list[int]:
    """Close desk claude for proj so a resuming remote session isn't a second client on the
    thread. Returns the pids acted on, for the audit log."""
    return _kill_pids(desktop_sessions(proj))


def close_remote(proj: str) -> list[int]:
    """The external-RC ✕: close remote-control sessions for proj started outside the
    launcher, by killing the process (same graceful SIGTERM/SIGKILL as takeover)."""
    return _kill_pids(remote_sessions(proj))
