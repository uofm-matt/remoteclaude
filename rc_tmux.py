"""tmux, spoken once — for the launcher and for the desk-side guard.

Each had grown its own binary default, its own `has-session` call and its own `rc-{proj}`
naming; two definitions of "is that session alive" drift apart quietly, and the `=name`
exact-match form below is the kind of hard-won detail that has to be in exactly one place.
graceful_stop() is the one confirming close: the desk guard's takeover and the
launcher's stop() (rc_sessions) both go through it, so a ✕ that reports "stopped" has
looked.

Deliberately a cheap leaf — no rc_config import, no token read, no gethostname: the guard
runs this on every desk `claude`.
"""

import os
import shutil
import subprocess
import time

# RC_TMUX_BIN wins, then whatever is on PATH (the desk case), then Homebrew's path: the
# service's minimal launchd PATH has no /opt/homebrew/bin, so `tmux` alone isn't found there.
TMUX = os.environ.get("RC_TMUX_BIN") or shutil.which("tmux") or "/opt/homebrew/bin/tmux"


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    """A tmux control call with its chatter captured, so it stays out of the audit log.
    No OSError guard: a missing tmux must surface on the launch/stop paths; running()
    catches its own FileNotFoundError because status dots are non-essential.
    Every -t below is `=name`: a bare -t prefix-matches, so with rc-alpha absent and
    rc-alpha-sub live, alpha's stop() would C-c the sibling and launch() report
    "already" (verified against tmux 3.x)."""
    return subprocess.run([TMUX, *args], capture_output=True, text=True)


def session_name(proj: str) -> str:
    return f"rc-{proj}"


def has_session(sess: str) -> bool:
    return tmux("has-session", "-t", f"={sess}").returncode == 0


def running() -> set[str]:
    """The projects with a live rc-* session, by name."""
    try:
        out = tmux("list-sessions", "-F", "#{session_name}").stdout
    except FileNotFoundError:  # tmux not installed yet; status is non-essential
        return set()
    return {
        line.removeprefix("rc-") for line in out.splitlines() if line.startswith("rc-")
    }


def graceful_stop(sess: str, wait: float = 5.0) -> bool:
    """Close sess and report whether it is actually gone.

    Graceful first: Ctrl-C TWICE, close together — claude's TUI answers a single one with
    "Press Ctrl-C again to exit" and stays up (verified live on 2.1.260: one C-c, or two
    4s apart, leave it running; two 0.4s apart exit it), so the single C-c this used to
    send never closed anything and every stop fell through to the kill. A clean exit lets
    claude deregister from Anthropic's relay; an abrupt kill-session sends SIGHUP, which
    the relay can't tell apart from the Mac dropping off the network — so the app keeps
    showing the session "connected" until the relay's inactivity timeout (~10 min) evicts
    it. Wait for the pane to exit on its own, kill-session only as the fallback, then
    confirm: a caller that reports "stopped" without confirming is guessing.
    """
    tmux("send-keys", "-t", f"={sess}", "C-c")
    time.sleep(0.3)  # inside the TUI's "again" window, outside its key-repeat debounce
    tmux("send-keys", "-t", f"={sess}", "C-c")
    deadline = time.monotonic() + wait
    while has_session(sess) and time.monotonic() < deadline:
        time.sleep(0.25)
    if has_session(sess):
        tmux("kill-session", "-t", f"={sess}")
    return not has_session(sess)
