#!/usr/bin/env python3
"""Claude Code hook: record a remote-control session's turn state for local awareness.

Wired (guarded by $RC_REMOTE) into UserPromptSubmit / Notification / Stop /
SubagentStop / SessionStart / SessionEnd in ~/.claude/settings.json. The launcher
tags remote tmux sessions with RC_REMOTE, and the sessions the RC server spawns
inherit it, so this only fires for phone-driven sessions — never a local desk one.

Writes one JSON file per session under RC_STATE_DIR; rc_status.py reads them so a
local shell can tell when a remote turn is live on the shared working tree.
"""

import json
import os
import sys
import time
from pathlib import Path

STATE = {
    "UserPromptSubmit": "working",
    "Notification": "waiting",
    "Stop": "idle",
    "SubagentStop": "idle",
    "SessionStart": "idle",
}

STATE_DIR = Path(os.environ.get("RC_STATE_DIR", Path.home() / ".cache" / "rc-state"))


def main() -> None:
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name", "")
    sid = payload.get("session_id") or os.environ.get("RC_REMOTE", "unknown")
    f = STATE_DIR / f"{sid}.json"

    if event == "SessionEnd":
        f.unlink(missing_ok=True)
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({
        "state": STATE.get(event, "working"),
        "project": os.environ.get("RC_PROJECT", ""),
        "cwd": payload.get("cwd") or os.getcwd(),
        "session_id": sid,
        "event": event,
        "ts": time.time(),
    }))


main()
