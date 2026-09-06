"""Desk (non-remote) claude sessions: the pgrep/ps/lsof scan that finds them, the cwd
boundary that scopes them to one project, the SIGTERM -> wait -> SIGKILL takeover, and the
TTL cache the 5s status poll leans on. All on canned probe output — no real process is
ever scanned or signalled."""

import json
import os
import signal
import subprocess
import time
import unittest
from pathlib import Path

import rc_config
import rc_desk
import rc_sessions

from tests._harness import MockedToolsCase, desk, proc


class DeskTest(MockedToolsCase):
    def test_run_tolerates_missing_binary(self):
        def boom(cmd, **kw):
            raise OSError("no such tool")

        subprocess.run = boom
        self.assertEqual(rc_desk._run(["nope"]), "")

    def test_alive_reflects_os_kill(self):
        self.alive = {42}
        self.assertTrue(rc_desk._alive(42))
        self.assertFalse(rc_desk._alive(99))

    def test_pid_cwd_via_proc_symlink(self):
        # both singletons are restored by the harness's restore_globals()
        os.path.islink = lambda p: p == "/proc/777/cwd"
        os.readlink = lambda p: "/the/cwd"
        self.assertEqual(rc_desk._pid_cwd("777"), "/the/cwd")

    def test_desktop_sessions_scopes_by_cwd(self):
        root = os.path.join(rc_config.PARENT, "proj")
        self.desk = {
            "111": desk(f"{root}/sub"),
            # 222 is an RC server, not a desktop client
            "222": desk(f"{root}/sub", command="claude remote-control"),
            # 333 isn't a claude process; 555 is a lookalike binary — loosening the
            # equality check to a substring match would include it
            "333": desk(f"{root}/sub", comm="grep"),
            # 444 lives in the SIBLING-PREFIX dir projx: real ~/projects has such pairs
            # (alpha/alpha-sub), and a bare startswith(root) mutant would cross-kill
            # it — the == root / root+os.sep boundary is load-bearing
            "444": desk(f"{root}x"),
            "555": desk(f"{root}/sub", comm="claude-helper"),
        }
        self.assertEqual(rc_desk.desktop_sessions("proj"), [111])
        # the pgrep match must be by full command line (-f), not a loose tool-name check
        self.assertTrue(
            any("-f" in c for c in self.calls if "pgrep" in " ".join(map(str, c)))
        )

    def test_takeover_sigterms_and_returns_pids(self):
        self.desk = {"111": desk(os.path.join(rc_config.PARENT, "proj"))}
        # after SIGTERM the process is gone -> _alive False, no SIGKILL
        self.alive = set()
        self.assertEqual(rc_desk.takeover("proj"), [111])
        self.assertIn((111, signal.SIGTERM), self.killed)

    def test_takeover_sigkills_straggler(self):
        self.desk = {"111": desk(os.path.join(rc_config.PARENT, "proj"))}
        self.alive = {111}  # survives SIGTERM -> forces the SIGKILL path
        # advance time past the 5s wait without real sleeping
        ticks = iter([0.0, 1.0, 10.0])
        time.monotonic = lambda: next(ticks, 100.0)  # restore_globals() puts it back
        sleeps = []
        time.sleep = lambda s: sleeps.append(s)
        rc_desk.takeover("proj")
        self.assertIn((111, signal.SIGKILL), self.killed)
        # the grace period must actually elapse first: deleting the wait loop kept
        # every test green while SIGKILL landed instantly
        self.assertTrue(sleeps, "SIGKILL fired without waiting out the SIGTERM grace")
        self.assertLess(
            self.killed.index((111, signal.SIGTERM)),
            self.killed.index((111, signal.SIGKILL)),
        )

    def test_desk_projects_finds_plain_claude_by_cwd(self):
        root = os.path.join(rc_config.PARENT, "proj")
        self.desk = {
            "111": desk(root),  # a desk session inside proj
            "222": desk(root, command="claude --remote-control proj"),  # RC server: out
            "333": desk("/somewhere/else"),  # outside PARENT: out
        }
        self.assertEqual(rc_desk.desk_projects(), ["proj"])
        # second call inside the TTL is served from cache: no new pgrep forked

    def test_desk_projects_maps_added_root_cwd_to_label(self):
        rc_desk.desk_projects.invalidate()
        base = os.path.dirname(rc_config.PARENT)
        media = os.path.realpath(os.path.join(base, "media"))
        os.makedirs(os.path.join(media, "movie"))
        Path(rc_config.ROOTS_FILE).write_text(json.dumps([media]))
        self.desk = {"111": desk(os.path.join(media, "movie"))}
        self.assertEqual(rc_desk.desk_projects(), ["media/movie"])

    def test_desk_projects_maps_grouped_project_to_group_name(self):
        # a desk claude inside a category maps to "group/name" (matching projects()), else the
        # badge never matches the launcher list and the desk dot vanishes for grouped projects
        self.addCleanup(setattr, rc_config, "GROUPS", rc_config.GROUPS)
        rc_config.GROUPS = frozenset({"work"})
        self.desk = {
            "111": desk(os.path.join(rc_config.PARENT, "work", "aws")),  # grouped
            "222": desk(os.path.join(rc_config.PARENT, "flat")),  # flat, unchanged
        }
        self.assertEqual(rc_desk.desk_projects(), ["flat", "work/aws"])
        scans = self._pgreps()
        rc_desk.desk_projects()
        self.assertEqual(self._pgreps(), scans)

    def test_desk_projects_ttl_expiry_rescans(self):
        rc_config.DESK_TTL = 0.0  # expire immediately: every call must rescan
        self.responses = {"pgrep": proc(stdout="")}
        rc_desk.desk_projects()
        rc_desk.desk_projects()
        # a frozen deadline check would serve the stale cache
        self.assertEqual(self._pgreps(), 2)

    def test_desk_stop_graceful_and_clears_cache(self):
        self.desk = {"111": desk(os.path.join(rc_config.PARENT, "proj"))}
        rc_desk.desk_projects()  # a warm cache the stop must invalidate
        self.alive = set()  # dies cleanly on SIGTERM -> no SIGKILL escalation
        self.assertEqual(rc_sessions.desk_stop("proj"), ("stopped", None))
        self.assertIn((111, signal.SIGTERM), self.killed)  # graceful first
        self.assertNotIn((111, signal.SIGKILL), self.killed)
        scans = self._pgreps()
        rc_desk.desk_projects()
        # badge clears on the next poll: the stop dropped the cache, so this rescans
        # rather than answering "proj" from the warm entry
        self.assertGreater(self._pgreps(), scans)

    def test_desk_stop_idle_when_nothing_running(self):
        subprocess.run = lambda cmd, **kw: proc(stdout="")
        self.assertEqual(rc_sessions.desk_stop("proj"), ("idle", None))


if __name__ == "__main__":
    unittest.main()
