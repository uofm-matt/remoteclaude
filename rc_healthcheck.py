#!/usr/bin/env python3
"""Login-health watchdog for the RC launcher.

`claude remote-control` needs a valid claude.ai OAuth login. If it lapses
(long idle, a `claude logout` to clear relay ghosts, a revoked token), every
project tap dies silently and you can't re-login from the phone — you're
locked out until you're back at the Mac. This runs on a timer (LaunchAgent),
checks `claude auth status`, and notifies on failure so you fix it before you
need it. Healthy runs just append a line to the log and stay quiet.

Set RC_NOTIFY_URL to an ntfy topic (or any webhook) to also get a phone push;
unset, it falls back to a local desktop notification (macOS or Linux) only.
"""

import contextlib
import json
import os
import platform
import shutil
import subprocess
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

CLAUDE = os.path.expanduser(os.environ.get("RC_CLAUDE_BIN", "~/.local/bin/claude"))
NOTIFY_URL = os.environ.get("RC_NOTIFY_URL", "")
MT = ZoneInfo("America/Denver")


def auth_state() -> tuple[str, str]:
    try:
        out = subprocess.run([CLAUDE, "auth", "status"],
                             capture_output=True, text=True, timeout=20).stdout
        d = json.loads(out)
        return ("ok", d.get("email", "")) if d.get("loggedIn") else ("loggedout", "")
    except (json.JSONDecodeError, subprocess.SubprocessError, OSError) as err:
        return "unknown", str(err)


def notify(title: str, msg: str) -> None:
    if platform.system() == "Darwin":
        subprocess.run(
            ["osascript", "-e", f"display notification {msg!r} with title {title!r}"],
            capture_output=True,
        )
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", title, msg], capture_output=True)
    if NOTIFY_URL:
        req = urllib.request.Request(NOTIFY_URL, data=msg.encode(), headers={"Title": title})
        with contextlib.suppress(OSError):
            urllib.request.urlopen(req, timeout=10)


if __name__ == "__main__":
    state, detail = auth_state()
    print(f"{datetime.now(MT):%Y-%m-%d %H:%M:%S} MT  login={state} {detail}".rstrip(),
          flush=True)
    if state != "ok":
        notify(
            "RC launcher: login problem",
            f"claude auth status = {state}. Run `claude /login` on the Mac to "
            "keep Remote Control working.",
        )
