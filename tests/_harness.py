"""Shared test scaffolding: one proc() factory (replacing an earlier proc/_proc split)
and restore_globals(), which snapshots every rc_launcher module attribute / stdlib singleton a
test may reassign and restores it via addCleanup — so a mutated global can't leak into a later
test regardless of run order. (This file is not collected: discover only runs test*.py.)"""

import threading
from types import SimpleNamespace

import rc_launcher

# module attrs tests reassign (SHARE/TOKEN/... and the launch-mode flags)
_ATTRS = ("SHARE", "TOKEN", "PARENT", "STATE_DIR", "CLAUDE_JSON",
          "RESUME", "SPAWN", "TAKEOVER", "log_event")
# stdlib singletons the subprocess-mock rebinds (rc_launcher.subprocess IS the module object)
_STDLIB = ((rc_launcher.subprocess, "run"), (rc_launcher.os, "kill"),
           (rc_launcher.time, "sleep"), (rc_launcher.time, "time"),
           (rc_launcher.os.path, "islink"), (rc_launcher.os, "readlink"))


def proc(returncode=0, stdout="", stderr=""):
    """A stand-in for subprocess.CompletedProcess in the mocked orchestration tests."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def restore_globals(tc):
    """Register addCleanup handlers that restore every mutable rc_launcher global and rebindable
    stdlib singleton to its current value. Call first in setUp, before the test mutates them."""
    for name in _ATTRS:
        tc.addCleanup(setattr, rc_launcher, name, getattr(rc_launcher, name))
    for obj, name in _STDLIB:
        tc.addCleanup(setattr, obj, name, getattr(obj, name))


def serve(tc):
    """Start a loopback rc_launcher.Server on an ephemeral port for tc and register its
    shutdown; returns the bound port. Uses Server (not raw ThreadingHTTPServer) so a client RST
    in a test doesn't dump a framework traceback into the test output."""
    srv = rc_launcher.Server(("127.0.0.1", 0), rc_launcher.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tc.addCleanup(srv.server_close)
    tc.addCleanup(srv.shutdown)
    return srv.server_address[1]
