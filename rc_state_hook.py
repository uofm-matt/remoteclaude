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

from rc_state import EVENT_STATE as STATE, STATE_DIR


def main() -> None:
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name", "")
    sid = payload.get("session_id") or os.environ.get("RC_REMOTE", "unknown")
    f = STATE_DIR / f"{sid}.json"

    if event == "SessionEnd":
        f.unlink(missing_ok=True)
        return

    # an event the vocabulary lacks must crash, not paint "working"
    state = STATE[event]
    # Notification covers two very different things: a BLOCKED turn (permission
    # request, a question) and the mere "Claude is waiting for your input" idle ping
    # after a turn ends. Under bypassPermissions the idle ping is nearly the only one
    # that fires, so it painted every finished session as amber "waiting". Idle ping
    # -> idle; anything else stays waiting.
    if (
        event == "Notification"
        and "waiting for your input" in payload.get("message", "").lower()
    ):
        state = "idle"

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(
        json.dumps(
            {
                "state": state,
                "project": os.environ.get("RC_PROJECT", ""),
                "cwd": payload.get("cwd") or os.getcwd(),
                "session_id": sid,
                "event": event,
                "ts": time.time(),
            }
        )
    )


HOOK_COMMAND = '[ -n "$RC_REMOTE" ] && python3 {repo}/rc_state_hook.py; true'


def hook_command(repo: str) -> str:
    """The exact settings.json command install.sh registers and uninstall.sh removes —
    one source, so the two scripts can never disagree on what to match."""
    return HOOK_COMMAND.format(repo=repo)


def cli(argv: list[str]) -> None:
    """`--hook-command <repo>` prints the settings command; anything else is the hook."""
    if argv[:1] == ["--hook-command"]:
        print(hook_command(argv[1]))
    else:
        main()


if __name__ == "__main__":
    cli(sys.argv[1:])
