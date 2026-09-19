"""launch / stop / _spawn and the decisions around them — resume vs fresh, the desk-thread
fork, the interactive-prompt answer, the death-reason classification — driven on canned
tmux output so no claude is actually spawned. The desk-session probes those paths call into
are tests/test_desk.py."""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

import rc_config
import rc_sessions
import rc_settings
import rc_tmux

from tests._harness import MockedToolsCase, desk, env, proc, spawn_ok


class OrchestrationTest(MockedToolsCase):
    def _seed_desk_thread(self, proj: str) -> None:
        """Write a desk-resumable (entrypoint cli) transcript so launch() takes the resume path."""
        slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.join(rc_config.PARENT, proj))
        d = rc_config.CLAUDE_PROJECTS / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "s1.jsonl").write_text('{"entrypoint":"cli","type":"user"}\n')

    # --- pure command builders ---

    def test_launch_cmd_resume_vs_fresh(self):
        c = rc_sessions.CLAUDE
        n = rc_sessions.rc_name(
            "proj"
        )  # hostname-prefixed, so the app shows the origin
        rc_settings.MODEL = "claude-sonnet-5"
        m = [
            "--model",
            "claude-sonnet-5",
        ]  # pinned on EVERY form, right after the binary
        rc_settings.RESUME, rc_settings.SPAWN = "continue", "same-dir"
        cmd, resuming = rc_sessions.launch_cmd("proj")
        self.assertTrue(resuming)
        # exact argv
        self.assertEqual(cmd, [c, *m, "--continue", "--remote-control", n])
        rc_settings.RESUME = "fork"
        self.assertEqual(
            rc_sessions.launch_cmd("proj")[0],
            [c, *m, "--continue", "--fork-session", "--remote-control", n],
        )
        # fresh same-dir -> FLAG form: local-first, desk-resumable
        rc_settings.RESUME, rc_settings.SPAWN = "off", "same-dir"
        cmd, resuming = rc_sessions.launch_cmd("proj")
        self.assertFalse(resuming)
        # never the relay-only subcommand; --model precedes --remote-control
        self.assertEqual(cmd, [c, *m, "--remote-control", n])
        # not same-dir -> subcommand form; --model is a GLOBAL flag before the subcommand
        rc_settings.SPAWN = "worktree"
        cmd, resuming = rc_sessions.launch_cmd("proj")
        self.assertFalse(resuming)
        self.assertEqual(
            cmd, [c, *m, "remote-control", "--name", n, "--spawn", "worktree"]
        )

    def test_death_reason_classifies(self):
        self.responses = {"capture-pane": proc(stdout="please trust this workspace\n")}
        self.assertEqual(rc_sessions.death_reason("s"), "untrusted dir")
        self.responses = {"capture-pane": proc(stdout="you are logged out\n")}
        self.assertIn("login expired", rc_sessions.death_reason("s"))
        self.responses = {
            "capture-pane": proc(stdout="Pane is dead\nboom other error\n")
        }
        self.assertEqual(rc_sessions.death_reason("s"), "boom other error")
        # the marker TRAILING the real error is the case the filter exists for: with only
        # the leading-marker fixture above, deleting the filter stayed green
        self.responses = {
            "capture-pane": proc(stdout="boom real error\nPane is dead\n")
        }
        self.assertEqual(rc_sessions.death_reason("s"), "boom real error")
        # "credential" in the login-keyword list was unexercised
        self.responses = {"capture-pane": proc(stdout="invalid credentials\n")}
        self.assertIn("login expired", rc_sessions.death_reason("s"))
        self.responses = {"capture-pane": proc(stdout="")}
        self.assertEqual(rc_sessions.death_reason("s"), "exited immediately")

    def test_has_desk_thread_discrimination_and_cap(self):
        # The fork deciding every launch: sdk-cli (relay-only mirror) must not count,
        # cli must, and only within the first 256KiB — the cap is the 525MB-slurp guard.
        slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.join(rc_config.PARENT, "proj"))
        d = rc_config.CLAUDE_PROJECTS / slug
        d.mkdir(parents=True)
        f = d / "s.jsonl"
        f.write_text('{"entrypoint":"sdk-cli","type":"user"}\n')
        # phone-born mirror: not resumable
        self.assertFalse(rc_sessions.has_desk_thread("proj"))
        pad = '{"type":"pad","d":"' + "z" * 100 + '"}\n'
        n_within = 200_000 // len(pad)  # marker lands ~200KB in — inside the cap
        n_beyond = 262_144 // len(pad) + 5  # pad alone already exceeds the cap
        f.write_text(pad * n_within + '{"entrypoint":"cli"}\n' + pad * 500)
        # cli within 256KiB of a big file
        self.assertTrue(rc_sessions.has_desk_thread("proj"))
        f.write_text(pad * n_beyond + '{"entrypoint":"cli"}\n')
        # cli only AFTER the cap: unseen, which is the point — reading past 262144
        # would mean full-file slurps per tap again
        self.assertFalse(rc_sessions.has_desk_thread("proj"))

    def test_launch_takes_over_a_running_session(self):
        # A live rc- session is no longer a no-op "already": launch reaps it (graceful
        # stop) and starts one fresh client, so a phone relaunch always lands a single
        # clean session over whatever the computer had open.
        rc_settings.RESUME, rc_config.STOP_WAIT = "off", 0
        seq = iter([True, False, False, False])  # alive at entry, gone by graceful_stop
        self.addCleanup(setattr, rc_tmux, "has_session", rc_tmux.has_session)
        rc_tmux.has_session = lambda s: next(seq, False)
        self.responses = {"pane_dead": proc(stdout="0\n")}
        events = []
        rc_config.log_event = lambda *a: events.append(a)
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        # it Ctrl-C'd the prior session (took over) rather than short-circuiting
        self.assertTrue(any("send-keys" in c and "C-c" in c for c in self._cmds()))
        self.assertIn("takeover", [e[0] for e in events])

    def test_launch_bails_when_new_session_name_collides(self):
        # Concurrency guard: two /launch on one proj both reap and both reach _spawn. If
        # new-session fails because a racing takeover already holds the name, _spawn must
        # NOT inspect the pre-existing (alive) session and report it "launched" — the
        # phantom that the removed "already" no-op used to make impossible.
        rc_settings.RESUME, rc_config.STOP_WAIT = "off", 0
        seq = iter([True, False, False, False])  # alive at entry, gone by graceful_stop
        self.addCleanup(setattr, rc_tmux, "has_session", rc_tmux.has_session)
        rc_tmux.has_session = lambda s: next(seq, False)
        # new-session collides; the pre-existing session is alive (pane not dead) — so the
        # old code would have sailed through to a phantom ("launched", None)
        self.responses = {
            "new-session": proc(returncode=1),
            "pane_dead": proc(stdout="0\n"),
        }
        status, reason = rc_sessions.launch("proj")
        self.assertEqual(status, "failed")
        self.assertIn("new-session", reason)
        # never touched the session it did not create
        self.assertFalse(any("remain-on-exit" in c for c in self._cmds()))

    def test_launch_fails_loudly_when_prior_session_wont_die(self):
        # A prior session that survives SIGINT and kill-session can't be taken over —
        # the new session would collide on its name — so launch fails loudly instead of
        # silently attaching to (and reporting "launched" over) the old one.
        rc_config.STOP_WAIT = 0
        self.responses = {"has-session": proc(returncode=0)}  # never dies
        status, reason = rc_sessions.launch("proj")
        self.assertEqual(status, "failed")
        self.assertIn("would not stop", reason)
        self.assertFalse(any("new-session" in c for c in self._cmds()))

    def test_launch_fresh_success(self):
        # fresh path, no takeover
        rc_settings.RESUME, rc_settings.SPAWN = "off", "same-dir"
        self.responses = spawn_ok()
        # a live desk claude in the project: a FRESH launch must leave it alone (the
        # takeover guard's `resuming` condition was mutation-deletable with 127 green)
        self.desk = {"777": desk(os.path.join(rc_config.PARENT, "proj"))}
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        self.assertEqual(self.killed, [])
        # ensure_trusted ran at the call site: the trust flag landed in CLAUDE_JSON
        trusted = json.loads(Path(rc_config.CLAUDE_JSON).read_text())
        key = os.path.join(rc_config.PARENT, "proj")
        self.assertTrue(trusted["projects"][key]["hasTrustDialogAccepted"])
        newsession = next(
            c for c in self.calls if "new-session" in " ".join(map(str, c))
        )
        self.assertIn("RC_PROJECT=proj", newsession)  # the hook and badge key on it
        self.assertEqual(
            newsession[-1],  # the exact claude command tmux is told to run —
            f"{rc_sessions.CLAUDE} --model {rc_settings.MODEL} --remote-control {rc_sessions.rc_name('proj')}",
        )  # flag form: local-first
        # rooted in the project dir (same-dir is load-bearing) and tagged so the state hook fires
        self.assertEqual(
            newsession[newsession.index("-c") + 1],
            os.path.join(rc_config.PARENT, "proj"),
        )
        self.assertIn("RC_REMOTE=rc-proj", newsession)
        # remain-on-exit toggled on (a dead pane survives for death_reason) then off
        cmds = self._cmds()
        self.assertTrue(any("remain-on-exit on" in c for c in cmds))
        self.assertTrue(any("remain-on-exit off" in c for c in cmds))

    def test_launch_injects_local_bin_on_path(self):
        # Phone-launched sessions inherit a truncated PATH missing ~/.local/bin, so MCP
        # servers/hooks claude spawns by name (uvx, uv, ruff) fail remotely. launch()
        # must prepend it via a per-session -e (the plist form is non-deterministic:
        # tmux sessions inherit whichever env started the tmux SERVER first).
        rc_settings.RESUME = "off"
        self.responses = spawn_ok()
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        newsession = next(
            c for c in self.calls if "new-session" in " ".join(map(str, c))
        )
        path_arg = next(a for a in newsession if str(a).startswith("PATH="))
        self.assertEqual(newsession[newsession.index(path_arg) - 1], "-e")
        local_bin = os.path.expanduser("~/.local/bin")
        # exact value: prefix AND tail — an empty tail (dropping the os.environ part)
        # would strip /usr/bin:/bin from every phone session and still pass a
        # startswith-only check
        self.assertEqual(path_arg, f"PATH={local_bin}:{os.environ['PATH']}")

    def test_launch_auto_answers_resume_prompt_with_full(self):
        # A huge thread makes claude ask summary-vs-full before it registers with the
        # relay; headless, that prompt must be answered or the phone never sees the
        # session. The owner's standing choice is FULL resume (never compact): Down
        # moves off the highlighted summary option, Enter confirms.
        rc_settings.RESUME, rc_settings.SPAWN = "off", "same-dir"
        self.responses = spawn_ok() | {
            "capture-pane": proc(
                stdout="Resume from summary (recommended)\nEnter to confirm\n"
            )
        }
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        answer = next(c for c in self.calls if "send-keys" in " ".join(map(str, c)))
        # full resume, not the summary default
        self.assertEqual(answer[-2:], ["Down", "Enter"])

    def test_launch_fails_loudly_on_unknown_prompt(self):
        # Any OTHER confirm-style prompt is a failed launch with the reason surfaced —
        # never a phantom "launched" whose session is invisible in the app.
        rc_settings.RESUME, rc_settings.SPAWN = "off", "same-dir"
        self.responses = spawn_ok() | {
            "capture-pane": proc(
                stdout="Choose a login method\nEnter to confirm · Esc to cancel\n"
            )
        }
        status, reason = rc_sessions.launch("proj")
        self.assertEqual(status, "failed")
        self.assertIn("stuck at interactive prompt", reason)
        self.assertIn("Choose a login method", reason)
        self.assertTrue(any("kill-session" in c for c in self._cmds()))

    def test_launch_dead_pane_reports_reason_and_kills(self):
        rc_settings.RESUME = "off"
        self.responses = {
            "has-session": proc(returncode=1),
            "pane_dead": proc(stdout="1\n"),
            "capture-pane": proc(stdout="Error: trust this folder first\n"),
        }
        self.assertEqual(rc_sessions.launch("proj"), ("failed", "untrusted dir"))
        self.assertTrue(any("kill-session" in c for c in self._cmds()))

    def test_launch_resume_falls_back_to_fresh(self):
        rc_settings.RESUME, rc_settings.SPAWN = "continue", "same-dir"
        # a cli thread exists, so the resume path genuinely runs
        self._seed_desk_thread("proj")
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

        subprocess.run = run
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        spawns = [c[-1] for c in self.calls if "new-session" in " ".join(map(str, c))]
        self.assertEqual(len(spawns), 2)
        self.assertIn("--continue", spawns[0])  # first attempt resumes
        # the fallback is a FRESH flag-form launch
        self.assertEqual(
            spawns[1],
            f"{rc_sessions.CLAUDE} --model {rc_settings.MODEL} --remote-control {rc_sessions.rc_name('proj')}",
        )

    def test_launch_skips_resume_without_desk_thread(self):
        # Brand-new or phone-born (relay-only) project: no cli transcript exists, so launch()
        # must go STRAIGHT to the fresh flag form — one spawn, no doomed --continue attempt
        # (whose late death used to read as a phantom "launched" and evaporate).
        rc_settings.RESUME, rc_settings.SPAWN = "continue", "same-dir"
        self.responses = spawn_ok()
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        spawns = [c[-1] for c in self.calls if "new-session" in " ".join(map(str, c))]
        self.assertEqual(
            spawns,
            [
                f"{rc_sessions.CLAUDE} --model {rc_settings.MODEL} --remote-control {rc_sessions.rc_name('proj')}"
            ],
        )

    def test_stop_sigint_then_kill_and_confirms(self):
        # survives SIGINT: kill-session follows, and a session still alive after that
        # is reported as failed — stop() no longer says "stopped" without looking
        rc_config.STOP_WAIT = 0
        self.responses = {"has-session": proc(returncode=0)}
        self.assertEqual(
            rc_sessions.stop("proj"),
            ("failed", "still alive after SIGINT and kill-session"),
        )
        cmds = self._cmds()
        presses = [i for i, c in enumerate(cmds) if "send-keys" in c and "C-c" in c]
        kill = next(i for i, c in enumerate(cmds) if "kill-session" in c)
        # claude's TUI needs Ctrl-C TWICE (one prints "Press Ctrl-C again to exit" and
        # stays up — verified live on 2.1.260), both before the kill fallback
        self.assertEqual(len(presses), 2)
        self.assertLess(presses[-1], kill)

    def test_stop_idle_when_no_session_exists(self):
        # a wrong/unmanaged proj (no rc-<proj> session) must read "idle", not "stopped" —
        # and stop() must not even send C-c or kill at a session that was never there
        self.responses = {"has-session": proc(returncode=1)}
        self.assertEqual(rc_sessions.stop("proj"), ("idle", None))
        self.assertFalse(
            any("send-keys" in c or "kill-session" in c for c in self._cmds())
        )

    def test_stop_reports_stopped_when_a_live_session_dies(self):
        # exists at entry, gone after the C-c: "stopped" is a confirmed kill, no fallback needed
        seq = iter(
            [True, False, False, False]
        )  # entry True, then gone for graceful_stop
        self.addCleanup(setattr, rc_tmux, "has_session", rc_tmux.has_session)
        rc_tmux.has_session = lambda s: next(seq, False)
        self.assertEqual(rc_sessions.stop("proj"), ("stopped", None))
        self.assertTrue(any("send-keys" in c and "C-c" in c for c in self._cmds()))
        self.assertFalse(any("kill-session" in c for c in self._cmds()))

    def test_tmux_targets_are_exact_match(self):
        # A bare -t prefix-matches: with rc-proj absent and rc-proj-sub live, stop()
        # would C-c the SIBLING's session and launch() reap it as a takeover (verified
        # against a live tmux). Every -t target must be the exact-match `=name` form.
        rc_settings.RESUME, rc_settings.SPAWN = "continue", "same-dir"
        self.responses = spawn_ok()
        rc_sessions.launch("proj")
        rc_sessions.stop("proj")
        targets = [c[i + 1] for c in self.calls for i, a in enumerate(c) if a == "-t"]
        self.assertGreaterEqual(len(targets), 5)  # spawn control calls + stop's pair
        self.assertEqual(set(targets), {"=rc-proj"})

    # --- login_status / running ---

    def test_login_status_parses_claude_auth(self):
        for out, want in (
            ('{"loggedIn": true}', "ok"),
            ('{"loggedIn": false}', "loggedout"),
            ("not json", "unknown"),
        ):
            self.responses = {"auth status": proc(stdout=out)}
            rc_sessions.login_status.invalidate()
            self.assertEqual(rc_sessions.login_status(), want)

    def test_running_parses_and_tolerates_no_tmux(self):
        self.responses = {"list-sessions": proc(stdout="rc-alpha\nrc-beta\nother\n")}
        self.assertEqual(rc_tmux.running(), {"alpha", "beta"})

    def test_grouped_project_session_name_roundtrips_through_the_plus(self):
        # a group/name project can't be a raw tmux session name ('/' breaks send-keys), so
        # session_name swaps it for '+'; running() must map it back so the badge matches
        self.assertEqual(rc_tmux.session_name("work/aws"), "rc-work+aws")
        self.assertEqual(rc_tmux.session_name("flat"), "rc-flat")  # flat unchanged
        self.responses = {"list-sessions": proc(stdout="rc-work+aws\nrc-flat\n")}
        self.assertEqual(rc_tmux.running(), {"work/aws", "flat"})

        def boom(cmd, **kw):
            raise FileNotFoundError

        subprocess.run = boom
        self.assertEqual(rc_tmux.running(), set())

    def test_launch_logs_snapshot_and_takeover(self):
        rc_settings.RESUME, rc_settings.SPAWN = "continue", "same-dir"
        # takeover only guards a real resume; needs a cli thread
        self._seed_desk_thread("proj")
        env(self, RC_SNAPSHOT="1", RC_STATE_DIR="/tmp/st")
        events = []
        rc_config.log_event = lambda *a: events.append(a)
        self.desk = {"111": desk(os.path.join(rc_config.PARENT, "proj"))}
        self.responses = {
            "has-session": proc(returncode=1),
            "is-inside-work-tree": proc(returncode=0),
            "stash create": proc(stdout="deadbeef\n"),
            "pane_dead": proc(stdout="0\n"),
        }
        self.alive = set()  # takeover target dies cleanly
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        kinds = [e[0] for e in events]
        self.assertIn("snap", kinds)
        self.assertIn("takeover", kinds)
        # takeover is unconditional now: the desk claude was actually signalled, not
        # merely logged (a bare log line would survive deleting the os.kill)
        self.assertTrue(any(pid == 111 for pid, _ in self.killed))


if __name__ == "__main__":
    unittest.main()
