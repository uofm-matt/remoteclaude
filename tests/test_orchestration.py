"""launch / stop / takeover / _spawn and their helpers, driven with subprocess, os.kill,
and time.sleep mocked — the tmux / pgrep / ps / lsof orchestration is exercised on canned
output, so no real processes are spawned or signalled."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import rc_launcher

from tests._harness import proc, restore_globals


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        restore_globals(self)  # snapshot + auto-restore every global/singleton reassigned below
        self.tmp = tempfile.mkdtemp()
        rc_launcher.PARENT = os.path.join(self.tmp, "projects")
        os.makedirs(os.path.join(rc_launcher.PARENT, "proj"))
        rc_launcher.CLAUDE_JSON = os.path.join(self.tmp, "claude.json")
        Path(rc_launcher.CLAUDE_JSON).write_text("{}")
        rc_launcher.log_event = lambda *a: None
        self.calls: list = []
        self.responses: dict = {}   # command-substring -> proc(...)
        self.killed: list = []      # (pid, sig) seen by os.kill
        self.alive: set = set()     # pids that os.kill(pid, 0) should treat as alive
        rc_launcher.subprocess.run = self._run
        rc_launcher.os.kill = self._kill
        rc_launcher.time.sleep = lambda *a: None
        # force _pid_cwd down the (mocked) lsof path — on Linux it would read the runner's real
        # /proc/<pid>/cwd and bypass the mock, so the desktop_sessions tests must stub it out.
        rc_launcher.os.path.islink = lambda p: False

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, cmd, **kw):
        self.calls.append(cmd)
        key = " ".join(map(str, cmd))
        for pat, resp in self.responses.items():
            if pat in key:
                return resp() if callable(resp) else resp
        return proc()

    def _kill(self, pid, sig):
        self.killed.append((pid, sig))
        if sig == 0 and pid not in self.alive:
            raise ProcessLookupError

    def _cmds(self) -> list[str]:
        return [" ".join(map(str, c)) for c in self.calls]

    # --- pure command builders ---

    def test_launch_cmd_resume_vs_fresh(self):
        c = rc_launcher.CLAUDE
        rc_launcher.RESUME, rc_launcher.SPAWN = "continue", "same-dir"
        cmd, resuming = rc_launcher.launch_cmd("proj")
        self.assertTrue(resuming)
        self.assertEqual(cmd, [c, "--continue", "--remote-control", "proj"])  # exact argv
        rc_launcher.RESUME = "fork"
        self.assertEqual(rc_launcher.launch_cmd("proj")[0],
                         [c, "--continue", "--fork-session", "--remote-control", "proj"])
        rc_launcher.RESUME, rc_launcher.SPAWN = "off", "same-dir"  # fresh same-dir -> FLAG form:
        cmd, resuming = rc_launcher.launch_cmd("proj")             # local-first, desk-resumable
        self.assertFalse(resuming)
        self.assertEqual(cmd, [c, "--remote-control", "proj"])  # never the relay-only subcommand
        rc_launcher.SPAWN = "worktree"  # not same-dir -> subcommand form, exact --spawn value
        cmd, resuming = rc_launcher.launch_cmd("proj")
        self.assertFalse(resuming)
        self.assertEqual(cmd, [c, "remote-control", "--name", "proj", "--spawn", "worktree"])

    # --- _run / _pid_cwd / _alive ---

    def test_run_tolerates_missing_binary(self):
        def boom(cmd, **kw):
            raise OSError("no such tool")
        rc_launcher.subprocess.run = boom
        self.assertEqual(rc_launcher._run(["nope"]), "")

    def test_alive_reflects_os_kill(self):
        self.alive = {42}
        self.assertTrue(rc_launcher._alive(42))
        self.assertFalse(rc_launcher._alive(99))

    # --- death_reason classification ---

    def test_death_reason_classifies(self):
        self.responses = {"capture-pane": proc(stdout="please trust this workspace\n")}
        self.assertEqual(rc_launcher.death_reason("s"), "untrusted dir")
        self.responses = {"capture-pane": proc(stdout="you are logged out\n")}
        self.assertIn("login expired", rc_launcher.death_reason("s"))
        self.responses = {"capture-pane": proc(stdout="Pane is dead\nboom other error\n")}
        self.assertEqual(rc_launcher.death_reason("s"), "boom other error")
        self.responses = {"capture-pane": proc(stdout="")}
        self.assertEqual(rc_launcher.death_reason("s"), "exited immediately")

    # --- desktop_sessions / takeover ---

    def test_desktop_sessions_scopes_by_cwd(self):
        root = os.path.join(rc_launcher.PARENT, "proj")

        def run(cmd, **kw):
            self.calls.append(cmd)
            key = " ".join(map(str, cmd))
            if "pgrep" in key:
                return proc(stdout="111 222 333\n")
            if "comm=" in key:  # 333 isn't a claude process at all
                return proc(stdout="grep\n" if "333" in key else "claude\n")
            if "command=" in key:  # 222 is an RC server, not a desktop client
                return proc(stdout="claude remote-control\n" if "222" in key else "claude\n")
            if "-Fn" in key:  # ALL three rooted in proj -> only the comm/RC filters can exclude 222/333
                return proc(stdout=f"n{root}/sub\n")
            return proc()
        rc_launcher.subprocess.run = run
        self.assertEqual(rc_launcher.desktop_sessions("proj"), [111])
        # the pgrep match must be by full command line (-f), not a loose tool-name check
        self.assertTrue(any("-f" in c for c in self.calls if "pgrep" in " ".join(map(str, c))))

    def test_takeover_sigterms_and_returns_pids(self):
        root = os.path.join(rc_launcher.PARENT, "proj")

        def run(cmd, **kw):
            key = " ".join(map(str, cmd))
            if "pgrep" in key:
                return proc(stdout="111\n")
            if "comm=" in key:
                return proc(stdout="claude\n")
            if "-Fn" in key:
                return proc(stdout=f"n{root}\n")
            return proc()
        rc_launcher.subprocess.run = run
        self.alive = set()  # after SIGTERM the process is gone -> _alive False, no SIGKILL
        self.assertEqual(rc_launcher.takeover("proj"), [111])
        self.assertIn((111, rc_launcher.signal.SIGTERM), self.killed)

    # --- launch / stop ---

    def test_launch_already_running(self):
        self.responses = {"has-session": proc(returncode=0)}
        self.assertEqual(rc_launcher.launch("proj"), ("already", None))

    def test_launch_fresh_success(self):
        rc_launcher.RESUME, rc_launcher.SPAWN = "off", "same-dir"  # fresh path, no takeover
        self.responses = {"has-session": proc(returncode=1), "pane_dead": proc(stdout="0\n")}
        self.assertEqual(rc_launcher.launch("proj"), ("launched", None))
        newsession = next(c for c in self.calls if "new-session" in " ".join(map(str, c)))
        self.assertEqual(newsession[-1],  # the exact claude command tmux is told to run —
                         f"{rc_launcher.CLAUDE} --remote-control proj")  # flag form: local-first
        # rooted in the project dir (same-dir is load-bearing) and tagged so the state hook fires
        self.assertEqual(newsession[newsession.index("-c") + 1],
                         os.path.join(rc_launcher.PARENT, "proj"))
        self.assertIn("RC_REMOTE=rc-proj", newsession)
        # remain-on-exit toggled on (a dead pane survives for death_reason) then off
        cmds = self._cmds()
        self.assertTrue(any("remain-on-exit on" in c for c in cmds))
        self.assertTrue(any("remain-on-exit off" in c for c in cmds))

    def test_launch_dead_pane_reports_reason_and_kills(self):
        rc_launcher.RESUME = "off"
        self.responses = {
            "has-session": proc(returncode=1),
            "pane_dead": proc(stdout="1\n"),
            "capture-pane": proc(stdout="Error: trust this folder first\n"),
        }
        self.assertEqual(rc_launcher.launch("proj"), ("failed", "untrusted dir"))
        self.assertTrue(any("kill-session" in c for c in self._cmds()))

    def test_launch_resume_falls_back_to_fresh(self):
        rc_launcher.RESUME, rc_launcher.SPAWN, rc_launcher.TAKEOVER = "continue", "same-dir", False
        panes = iter(["1\n", "0\n"])  # resume _spawn dies, fresh _spawn lives

        def run(cmd, **kw):
            self.calls.append(cmd)
            key = " ".join(map(str, cmd))
            if "has-session" in key:
                return proc(returncode=1)
            if "pane_dead" in key:
                return proc(stdout=next(panes))
            if "capture-pane" in key:
                return proc(stdout="no conversation to continue\n")
            return proc()
        rc_launcher.subprocess.run = run
        self.assertEqual(rc_launcher.launch("proj"), ("launched", None))
        spawns = [c[-1] for c in self.calls if "new-session" in " ".join(map(str, c))]
        self.assertEqual(len(spawns), 2)
        self.assertIn("--continue", spawns[0])     # first attempt resumes
        self.assertEqual(spawns[1],                # the fallback is a FRESH flag-form launch
                         f"{rc_launcher.CLAUDE} --remote-control proj")

    def test_stop_sigint_then_kill(self):
        self.assertEqual(rc_launcher.stop("proj"), ("stopped", None))
        cmds = self._cmds()
        sigint = next(i for i, c in enumerate(cmds) if "send-keys" in c and "C-c" in c)
        kill = next(i for i, c in enumerate(cmds) if "kill-session" in c)
        self.assertLess(sigint, kill)  # SIGINT (relay deregister) MUST precede the SIGHUP kill

    # --- login_status / running ---

    def test_login_status_parses_claude_auth(self):
        for out, want in (('{"loggedIn": true}', "ok"), ('{"loggedIn": false}', "loggedout"),
                          ("not json", "unknown")):
            self.responses = {"auth status": proc(stdout=out)}
            rc_launcher._login_status.cache_clear()
            self.assertEqual(rc_launcher.login_status(), want)

    def test_running_parses_and_tolerates_no_tmux(self):
        self.responses = {"list-sessions": proc(stdout="rc-alpha\nrc-beta\nother\n")}
        self.assertEqual(rc_launcher.running(), {"alpha", "beta"})

        def boom(cmd, **kw):
            raise FileNotFoundError
        rc_launcher.subprocess.run = boom
        self.assertEqual(rc_launcher.running(), set())

    # --- snapshot failure branches ---

    def test_snapshot_not_a_repo(self):
        os.environ["RC_SNAPSHOT"] = "1"
        self.responses = {"is-inside-work-tree": proc(returncode=1)}
        try:
            self.assertIsNone(rc_launcher.snapshot("proj"))
        finally:
            os.environ.pop("RC_SNAPSHOT", None)

    def test_snapshot_clean_tree_returns_none(self):
        os.environ["RC_SNAPSHOT"] = "1"
        self.responses = {"is-inside-work-tree": proc(returncode=0), "stash create": proc(stdout="\n")}
        try:
            self.assertIsNone(rc_launcher.snapshot("proj"))
        finally:
            os.environ.pop("RC_SNAPSHOT", None)

    # --- _pid_cwd via /proc, takeover SIGKILL, launch logging ---

    def test_pid_cwd_via_proc_symlink(self):
        real_islink, real_readlink = rc_launcher.os.path.islink, rc_launcher.os.readlink
        rc_launcher.os.path.islink = lambda p: p == "/proc/777/cwd"
        rc_launcher.os.readlink = lambda p: "/the/cwd"
        try:
            self.assertEqual(rc_launcher._pid_cwd("777"), "/the/cwd")
        finally:
            rc_launcher.os.path.islink, rc_launcher.os.readlink = real_islink, real_readlink

    def test_takeover_sigkills_straggler(self):
        root = os.path.join(rc_launcher.PARENT, "proj")

        def run(cmd, **kw):
            key = " ".join(map(str, cmd))
            if "pgrep" in key:
                return proc(stdout="111\n")
            if "comm=" in key:
                return proc(stdout="claude\n")
            if "-Fn" in key:
                return proc(stdout=f"n{root}\n")
            return proc()
        rc_launcher.subprocess.run = run
        self.alive = {111}  # survives SIGTERM -> forces the SIGKILL path
        ticks = iter([0.0, 1.0, 10.0])  # advance time past the 5s wait without real sleeping
        real_time = rc_launcher.time.time
        rc_launcher.time.time = lambda: next(ticks, 100.0)
        try:
            rc_launcher.takeover("proj")
        finally:
            rc_launcher.time.time = real_time
        self.assertIn((111, rc_launcher.signal.SIGKILL), self.killed)

    def test_launch_logs_snapshot_and_takeover(self):
        rc_launcher.RESUME, rc_launcher.SPAWN, rc_launcher.TAKEOVER = "continue", "same-dir", True
        os.environ["RC_SNAPSHOT"] = "1"
        os.environ["RC_STATE_DIR"] = "/tmp/st"
        events = []
        rc_launcher.log_event = lambda *a: events.append(a)
        root = os.path.join(rc_launcher.PARENT, "proj")

        def run(cmd, **kw):
            self.calls.append(cmd)
            key = " ".join(map(str, cmd))
            if "has-session" in key:
                return proc(returncode=1)
            if "is-inside-work-tree" in key:
                return proc(returncode=0)
            if "stash create" in key:
                return proc(stdout="deadbeef\n")
            if "pgrep" in key:
                return proc(stdout="111\n")
            if "comm=" in key:
                return proc(stdout="claude\n")
            if "-Fn" in key:
                return proc(stdout=f"n{root}\n")
            if "pane_dead" in key:
                return proc(stdout="0\n")
            return proc()
        rc_launcher.subprocess.run = run
        self.alive = set()  # takeover target dies cleanly
        try:
            self.assertEqual(rc_launcher.launch("proj"), ("launched", None))
        finally:
            os.environ.pop("RC_SNAPSHOT", None)
            os.environ.pop("RC_STATE_DIR", None)
        kinds = [e[0] for e in events]
        self.assertIn("snap", kinds)
        self.assertIn("takeover", kinds)


if __name__ == "__main__":
    unittest.main()
