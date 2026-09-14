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
import rc_tmux

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

    def test_rc_projects_finds_external_remote_control_and_splits_from_desk(self):
        root = os.path.join(rc_config.PARENT, "proj")
        self.desk = {
            "111": desk(root),  # plain desk claude -> desk, not rc
            "222": desk(
                root, command="claude --remote-control proj"
            ),  # external RC -> rc
        }
        # the two scans partition the same processes by kind, no overlap
        self.assertEqual(rc_desk.rc_projects(), ["proj"])
        self.assertEqual(rc_desk.desk_projects(), ["proj"])
        self.assertEqual(rc_desk.remote_sessions("proj"), [222])
        self.assertEqual(rc_desk.desktop_sessions("proj"), [111])

    def test_status_payload_extrc_excludes_launcher_tmux_projects(self):
        # a project that has a launcher tmux rc- session shows as running, not external, even
        # though its claude is also --remote-control; only sessions started OUTSIDE go to extrc
        os.makedirs(os.path.join(rc_config.PARENT, "solo"))
        self.desk = {
            "222": desk(
                os.path.join(rc_config.PARENT, "proj"),
                command="claude --remote-control p",
            ),
            "333": desk(
                os.path.join(rc_config.PARENT, "solo"),
                command="claude --remote-control s",
            ),
        }
        self.responses = {
            "list-sessions": proc(stdout="rc-proj\n")
        }  # proj is a tmux rc- session
        p = rc_sessions.status_payload()
        self.assertEqual(p["running"], ["proj"])
        self.assertEqual(
            p["extrc"], ["solo"]
        )  # proj dropped (already running); solo kept

    def test_stop_invalidates_extrc_so_closed_session_does_not_reappear(self):
        # a same-dir tmux RC session is in rc_projects AND running while alive. After the
        # normal ✕ (stop), running drops it — stop() must also drop the rc_projects cache,
        # or extrc = rc_projects - running re-lists it with a spurious 📡 badge until the TTL.
        self.desk = {
            "222": desk(
                os.path.join(rc_config.PARENT, "proj"),
                command="claude --remote-control proj",
            )
        }
        self.responses = {
            "list-sessions": proc(stdout="rc-proj\n")
        }  # alive as a tmux rc-
        rc_desk.rc_projects()  # warm the cache with proj present
        # now it is stopped: has-session gone, graceful_stop confirms, the RC pid is dead
        seq = iter([True, False, False, False])
        self.addCleanup(setattr, rc_tmux, "has_session", rc_tmux.has_session)
        rc_tmux.has_session = lambda s: next(seq, False)
        rc_config.STOP_WAIT = 0
        self.desk = {}  # the RC pid is gone after the kill
        self.assertEqual(rc_sessions.stop("proj"), ("stopped", None))
        self.responses = {"list-sessions": proc(stdout="")}  # running() now empty
        self.assertEqual(rc_sessions.status_payload()["extrc"], [])  # not re-listed

    def test_stop_on_a_tmux_project_uses_tmux_not_a_pid_kill(self):
        # stop() checks tmux FIRST, so a project with a launcher tmux session takes the
        # graceful C-c path and its claude is never pid-killed — even though the same project
        # also has a --remote-control process (the external fallback must not fire here)
        self.desk = {
            "222": desk(
                os.path.join(rc_config.PARENT, "proj"),
                command="claude --remote-control proj",
            )
        }
        seq = iter([True, False, False, False])  # alive at entry, gone by graceful_stop
        self.addCleanup(setattr, rc_tmux, "has_session", rc_tmux.has_session)
        rc_tmux.has_session = lambda s: next(seq, False)
        rc_config.STOP_WAIT = 0
        self.assertEqual(rc_sessions.stop("proj"), ("stopped", None))
        self.assertEqual(self.killed, [])  # no pid SIGTERM/SIGKILL — took the tmux path
        self.assertTrue(any("send-keys" in c and "C-c" in c for c in self._cmds()))

    def test_close_remote_kills_only_the_rc_session(self):
        self.desk = {
            "111": desk(
                os.path.join(rc_config.PARENT, "proj")
            ),  # desk: must be left alone
            "222": desk(
                os.path.join(rc_config.PARENT, "proj"),
                command="claude --remote-control proj",
            ),
        }
        self.alive = set()  # dies on SIGTERM
        self.assertEqual(rc_desk.close_remote("proj"), [222])
        self.assertIn((222, signal.SIGTERM), self.killed)
        self.assertNotIn(
            (111, signal.SIGTERM), self.killed
        )  # the desk session survives

    def test_stop_falls_through_to_external_rc_when_no_tmux(self):
        # no tmux session -> plain stop() falls through and kills the external RC process,
        # so a caller need not know the launch method (ext=1 is redundant now); "idle" when
        # neither a tmux session nor an external RC process exists
        self.desk = {
            "222": desk(
                os.path.join(rc_config.PARENT, "proj"),
                command="claude --remote-control proj",
            )
        }
        self.responses = {"has-session": proc(returncode=1)}  # no tmux -> the fallback
        rc_desk.rc_projects()  # a warm cache the stop must invalidate
        self.alive = set()
        self.assertEqual(rc_sessions.stop("proj"), ("stopped", None))
        self.assertIn((222, signal.SIGTERM), self.killed)
        self.assertEqual(rc_sessions.stop("nomatch"), ("idle", None))  # nothing there

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
