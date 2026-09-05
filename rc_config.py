"""Every env-derived setting the launcher reads, plus the audit log line, the project
listing and the TTL cache decorator the rest of the tree shares.

Split out of rc_launcher.py so the git / desk / session / file-share clusters can be their
own modules: launch() needs SHARE and both clusters need log_event, NAME_RE and the env
globals, so extracting a cluster while importing them from the launcher would have made
this repo's only import cycle. Read these as `cfg.NAME` at call time, never
`from rc_config import NAME` — one binding per name is what lets a test redirect PARENT or
SHARE once and have every module follow.

Defaults match this machine; the LaunchAgent sets the rest. Refuses nothing here: the
server checks for the token at startup.
"""

import contextlib
import functools
import os
import re
import socket
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from rc_claude import MT

# realpath'd, like SHARE below: the desk scan and takeover compare cwds read back from
# lsof / /proc (always physical) against this prefix, and a symlinked or trailing-slashed
# ~/projects would silently match nothing.
PARENT = os.path.realpath(
    os.path.expanduser(os.environ.get("RC_PROJECTS_PARENT", "~/projects"))
)
GIT = os.environ.get("RC_GIT_BIN", "git")


def _read_token() -> str:
    """Auth token from the 0600 file install.sh writes — the only place it lives. The
    service files stopped carrying the secret, so `launchctl print` / `systemctl show`
    can't leak it and rotation is write-file + kickstart, no plist surgery. No env
    fallback: an env-carried token is readable via ps/launchctl and inherited by every
    child of the HTTP process — the channel the 2026-08-16 remediation closed."""
    tf = Path(
        os.path.expanduser(
            os.environ.get("RC_LAUNCHER_TOKEN_FILE", "~/.config/rc-launcher/token")
        )
    )
    with contextlib.suppress(OSError):
        return tf.read_text().strip()
    return ""


TOKEN = _read_token()
PORT = int(os.environ.get("RC_LAUNCHER_PORT", "8787"))
BIND = os.environ.get("RC_LAUNCHER_BIND", "0.0.0.0")
SPAWN = os.environ.get("RC_SPAWN", "same-dir")  # same-dir | worktree | session
RESUME = os.environ.get("RC_RESUME", "continue")  # continue | fork | off
TAKEOVER = os.environ.get("RC_TAKEOVER", "1") not in ("0", "off", "")
HOST = socket.gethostname().split(".")[0]
CLAUDE_JSON = os.path.expanduser("~/.claude.json")
# per-project transcripts
CLAUDE_PROJECTS = Path(os.path.expanduser("~/.claude/projects"))
SHARE = os.path.realpath(
    os.path.expanduser(os.environ.get("RC_SHARE_DIR", "~/rc-share"))
)
RCPART_TTL = 6 * 3600  # abandoned .rcpart uploads (no writes in this long) get swept
# SIGINT grace before kill-session
STOP_WAIT = float(os.environ.get("RC_STOP_WAIT", "5"))
# per-project git state is cached this long
GIT_TTL = float(os.environ.get("RC_GIT_TTL", "30"))
# cap a hung `git status`
GIT_STATUS_TIMEOUT = float(os.environ.get("RC_GIT_STATUS_TIMEOUT", "3"))
# desk-session scan is cached this long
DESK_TTL = float(os.environ.get("RC_DESK_TTL", "10"))
LOGIN_TTL = 60.0  # `claude auth status` spawns a process; the phone polls every 5s

NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # no dot: rc-<name> is a tmux target, and a
# dot there parses as session.pane — an untargetable session stop() would think it killed


def log_event(action: str, proj: str, result: str) -> None:
    """One audit line per launch/stop to StandardOutPath (/tmp/rc-launcher.log)."""
    print(
        f"{datetime.now(MT):%Y-%m-%d %H:%M:%S} MT  {action:<6} {proj} -> {result}",
        flush=True,
    )


def project_dir(proj: str) -> str:
    """A project's on-disk root under PARENT — read as cfg.project_dir() at call time,
    so a test that redirects PARENT reaches every caller (rc_git/rc_desk/rc_sessions)."""
    return os.path.join(PARENT, proj)


def projects() -> list[str]:
    try:
        entries = os.listdir(PARENT)
    except FileNotFoundError:
        return []
    return sorted(
        e for e in entries if NAME_RE.match(e) and os.path.isdir(project_dir(e))
    )


class TTLCache[T]:
    """One cached callable: see ttl_cached()."""

    def __init__(self, fn: Callable[..., T], ttl: Callable[[], float]) -> None:
        self._fn, self._ttl = fn, ttl
        self._cache: dict[tuple, tuple[float, T]] = {}
        self._lock = threading.Lock()
        self._inflight: dict[tuple, threading.Lock] = {}  # single-flight per key
        self._gen = 0  # bumped by invalidate(); a fill from an older gen must not land
        functools.update_wrapper(self, fn)

    def __call__(self, *args) -> T:
        # Single-flight: concurrent misses on one key wait for the first computation
        # instead of each forking the work (the page's 5s poll fires whether or not the
        # last one finished, so a slow scan used to fan out into parallel git forks).
        with self._lock:
            if (hit := self._fresh(args)) is not None:
                return hit
            gen = self._gen
            gate = self._inflight.setdefault(args, threading.Lock())
        with gate:
            with self._lock:
                if (hit := self._fresh(args)) is not None:
                    return hit
            value = self._fn(*args)
            with self._lock:
                # an invalidate() while we computed bumped the generation: this result is
                # pre-invalidate, so it must not overwrite a fresher one or reappear stale.
                if gen == self._gen:
                    self._cache[args] = (time.monotonic() + self._ttl(), value)
                # prune this key's lock so _inflight can't grow without bound
                self._inflight.pop(args, None)
        return value

    def _fresh(self, args: tuple) -> T | None:
        hit = self._cache.get(args)
        return hit[1] if hit and hit[0] > time.monotonic() else None

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
            # any fill now in flight is pre-invalidate; its write will be dropped
            self._gen += 1


def ttl_cached[T](
    ttl: Callable[[], float],
) -> Callable[[Callable[..., T]], TTLCache[T]]:
    """Cache the wrapped function's result per positional-argument tuple for ttl() seconds,
    with an .invalidate() that drops every entry.

    One decorator for the three caches the launcher had grown in three shapes (an lru_cache
    over a time bucket for the login probe, a dict+lock keyed per project for git state, a
    tuple+lock+hand-rolled invalidate for the desk scan) — they cache for the same reason:
    the phone polls /status every few seconds and each of these forks a process. ttl is a
    callable, read per call, so a test can set the TTL to 0 and force a rescan.
    """
    return lambda fn: TTLCache(fn, ttl)
