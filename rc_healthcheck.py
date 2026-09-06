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
import http.client
import json
import os
import platform
import shutil
import subprocess
import urllib.request
from datetime import datetime

from rc_claude import MT, auth_status

NOTIFY_URL = os.environ.get("RC_NOTIFY_URL", "")
# Read from env, not by importing rc_config — the watchdog stays independent of the launcher
# tree so it still runs when the launcher is broken. Defaults match rc_config's own.
SHARE = os.path.realpath(
    os.path.expanduser(os.environ.get("RC_SHARE_DIR", "~/rc-share"))
)
PORT = int(
    os.environ.get("RC_LAUNCHER_PORT") or "8787"
)  # empty env must not ValueError
MIN_FREE_GB = (
    5.0  # absolute floor beats a percent: 10% of 1TB is still 100GB of false calm
)
LIVENESS_TIMEOUT = 5.0
# open() past any HTTP_PROXY: a corporate proxy must not intercept the localhost probe and
# report the launcher down. Named so tests can stub it.
_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def notify(title: str, msg: str) -> None:
    if platform.system() == "Darwin":
        subprocess.run(
            ["osascript", "-e", f"display notification {msg!r} with title {title!r}"],
            capture_output=True,
        )
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", title, msg], capture_output=True)
    if NOTIFY_URL:
        req = urllib.request.Request(
            NOTIFY_URL, data=msg.encode(), headers={"Title": title}
        )
        with contextlib.suppress(OSError):
            urllib.request.urlopen(req, timeout=10)


def check_disk() -> None:
    """Notify if the boot volume or the share is running out — the failure that silently
    downed the launcher before. Each path is probed independently."""
    for label, path in (("boot volume", "/"), ("rc-share", SHARE)):
        try:
            st = os.statvfs(path)
        except OSError:
            continue
        free_gb = st.f_bavail * st.f_frsize / 1024**3
        if free_gb < MIN_FREE_GB:
            notify(
                "RC launcher: low disk", f"{label} ({path}) has {free_gb:.1f} GiB free"
            )


def check_launcher() -> str:
    """GET the unauthenticated /version. Notify if the launcher isn't answering (a crash-loop
    or wedge the login check can't see), and return the running build stamp for the log —
    blank when unreachable."""
    try:
        with _open(f"http://127.0.0.1:{PORT}/version", timeout=LIVENESS_TIMEOUT) as r:
            body = json.loads(r.read())
        if isinstance(body, dict) and "version" in body:
            return body["version"]
        problem = (
            "unexpected /version response"  # something other than the launcher answered
        )
    except (OSError, ValueError, http.client.HTTPException) as e:
        # refused / timeout / HTTPError(OSError) / bad JSON / truncated or malformed HTTP
        problem = str(e) or type(e).__name__
    notify("RC launcher: not responding", f"/version on :{PORT}: {problem}")
    return ""


def main() -> None:
    # the watchdog can afford a longer probe than the badge
    state, detail = auth_status(timeout=20)
    version = (
        check_launcher()
    )  # notifies if the launcher is down; returns the live build
    print(
        f"{datetime.now(MT):%Y-%m-%d %H:%M:%S} MT  login={state} build={version} "
        f"{detail}".rstrip(),
        flush=True,
    )
    if state != "ok":
        notify(
            "RC launcher: login problem",
            f"claude auth status = {state}. Run `claude /login` on the Mac to "
            "keep Remote Control working.",
        )
    check_disk()


if __name__ == "__main__":
    main()
