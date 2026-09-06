"""rc_state_hook.py (writes turn-state files from Claude Code hook events) and rc_status.py
(reads them for the shell prompt / --list) — importable now that both are behind a __name__
guard. STATE_DIR is redirected to a tmp dir and restored per test."""

import http.client
import io
import json
import os
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

    def test_hook_command_and_settings_merge_are_single_sourced(self):
        # BOTH the command string and the settings.json merge/removal live once in
        # rc_state_hook; the scripts only call its CLI, so uninstall can't drift from install
        # and neither embeds the JSON merge Python any more.
        cmd = rc_state_hook.hook_command("/r")
        self.assertEqual(
            cmd, '[ -n "$RC_REMOTE" ] && python3 /r/rc_state_hook.py; true'
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc_state_hook.cli(["--hook-command", "/r"])
        self.assertEqual(buf.getvalue().strip(), cmd)
        root = Path(rc_state_hook.__file__).parent
        for script, flag in (
            ("install.sh", "--install-hook"),
            ("uninstall.sh", "--remove-hook"),
        ):
            text = (root / script).read_text()
            self.assertIn(flag, text, script)  # calls the CLI, doesn't embed the logic
            self.assertNotIn(
                "rc_state_hook.py; true", text, script
            )  # no embedded command
            self.assertNotIn('setdefault("hooks"', text, script)  # no embedded merge
            self.assertNotIn(
                "json.load", text, script
            )  # no embedded settings.json parse

    def test_install_and_remove_hook_roundtrip(self):
        # the settings.json merge, now unit-testable (install.sh had zero coverage of it):
        # registers on all six events idempotently, removes cleanly, preserves other hooks.
        keep(self, (rc_state_hook, "SETTINGS"))
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        rc_state_hook.SETTINGS = os.path.join(d, "settings.json")
        Path(rc_state_hook.SETTINGS).write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [{"hooks": [{"type": "command", "command": "other"}]}]
                    }
                }
            )
        )
        cmd = rc_state_hook.hook_command("/r")
        rc_state_hook.install_hook("/r")
        s = json.loads(Path(rc_state_hook.SETTINGS).read_text())
        six = {
            "UserPromptSubmit",
            "Notification",
            "Stop",
            "SubagentStop",
            "SessionStart",
            "SessionEnd",
        }
        self.assertEqual(
            set(rc_state_hook.EVENTS), six
        )  # the exact set, so a drop is caught
        self.assertEqual(set(s["hooks"]), six)  # all six registered
        mine = lambda st, ev: any(
            h["command"] == cmd for e in st["hooks"].get(ev, []) for h in e["hooks"]
        )
        self.assertTrue(all(mine(s, ev) for ev in rc_state_hook.EVENTS))
        total = sum(len(v) for v in s["hooks"].values())
        rc_state_hook.install_hook("/r")  # idempotent — no growth
        s2 = json.loads(Path(rc_state_hook.SETTINGS).read_text())
        self.assertEqual(sum(len(v) for v in s2["hooks"].values()), total)
        # drive it through the CLI too (what the scripts call), then via cli --remove-hook
        with redirect_stdout(io.StringIO()):
            rc_state_hook.cli(["--remove-hook", "/r"])
        s3 = json.loads(Path(rc_state_hook.SETTINGS).read_text())
        self.assertFalse(any(mine(s3, ev) for ev in rc_state_hook.EVENTS))  # ours gone
        self.assertIn("other", json.dumps(s3))  # the unrelated hook preserved
        with redirect_stdout(io.StringIO()):
            rc_state_hook.cli(["--install-hook", "/r"])  # cli install path
        self.assertTrue(
            mine(json.loads(Path(rc_state_hook.SETTINGS).read_text()), "Stop")
        )
        os.remove(rc_state_hook.SETTINGS)
        self.assertIn("no ", rc_state_hook.remove_hook("/r"))  # no-settings-file branch

    def test_hook_merge_tolerates_bad_and_unrelated_settings(self):
        # robustness the panel flagged: empty/malformed settings.json, and a non-list value
        # on a real event or an unrelated one, must not crash install/uninstall or touch
        # other hooks.
        keep(self, (rc_state_hook, "SETTINGS"))
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        rc_state_hook.SETTINGS = os.path.join(d, "settings.json")
        cmd = rc_state_hook.hook_command("/r")

        def mine(st, ev):
            return any(
                h["command"] == cmd for e in st["hooks"].get(ev, []) for h in e["hooks"]
            )

        # a 0-byte file is "empty", not a crash
        Path(rc_state_hook.SETTINGS).write_text("")
        rc_state_hook.install_hook("/r")
        s = json.loads(Path(rc_state_hook.SETTINGS).read_text())
        self.assertEqual(set(s["hooks"]), set(rc_state_hook.EVENTS))
        # corrupt one of the SIX events (non-list) and add a non-list unrelated event: remove
        # must skip the corrupt real event (isinstance guard, else a TypeError aborts) and
        # never visit the unrelated one (EVENTS-only iteration).
        s["hooks"]["Stop"] = "not-a-list"
        s["hooks"]["WeirdEvent"] = "not-a-list"
        Path(rc_state_hook.SETTINGS).write_text(json.dumps(s))
        rc_state_hook.remove_hook("/r")  # must not raise
        s2 = json.loads(Path(rc_state_hook.SETTINGS).read_text())
        self.assertFalse(
            any(
                mine(s2, ev)
                for ev in rc_state_hook.EVENTS
                if isinstance(s2["hooks"].get(ev), list)
            )
        )  # our hook gone from every well-formed event
        self.assertEqual(
            s2["hooks"].get("Stop"), "not-a-list"
        )  # corrupt real event skipped
        self.assertEqual(
            s2["hooks"].get("WeirdEvent"), "not-a-list"
        )  # unrelated untouched
        # malformed JSON: leave it, report it — never clobber or crash
        Path(rc_state_hook.SETTINGS).write_text("{bad json")
        self.assertIn("could not parse", rc_state_hook.remove_hook("/r"))
        self.assertEqual(Path(rc_state_hook.SETTINGS).read_text(), "{bad json")

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
        # isolate the login path from the disk/liveness probes main() now also runs, so this
        # test asserts only the auth notification it names (else it hits real disk + network)
        keep(
            self,
            (rc_healthcheck, "notify"),
            (rc_healthcheck, "check_disk"),
            (rc_healthcheck, "check_launcher"),
        )
        rc_healthcheck.check_disk = lambda: None
        rc_healthcheck.check_launcher = lambda: "testbuild"
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

    def test_check_disk_alerts_below_floor_only(self):
        from types import SimpleNamespace

        keep(self, (rc_healthcheck, "notify"), (rc_healthcheck.os, "statvfs"))
        notes = []
        rc_healthcheck.notify = lambda t, m: notes.append((t, m))
        # f_bavail * f_frsize / 1024**3 GiB free; MIN_FREE_GB is 5.0
        rc_healthcheck.os.statvfs = lambda p: SimpleNamespace(
            f_frsize=1, f_bavail=1 * 1024**3
        )
        rc_healthcheck.check_disk()
        self.assertTrue(notes)  # 1 GiB < 5 GiB floor -> alert (per probed path)
        notes.clear()
        rc_healthcheck.os.statvfs = lambda p: SimpleNamespace(
            f_frsize=1, f_bavail=50 * 1024**3
        )
        rc_healthcheck.check_disk()
        self.assertFalse(notes)  # 50 GiB free -> quiet
        # straddle the 5.0 floor so a silent bump (5.0 -> 2.0) can't pass, and pin the < edge
        gib = 1024**3
        notes.clear()
        rc_healthcheck.os.statvfs = lambda p: SimpleNamespace(
            f_frsize=1, f_bavail=int(4.9 * gib)
        )
        rc_healthcheck.check_disk()
        self.assertTrue(notes)  # 4.9 GiB < 5.0 -> alert
        notes.clear()
        rc_healthcheck.os.statvfs = lambda p: SimpleNamespace(
            f_frsize=1, f_bavail=int(5.1 * gib)
        )
        rc_healthcheck.check_disk()
        self.assertFalse(notes)  # 5.1 GiB -> quiet

    def test_check_launcher_returns_version_up_and_alerts_down(self):
        keep(self, (rc_healthcheck, "notify"), (rc_healthcheck, "_open"))
        notes = []
        rc_healthcheck.notify = lambda t, m: notes.append((t, m))

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"version": "abc123def456"}'

        rc_healthcheck._open = lambda url, timeout=None: _Resp()
        self.assertEqual(rc_healthcheck.check_launcher(), "abc123def456")
        self.assertFalse(notes)  # 200 -> quiet, returns the stamp

        def _boom(url, timeout=None):
            raise OSError("connection refused")

        rc_healthcheck._open = _boom
        self.assertEqual(rc_healthcheck.check_launcher(), "")  # unreachable -> blank
        self.assertTrue(notes)  # ...and a not-responding alert

        def _truncated(url, timeout=None):
            raise http.client.IncompleteRead(
                b"partial"
            )  # wedged: malformed HTTP, not OSError

        notes.clear()
        rc_healthcheck._open = _truncated
        self.assertEqual(
            rc_healthcheck.check_launcher(), ""
        )  # HTTPException caught, no crash
        self.assertTrue(notes)

        # a wrong service on the port answers 200 with non-dict JSON: must not crash
        class _Bad(_Resp):
            def read(self):
                return b"null"

        notes.clear()
        rc_healthcheck._open = lambda url, timeout=None: _Bad()
        self.assertEqual(rc_healthcheck.check_launcher(), "")  # no AttributeError
        self.assertTrue(notes)  # unexpected response is a liveness alert

    def test_check_disk_survives_a_missing_path(self):
        keep(self, (rc_healthcheck, "notify"), (rc_healthcheck.os, "statvfs"))
        notes = []
        rc_healthcheck.notify = lambda t, m: notes.append((t, m))

        def _missing(p):
            raise OSError("no such path")

        rc_healthcheck.os.statvfs = _missing
        rc_healthcheck.check_disk()  # must not raise
        self.assertFalse(
            notes
        )  # a probe path that can't be stat'd is skipped, not alerted


if __name__ == "__main__":
    unittest.main()
