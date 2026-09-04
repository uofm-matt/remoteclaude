"""rc_state_hook.py (writes turn-state files from Claude Code hook events) and rc_status.py
(reads them for the shell prompt / --list) — importable now that both are behind a __name__
guard. STATE_DIR is redirected to a tmp dir and restored per test."""

import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import rc_claude
import rc_healthcheck
import rc_state
import rc_state_hook
import rc_status

from tests._harness import keep, proc


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = (rc_state_hook.STATE_DIR, rc_status.STATE_DIR)
        rc_state_hook.STATE_DIR = self.tmp
        rc_status.STATE_DIR = self.tmp

    def tearDown(self):
        rc_state_hook.STATE_DIR, rc_status.STATE_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _hook(self, payload):
        old = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            rc_state_hook.main()
        finally:
            sys.stdin = old

    def _status(self, *argv):
        buf, old = io.StringIO(), sys.argv
        sys.argv = ["rc_status.py", *argv]
        try:
            with redirect_stdout(buf):
                rc_status.main()
        finally:
            sys.argv = old
        return buf.getvalue()

    def test_hook_maps_events_to_state_and_cleans_up(self):
        self._hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s1",
                "cwd": "/tmp/proj",
                "project": "proj",
            }
        )
        f = self.tmp / "s1.json"
        self.assertEqual(json.loads(f.read_text())["state"], "working")
        self._hook(
            {
                "hook_event_name": "Notification",
                "session_id": "s1",
                "cwd": "/tmp/proj",
                "project": "proj",
                "message": "Claude needs your permission to use Bash",
            }
        )
        # blocked turn -> waiting
        self.assertEqual(json.loads(f.read_text())["state"], "waiting")
        self._hook(
            {
                "hook_event_name": "Notification",
                "session_id": "s1",
                "cwd": "/tmp/proj",
                "project": "proj",
                "message": "Claude is waiting for your input",
            }
        )
        # idle ping is NOT waiting
        self.assertEqual(json.loads(f.read_text())["state"], "idle")
        for ev in ("Stop", "SubagentStop", "SessionStart"):
            self._hook(
                {
                    "hook_event_name": ev,
                    "session_id": "s1",
                    "cwd": "/tmp/proj",
                    "project": "proj",
                }
            )
            # each must map to a real RANK state — a renamed/typo'd value would drop the
            # session out of every badge and --list silently (the drift rc_state exists for)
            self.assertEqual(json.loads(f.read_text())["state"], "idle", ev)
        self._hook({"hook_event_name": "SessionEnd", "session_id": "s1"})
        self.assertFalse(f.exists())  # SessionEnd removes the file

    def test_hook_command_is_the_single_source(self):
        # install.sh and uninstall.sh used to each embed this string; if one drifted the
        # uninstaller stopped matching. Both now ask rc_state_hook.py for it, and no
        # copy may reappear in either script.
        cmd = rc_state_hook.hook_command("/r")
        self.assertEqual(
            cmd, '[ -n "$RC_REMOTE" ] && python3 /r/rc_state_hook.py; true'
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_state_hook.cli(["--hook-command", "/r"])  # what install.sh actually runs
        self.assertEqual(buf.getvalue().strip(), cmd)
        root = Path(rc_state_hook.__file__).parent
        for script in ("install.sh", "uninstall.sh"):
            text = (root / script).read_text()
            self.assertNotIn("rc_state_hook.py; true", text, script)
            self.assertIn("--hook-command", text, script)
        # a failing substitution in a prefix assignment escapes `set -e`; the guard is
        # what keeps an empty command out of six hook entries
        self.assertIn('[ -n "$RC_HOOK_CMD" ]', (root / "install.sh").read_text())

    def test_unknown_event_fails_loud(self):
        # an event the vocabulary lacks used to paint "working"; it must crash instead
        with self.assertRaises(KeyError):
            self._hook({"hook_event_name": "SomethingNew", "session_id": "s9"})

    def test_status_list_shows_live_session(self):
        (self.tmp / "s1.json").write_text(
            json.dumps(
                {
                    "state": "working",
                    "project": "proj",
                    "cwd": "/tmp/proj",
                    "session_id": "s1",
                    "ts": time.time(),
                }
            )
        )
        out = self._status("--list")
        self.assertIn("proj", out)
        self.assertIn("working", out)

    def test_status_prompt_tag_when_tree_shares(self):
        here = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, here, True)
        (self.tmp / "s1.json").write_text(
            json.dumps(
                {
                    "state": "working",
                    "project": "p",
                    "cwd": str(here),
                    "session_id": "s1",
                    "ts": time.time(),
                }
            )
        )
        # RPROMPT glyph for a live turn here
        self.assertIn("rc:working", self._status(str(here)))

    def test_status_ignores_stale(self):
        (self.tmp / "s1.json").write_text(
            json.dumps(
                {
                    "state": "working",
                    "project": "old",
                    "cwd": "/tmp/x",
                    "session_id": "s1",
                    "ts": time.time() - rc_state.STATE_TTL - 60,
                }
            )
        )
        self.assertNotIn("old", self._status("--list"))

    def test_status_skips_corrupt_state_file(self):
        # corrupt -> skipped, RPROMPT must not crash
        (self.tmp / "bad.json").write_text("{not json")
        (self.tmp / "s1.json").write_text(
            json.dumps(
                {
                    "state": "working",
                    "project": "p",
                    "cwd": "/tmp/p",
                    "session_id": "s1",
                    "ts": time.time(),
                }
            )
        )
        out = self._status("--list")
        self.assertIn("p", out)  # the good file still lists
        self.assertNotIn("bad", out)  # corrupt file skipped cleanly, no traceback


class HealthcheckTest(unittest.TestCase):
    def setUp(self):
        keep(self, (rc_healthcheck.subprocess, "run"))

    def _claude(self, stdout):
        rc_healthcheck.subprocess.run = lambda *a, **k: proc(stdout=stdout)

    def test_auth_status_maps_claude_status(self):
        # the shared probe both the launcher badge and the watchdog now use
        self._claude('{"loggedIn": true, "email": "me@x"}')
        self.assertEqual(rc_claude.auth_status(), ("ok", "me@x"))
        self._claude('{"loggedIn": false}')
        self.assertEqual(rc_claude.auth_status(), ("loggedout", ""))
        self._claude("not json")
        # JSONDecodeError -> unknown
        self.assertEqual(rc_claude.auth_status()[0], "unknown")

    def test_notify_uses_desktop_path(self):
        keep(self, (rc_healthcheck.platform, "system"))
        calls = []
        rc_healthcheck.subprocess.run = lambda *a, **k: calls.append(a[0])
        rc_healthcheck.platform.system = lambda: "Darwin"
        rc_healthcheck.notify("title", "msg")
        self.assertTrue(any("osascript" in c for c in calls))  # macOS notification path

    def test_notify_linux_desktop_and_phone_push(self):
        keep(
            self,
            (rc_healthcheck.platform, "system"),
            (rc_healthcheck.shutil, "which"),
            (rc_healthcheck, "NOTIFY_URL"),
            (rc_healthcheck.urllib.request, "urlopen"),
        )
        calls, pushed = [], []
        rc_healthcheck.platform.system = lambda: "Linux"
        rc_healthcheck.shutil.which = lambda n: "/usr/bin/notify-send"
        rc_healthcheck.subprocess.run = lambda *a, **k: calls.append(a[0])
        rc_healthcheck.NOTIFY_URL = "http://ntfy.example/rc"
        rc_healthcheck.urllib.request.urlopen = lambda req, timeout=None: pushed.append(
            req.full_url
        )
        rc_healthcheck.notify("t", "m")
        self.assertTrue(any("notify-send" in c for c in calls))  # Linux desktop path
        # the ntfy/webhook phone push fired
        self.assertIn("http://ntfy.example/rc", pushed)

    def test_main_notifies_only_on_login_problem(self):
        keep(self, (rc_healthcheck, "notify"))
        notes = []
        rc_healthcheck.notify = lambda title, msg: notes.append((title, msg))
        self._claude('{"loggedIn": false}')  # logged out -> alert
        with redirect_stdout(io.StringIO()):
            rc_healthcheck.main()
        # a login problem fires exactly one notification
        self.assertEqual(len(notes), 1)
        self._claude('{"loggedIn": true, "email": "me@x"}')  # healthy -> stay quiet
        with redirect_stdout(io.StringIO()):
            rc_healthcheck.main()
        self.assertEqual(len(notes), 1)  # no new notification on the healthy run


if __name__ == "__main__":
    unittest.main()
