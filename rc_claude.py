"""Shared claude(1) probe: the CLAUDE binary path, the `claude auth status` contract, and MT.

auth_status() is read by both rc_sessions (the login badge) and rc_healthcheck (the
watchdog) so the external JSON contract and the default binary path have one definition
instead of two that drift; MT is the timezone every timestamp in the tree prints in.
This module depends on nothing in the launcher tree, so rc_healthcheck stays independent
of it — the watchdog still runs if the launcher is broken.
"""

import json
import os
import subprocess
from zoneinfo import ZoneInfo

CLAUDE = os.path.expanduser(os.environ.get("RC_CLAUDE_BIN", "~/.local/bin/claude"))
MT = ZoneInfo("America/Denver")


def auth_status(timeout: float = 15) -> tuple[str, str]:
    """('ok' | 'loggedout' | 'unknown', detail): the login state, with detail = the email when
    logged in, the error text when unknown, else ''. Spawns a process, so a caller that polls
    should cache the result."""
    try:
        out = subprocess.run(
            [CLAUDE, "auth", "status"], capture_output=True, text=True, timeout=timeout
        ).stdout
        d = json.loads(out)
    except (json.JSONDecodeError, subprocess.SubprocessError, OSError) as err:
        return "unknown", str(err)
    return ("ok", d.get("email", "")) if d.get("loggedIn") else ("loggedout", "")
