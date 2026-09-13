"""Phone-editable launcher toggles, persisted beside the token and remembered across
restarts. The launch path reads resume()/spawn() rather than the RESUME/SPAWN env defaults
directly, so a switch flipped from the phone takes effect with no reload; a missing or torn
settings file falls back to those defaults and never breaks a launch. Caveat: once a toggle
is written, its off state is the plain default (continue / same-dir), so the non-default env
values RC_RESUME=off and RC_SPAWN=session|worktree become unreachable from that point."""

import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path

# serialize the read-merge-write so two /settings taps can't race
_LOCK = threading.Lock()

SPAWN = os.environ.get("RC_SPAWN", "same-dir")  # same-dir | worktree | session
RESUME = os.environ.get("RC_RESUME", "continue")  # continue | fork | off
SETTINGS_FILE = Path(
    os.path.expanduser(
        os.environ.get(
            "RC_LAUNCHER_SETTINGS_FILE", "~/.config/rc-launcher/settings.json"
        )
    )
)


def _settings() -> dict:
    """The persisted toggles, kept to real bools. Missing/unreadable/non-UTF8/torn/malformed
    all read as {} (ValueError covers JSONDecodeError and read_text's UnicodeDecodeError), and
    non-bool values are dropped, so nothing on disk can break a launch or flip a toggle."""
    with contextlib.suppress(OSError, ValueError):
        if isinstance(data := json.loads(SETTINGS_FILE.read_text()), dict):
            return {k: v for k, v in data.items() if isinstance(v, bool)}
    return {}


def _toggled(key: str, on: str, off: str, default: str) -> str:
    s = _settings()
    return (on if s[key] else off) if key in s else default


def resume() -> str:  # "fork" branches the thread on resume; else "continue" reopens it
    return _toggled("fork", "fork", "continue", RESUME)


def spawn() -> str:  # "worktree" isolates each session in its own tree; else same-dir
    return _toggled("worktree", "worktree", "same-dir", SPAWN)


def set_toggle(name: str, on: bool) -> tuple[str, str | None]:
    """Persist one toggle (fork | worktree) atomically, mirroring add_root's temp-replace."""
    if name not in ("fork", "worktree"):
        return "badname", f"unknown setting {name!r}"
    tmp = None
    try:
        # read-merge-write is one critical section, else a racing tap is lost
        with _LOCK:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(SETTINGS_FILE.parent), prefix="settings."
            )
            with os.fdopen(fd, "w") as f:
                json.dump(_settings() | {name: on}, f)
            os.replace(tmp, SETTINGS_FILE)
    except OSError as e:
        if tmp:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        return "failed", str(e)
    return "set", None
