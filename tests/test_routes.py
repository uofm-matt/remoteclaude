"""The launcher's own routes — /status, /create, /launch, /stop and the root page — over
the real server with subprocess/os.kill/sleep mocked, so no tmux or claude is actually
spawned; plus the share page those same handlers render."""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import rc_config
import rc_sessions
import rc_share
import rc_templates

from tests._harness import (
    ServerCase,
    desk,
    proc,
    respond,
    restore_globals,
    share_dir,
    spawn_ok,
)


class RowsHtmlTest(unittest.TestCase):
    def setUp(self):
        restore_globals(self)
        self.share = rc_config.SHARE = share_dir(self)

    def test_hides_rcpart_and_escaping_symlink(self):
        open(os.path.join(self.share, "real.txt"), "w").close()
        open(os.path.join(self.share, "partial.rcpart"), "w").close()
        # -> outside SHARE
        os.symlink(tempfile.mkdtemp(), os.path.join(self.share, "escape"))
        rows = rc_share.rows_html(self.share, "")
        self.assertIn("real.txt", rows)
        self.assertNotIn("partial", rows)  # .rcpart hidden
        self.assertNotIn("escape", rows)  # symlink out of SHARE not listed

    def test_files_page_carries_the_download_confirmation_hooks(self):
        # the app keys on the UA tag and calls rcDownloadDone(); a browser gets the
        # fetch-with-progress path. Both live in the page, so both are pinned here.
        out = rc_share.share_page(self.share, "").decode()
        self.assertIn("rc-launcher-app", out)
        self.assertIn("window.rcDownloadDone=function", out)
        self.assertIn("URL.createObjectURL", out)
        # and the script must parse: a bad \\u escape or an unbalanced brace in the new
        # async function would leave the page dead while every substring above still matched
        if not (node := shutil.which("node")):
            self.skipTest("node not installed")
        script = re.search(r"<script>(.*)</script>", out, re.S).group(1)
        path = os.path.join(self.share, "page.js")
        Path(path).write_text(script)
        self.assertEqual(subprocess.run([node, "--check", path]).returncode, 0)

    def test_script_context_values_are_escaped(self):
        self.assertNotIn("<", rc_templates.js("</script>"))  # the escape at the source
        out = rc_share.share_page(self.share, "a</script>b")
        self.assertNotIn(b"</script>b", out)  # the rel didn't break out of the <script>
        self.assertNotIn(b"__REL__", out)  # no unfilled placeholder left

    def test_rows_html_dirs_first_empty_and_unreadable(self):
        os.makedirs(os.path.join(self.share, "adir"))
        open(os.path.join(self.share, "afile.txt"), "w").close()
        rows = rc_share.rows_html(self.share, "")
        # dirs before files
        self.assertLess(rows.index("adir"), rows.index("afile.txt"))
        self.assertIn(
            "empty", rc_share.rows_html(os.path.join(self.share, "adir"), "/adir")
        )
        # OSError is not "empty"
        self.assertIn(
            "unreadable", rc_share.rows_html(os.path.join(self.share, "no"), "/no")
        )


class RouteTest(ServerCase):
    """The do_GET routes (/status, /create, /launch, /stop, root) over the real server with
    subprocess/os.kill/sleep mocked, so no tmux/claude is actually spawned."""

    def setUp(self):
        super().setUp()
        self.aux = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.aux, True)
        rc_config.PARENT = os.path.join(self.aux, "projects")
        os.makedirs(rc_config.PARENT)
        rc_sessions.STATE_DIR = Path(self.aux, "state")
        rc_config.CLAUDE_JSON = os.path.join(self.aux, "claude.json")
        # never the host's
        rc_config.CLAUDE_PROJECTS = Path(self.aux, "claude-projects")
        Path(rc_config.CLAUDE_JSON).write_text("{}")
        self.responses: dict = {}
        self.desk: dict = {}
        subprocess.run = lambda cmd, **kw: self._resp(cmd)
        os.kill = lambda *a: None
        time.sleep = lambda *a: None
        rc_config.RESUME = "off"  # fresh launches (no takeover) keep route tests simple

    def _resp(self, cmd):
        return respond(cmd, self.desk, self.responses)

    def get(self, path):
        status, _, body = self.req("GET", path)
        return status, body

    def test_status_route(self):
        self.responses = {
            "list-sessions": proc(stdout="rc-alpha\n"),
            "auth status": proc(stdout='{"loggedIn": true}'),
        }
        status, body = self.get("/status")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["login"], "ok")
        self.assertIn("alpha", d["running"])
        self.assertIn("git", d)  # badges follow the poll now, not just the page load

    def test_root_page_fills_placeholders(self):
        self.responses = {"auth status": proc(stdout='{"loggedIn": true}')}
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertNotIn(b"__PROJECTS__", body)
        self.assertNotIn(b"__LOGIN__", body)

    def test_create_route_makes_and_launches(self):
        self.responses = spawn_ok()
        status, body = self.get("/create?proj=newp")
        d = json.loads(body)
        self.assertEqual(d["status"], "created")
        self.assertEqual(d["launch"], "launched")
        self.assertTrue(os.path.isdir(os.path.join(rc_config.PARENT, "newp")))

    def test_create_route_bad_name_reports_reason(self):
        status, body = self.get("/create?proj=bad%20name")  # space -> badname + reason
        d = json.loads(body)
        self.assertEqual(d["status"], "badname")
        self.assertIn("reason", d)

    def test_launch_route_unknown_project_404(self):
        self.assertEqual(self.get("/launch?proj=ghost")[0], 404)

    def test_launch_and_stop_routes(self):
        os.makedirs(os.path.join(rc_config.PARENT, "realp"))
        self.responses = spawn_ok()
        self.assertEqual(
            json.loads(self.get("/launch?proj=realp&json=1")[1])["status"], "launched"
        )
        self.assertEqual(
            json.loads(self.get("/stop?proj=realp&json=1")[1])["status"], "stopped"
        )

    def test_stop_desk_route_sigterms_desk_session(self):
        # The X on a desk-badged row: /stop?desk=1 must take the desk_stop branch —
        # SIGTERM the desk claude, never the tmux C-c/kill-session path (deleting the
        # route conditional would fall through to stop() and phantom-"stop" nothing).
        os.makedirs(os.path.join(rc_config.PARENT, "deskp"))
        root = os.path.join(rc_config.PARENT, "deskp")
        killed, calls = [], []

        def kill(pid, sig):
            killed.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError  # SIGTERM worked; takeover needn't escalate

        os.kill = kill
        subprocess.run = lambda cmd, **kw: (
            calls.append(cmd),
            self._resp(cmd),
        )[1]
        self.desk = {"321": desk(root)}
        status, body = self.get("/stop?proj=deskp&desk=1&json=1")
        self.assertEqual(json.loads(body)["status"], "stopped")
        self.assertIn((321, signal.SIGTERM), killed)  # graceful desk close
        self.assertNotIn((321, signal.SIGKILL), killed)
        joined = [" ".join(map(str, c)) for c in calls]
        self.assertFalse(any("send-keys" in c or "kill-session" in c for c in joined))

    def test_addroot_requires_the_token(self):
        self.assertEqual(self.req("GET", "/addroot?path=/tmp", cookie=False)[0], 403)

    def test_addroot_adds_a_directory_and_lists_its_children(self):
        extra = os.path.join(self.aux, "extra")
        os.makedirs(os.path.join(extra, "sub"))
        status, _, body = self.req("GET", f"/addroot?path={extra}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "added")
        projs = json.loads(self.req("GET", "/status")[2])["projects"]
        self.assertIn("extra/sub", projs)  # the new root's child now lists
        self.assertEqual(
            json.loads(self.req("GET", "/addroot?path=/no/such")[2])["status"],
            "badpath",
        )

    def test_version_route_is_unauthenticated_and_returns_the_stamp(self):
        # the watchdog liveness probe hits this with no token; moving it below _authed would
        # 403 and break the probe, so pin that a token-less GET gets 200 + the build stamp
        status, _, body = self.req("GET", "/version", cookie=False)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], rc_config.VERSION)

    def test_stop_route_reports_failed_when_the_session_survives(self):
        # the contract stopSess keys on: a session still alive after the double C-c
        # and the kill is "failed" with a reason, so the page keeps the dot and toasts
        os.makedirs(os.path.join(rc_config.PARENT, "p"))
        rc_config.STOP_WAIT = 0
        self.responses = {"has-session": proc(returncode=0)}
        status, body = self.get("/stop?proj=p&json=1")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {
                "status": "failed",
                "proj": "p",
                "reason": "still alive after SIGINT and kill-session",
            },
        )

    def test_launch_ignores_the_desk_flag(self):
        # desk=1 only means something on /stop; on /launch it must launch, not desk-stop
        # (the audit's half-pin: only the /stop side of the conditional was tested)
        os.makedirs(os.path.join(rc_config.PARENT, "p"))
        killed = []
        os.kill = lambda pid, sig: killed.append((pid, sig))
        self.desk = {"321": desk(os.path.join(rc_config.PARENT, "p"))}
        self.responses = spawn_ok()
        status, body = self.get("/launch?proj=p&desk=1&json=1")
        self.assertEqual(json.loads(body)["status"], "launched")
        self.assertEqual(killed, [])

    def test_unknown_route_404(self):
        self.assertEqual(self.get("/nonexistent")[0], 404)

    def _dead_spawn(self):
        # a fresh launch whose tmux pane dies inside the liveness window, with a
        # recognisable death reason on its dead pane
        return spawn_ok() | {
            "pane_dead": proc(stdout="1\n"),
            "capture-pane": proc(stdout="please trust this workspace\n"),
        }

    def test_launch_json_failure_carries_reason(self):
        os.makedirs(os.path.join(rc_config.PARENT, "p"))
        self.responses = self._dead_spawn()
        status, body = self.get("/launch?proj=p&json=1")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {"status": "failed", "proj": "p", "reason": "untrusted dir"},
        )

    def test_create_then_failed_launch_carries_launch_reason(self):
        self.responses = self._dead_spawn()
        status, body = self.get("/create?proj=newproj")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual((payload["status"], payload["launch"]), ("created", "failed"))
        self.assertEqual(payload["launch_reason"], "untrusted dir")

    def test_launch_without_json_renders_the_page(self):
        # the browser form (no json=1) gets the launcher page back, not JSON
        os.makedirs(os.path.join(rc_config.PARENT, "p"))
        self.responses = spawn_ok()
        status, body = self.get("/launch?proj=p")
        self.assertEqual(status, 200)
        self.assertTrue(body.lstrip().lower().startswith(b"<!doctype html"), body[:40])


if __name__ == "__main__":
    unittest.main()
