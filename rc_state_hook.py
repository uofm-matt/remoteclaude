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


SETTINGS = os.path.expanduser("~/.claude/settings.json")
EVENTS = (
    "UserPromptSubmit",
    "Notification",
    "Stop",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
)


def install_hook(repo: str) -> str:
    """Register the state hook on every RC event in settings.json, idempotently — the merge
    install.sh used to embed, now here so uninstall's removal matches it by construction."""
    cmd = hook_command(repo)
    p = Path(SETTINGS)
    text = p.read_text() if p.exists() else ""
    d = (
        json.loads(text) if text.strip() else {}
    )  # 0-byte/whitespace is empty; bad JSON raises
    hooks = d.setdefault("hooks", {})
    added = False
    for ev in EVENTS:
        entries = hooks.setdefault(ev, [])
        if not any(
            h.get("command") == cmd for e in entries for h in e.get("hooks", [])
        ):
            entries.append({"hooks": [{"type": "command", "command": cmd}]})
            added = True
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2) + "\n")
    return f"state hook {'registered' if added else 'already present'} in {p}"


def remove_hook(repo: str) -> str:
    """Remove the state hook from settings.json, leaving any other hooks intact."""
    cmd = hook_command(repo)
    p = Path(SETTINGS)
    if not p.exists():
        return f"no {p}"
    text = p.read_text()
    if not text.strip():
        return f"empty {p}; nothing to remove"
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return f"could not parse {p}; left unchanged"
    hooks = d.get("hooks", {})
    for (
        ev
    ) in EVENTS:  # only the events install_hook touches — the exact inverse, and an
        entries = hooks.get(
            ev
        )  # unrelated event with a non-list value can't abort the removal
        if not isinstance(entries, list):
            continue
        kept = [
            e
            for e in entries
            if not any(h.get("command") == cmd for h in e.get("hooks", []))
        ]
        if kept:
            hooks[ev] = kept
        else:
            hooks.pop(ev, None)
    p.write_text(json.dumps(d, indent=2) + "\n")
    return f"state hook removed from {p}"


def hook_command(repo: str) -> str:
    """The exact settings.json command install.sh registers and uninstall.sh removes —
    one source, so the two scripts can never disagree on what to match."""
    return HOOK_COMMAND.format(repo=repo)


def cli(argv: list[str]) -> None:
    """--hook-command/--install-hook/--remove-hook <repo> manage the settings.json hook (what
    install.sh/uninstall.sh call); anything else runs the hook itself (the event handler)."""
    match argv:
        case ["--hook-command", repo]:
            print(hook_command(repo))
        case ["--install-hook", repo]:
            print("  ", install_hook(repo))
        case ["--remove-hook", repo]:
            print("  ", remove_hook(repo))
        case _:
            main()


if __name__ == "__main__":
    cli(sys.argv[1:])
