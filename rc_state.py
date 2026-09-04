"""Shared state vocabulary for the remote-control awareness system.

The session cluster (rc_sessions.py), the status reader (rc_status.py), and the Claude Code
hook (rc_state_hook.py) all import these, so the state names, the rank, the directory, and
the TTL can't drift apart between them. Before this was extracted, a state renamed in the
hook's event map simply fell out of the launcher's rank filter with no error.
"""

import json
import os
import time
from pathlib import Path
from types import MappingProxyType

STATE_DIR = Path(os.environ.get("RC_STATE_DIR", Path.home() / ".cache" / "rc-state"))
STATE_TTL = float(os.environ.get("RC_STATE_TTL", "3600"))

# turn state -> priority; these keys are the entire state vocabulary
RANK = MappingProxyType({"working": 3, "waiting": 2, "idle": 1})

# Claude Code hook event -> turn state (every value must be a RANK key)
EVENT_STATE = MappingProxyType(
    {
        "UserPromptSubmit": "working",
        "Notification": "waiting",
        "Stop": "idle",
        "SubagentStop": "idle",
        "SessionStart": "idle",
    }
)


def valid_states(state_dir: Path, now: float | None = None) -> list[dict]:
    """State files in state_dir that parse, are fresh (within STATE_TTL), and carry a known
    state — the single read filter behind the launcher's session_states() and rc_status's
    live(), so the on-disk schema and the staleness rule live in one place, not two. A corrupt
    or unreadable file is skipped, never raised (rc_status runs in the zsh RPROMPT). state_dir
    is a parameter because each caller redirects its own STATE_DIR (tests, and the env override
    resolved at import in each module)."""
    now = time.time() if now is None else now
    out = []
    for f in state_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("state") in RANK and now - d.get("ts", 0) <= STATE_TTL:
            out.append(d)
    return out
