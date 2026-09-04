"""Shared test scaffolding: one proc() factory (replacing an earlier proc/_proc split),
respond()/desk() — the one canned answer for the pgrep/ps/lsof probes that six tests
used to re-implement inline — and restore_globals(), which snapshots every rc_launcher
module attribute / stdlib singleton a test may reassign and restores it via addCleanup,
so a mutated global can't leak into a later test regardless of run order. (This file is
not collected: discover only runs test*.py.)"""

import os
import shutil
import tempfile
import threading
import unittest.mock
from itertools import pairwise
from types import SimpleNamespace

import rc_launcher

# module attrs tests reassign (SHARE/TOKEN/... and the launch-mode flags)
_ATTRS = (
    "SHARE",
    "TOKEN",
    "PARENT",
    "STATE_DIR",
    "CLAUDE_JSON",
    "CLAUDE_PROJECTS",
    "RESUME",
    "SPAWN",
    "TAKEOVER",
    "log_event",
    "_desk_cache",
)
# stdlib singletons the subprocess-mock rebinds (rc_launcher.subprocess IS the module object)
_STDLIB = (
    (rc_launcher.subprocess, "run"),
    (rc_launcher.os, "kill"),
    (rc_launcher.time, "sleep"),
    (rc_launcher.time, "time"),
    (rc_launcher.os.path, "islink"),
    (rc_launcher.os, "readlink"),
)


def proc(returncode=0, stdout="", stderr=""):
    """A stand-in for subprocess.CompletedProcess in the mocked orchestration tests."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def desk(cwd, comm="claude", command="claude --continue"):
    """One live process as _desk_claude_pids() sees it: what `ps -o comm=` reports, what
    `ps -o command=` reports, and the cwd lsof hands back. The defaults describe a plain
    desk claude — the cases that must be FILTERED name the field that filters them."""
    return comm, command, cwd


def respond(cmd, desk_procs, responses):
    """Answer one mocked subprocess.run. The desk-claude probes (`pgrep -f claude` and
    the per-pid `ps -o comm=` / `ps -o command=` / `lsof -Fn`) come from desk_procs
    {pid: desk(...)}; everything else falls through to the substring table."""
    key = " ".join(map(str, cmd))
    if desk_procs:
        if "pgrep" in key:
            return proc(stdout="\n".join(desk_procs) + "\n")
        # pairwise, not an index: `tmux capture-pane -t =rc-x -p` ends on a bare -p
        pid = next((b for a, b in pairwise(cmd) if a == "-p"), None)
        if pid in desk_procs:
            comm, command, cwd = desk_procs[pid]
            if "comm=" in key:
                return proc(stdout=f"{comm}\n")
            if "command=" in key:
                return proc(stdout=f"{command}\n")
            if "-Fn" in key:
                return proc(stdout=f"n{cwd}\n")
    return next((r for pat, r in responses.items() if pat in key), proc())


def restore_globals(tc):
    """Register addCleanup handlers that restore every mutable rc_launcher global and rebindable
    stdlib singleton to its current value. Call first in setUp, before the test mutates them."""
    for name in _ATTRS:
        tc.addCleanup(setattr, rc_launcher, name, getattr(rc_launcher, name))
    for obj, name in _STDLIB:
        tc.addCleanup(setattr, obj, name, getattr(obj, name))


def share_dir(tc):
    """A tmp SHARE for tc, removed on cleanup. realpath'd the way the real SHARE is at
    import: mkdtemp hands back /var/..., confinement checks see /private/var/..."""
    path = os.path.realpath(tempfile.mkdtemp())
    tc.addCleanup(shutil.rmtree, path, True)
    return path


def spawn_ok():
    """The two tmux answers a clean launch needs: no session yet, new pane alive."""
    return {"has-session": proc(returncode=1), "pane_dead": proc(stdout="0\n")}


def env(tc, **values):
    """Set process env vars for the duration of tc. patch.dict restores what was there
    before (rather than deleting the key), so a preset RC_SNAPSHOT survives the test."""
    patch = unittest.mock.patch.dict(os.environ, values)
    patch.start()
    tc.addCleanup(patch.stop)


def serve(tc):
    """Start a loopback rc_launcher.Server on an ephemeral port for tc and register its
    shutdown; returns the bound port. Uses Server (not raw ThreadingHTTPServer) so a client RST
    in a test doesn't dump a framework traceback into the test output."""
    srv = rc_launcher.Server(("127.0.0.1", 0), rc_launcher.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tc.addCleanup(srv.server_close)
    tc.addCleanup(srv.shutdown)
    return srv.server_address[1]
