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
        rc_config.RESUME, rc_config.SPAWN = "continue", "same-dir"
        cmd, resuming = rc_sessions.launch_cmd("proj")
        self.assertTrue(resuming)
        self.assertEqual(
            cmd, [c, "--continue", "--remote-control", "proj"]
        )  # exact argv
        rc_config.RESUME = "fork"
        self.assertEqual(
            rc_sessions.launch_cmd("proj")[0],
            [c, "--continue", "--fork-session", "--remote-control", "proj"],
        )
        rc_config.RESUME, rc_config.SPAWN = (
            "off",
            "same-dir",
        )  # fresh same-dir -> FLAG form:
        cmd, resuming = rc_sessions.launch_cmd("proj")  # local-first, desk-resumable
        self.assertFalse(resuming)
        self.assertEqual(
            cmd, [c, "--remote-control", "proj"]
        )  # never the relay-only subcommand
        rc_config.SPAWN = (
            "worktree"  # not same-dir -> subcommand form, exact --spawn value
        )
        cmd, resuming = rc_sessions.launch_cmd("proj")
        self.assertFalse(resuming)
        self.assertEqual(
            cmd, [c, "remote-control", "--name", "proj", "--spawn", "worktree"]
        )

    # --- _run / _pid_cwd / _alive ---

    def test_death_reason_classifies(self):
        self.responses = {"capture-pane": proc(stdout="please trust this workspace\n")}
        self.assertEqual(rc_sessions.death_reason("s"), "untrusted dir")
        self.responses = {"capture-pane": proc(stdout="you are logged out\n")}
        self.assertIn("login expired", rc_sessions.death_reason("s"))
        self.responses = {
            "capture-pane": proc(stdout="Pane is dead\nboom other error\n")
        }
        self.assertEqual(rc_sessions.death_reason("s"), "boom other error")
        self.responses = {"capture-pane": proc(stdout="")}
        self.assertEqual(rc_sessions.death_reason("s"), "exited immediately")

    # --- desktop_sessions / takeover ---

    def test_has_desk_thread_discrimination_and_cap(self):
        # The fork deciding every launch: sdk-cli (relay-only mirror) must not count,
        # cli must, and only within the first 256KiB — the cap is the 525MB-slurp guard.
        slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.join(rc_config.PARENT, "proj"))
        d = rc_config.CLAUDE_PROJECTS / slug
        d.mkdir(parents=True)
        f = d / "s.jsonl"
        f.write_text('{"entrypoint":"sdk-cli","type":"user"}\n')
        self.assertFalse(
            rc_sessions.has_desk_thread("proj")
        )  # phone-born mirror: not resumable
        pad = '{"type":"pad","d":"' + "z" * 100 + '"}\n'
        n_within = 200_000 // len(pad)  # marker lands ~200KB in — inside the cap
        n_beyond = 262_144 // len(pad) + 5  # pad alone already exceeds the cap
        f.write_text(pad * n_within + '{"entrypoint":"cli"}\n' + pad * 500)
        self.assertTrue(
            rc_sessions.has_desk_thread("proj")
        )  # cli within 256KiB of a big file
        f.write_text(pad * n_beyond + '{"entrypoint":"cli"}\n')
        self.assertFalse(
            rc_sessions.has_desk_thread("proj")
        )  # cli only AFTER the cap: unseen,
        # which is the point — reading past 262144 would mean full-file slurps per tap again

    def test_launch_already_running(self):
        self.responses = {"has-session": proc(returncode=0)}
        self.assertEqual(rc_sessions.launch("proj"), ("already", None))

    def test_launch_fresh_success(self):
        rc_config.RESUME, rc_config.SPAWN = (
            "off",
            "same-dir",
        )  # fresh path, no takeover
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
            f"{rc_sessions.CLAUDE} --remote-control proj",
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
        rc_config.RESUME = "off"
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
        rc_config.RESUME, rc_config.SPAWN = "off", "same-dir"
        self.responses = spawn_ok() | {
            "capture-pane": proc(
                stdout="Resume from summary (recommended)\nEnter to confirm\n"
            )
        }
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        answer = next(c for c in self.calls if "send-keys" in " ".join(map(str, c)))
        self.assertEqual(
            answer[-2:], ["Down", "Enter"]
        )  # full resume, not the summary default

    def test_launch_fails_loudly_on_unknown_prompt(self):
        # Any OTHER confirm-style prompt is a failed launch with the reason surfaced —
        # never a phantom "launched" whose session is invisible in the app.
        rc_config.RESUME, rc_config.SPAWN = "off", "same-dir"
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
        rc_config.RESUME = "off"
        self.responses = {
            "has-session": proc(returncode=1),
            "pane_dead": proc(stdout="1\n"),
            "capture-pane": proc(stdout="Error: trust this folder first\n"),
        }
        self.assertEqual(rc_sessions.launch("proj"), ("failed", "untrusted dir"))
        self.assertTrue(any("kill-session" in c for c in self._cmds()))

    def test_launch_resume_falls_back_to_fresh(self):
        rc_config.RESUME, rc_config.SPAWN, rc_config.TAKEOVER = (
            "continue",
            "same-dir",
            False,
        )
        self._seed_desk_thread(
            "proj"
        )  # a cli thread exists, so the resume path genuinely runs
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
        self.assertEqual(
            spawns[1],  # the fallback is a FRESH flag-form launch
            f"{rc_sessions.CLAUDE} --remote-control proj",
        )

    def test_launch_skips_resume_without_desk_thread(self):
        # Brand-new or phone-born (relay-only) project: no cli transcript exists, so launch()
        # must go STRAIGHT to the fresh flag form — one spawn, no doomed --continue attempt
        # (whose late death used to read as a phantom "launched" and evaporate).
        rc_config.RESUME, rc_config.SPAWN, rc_config.TAKEOVER = (
            "continue",
            "same-dir",
            False,
        )
        self.responses = spawn_ok()
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        spawns = [c[-1] for c in self.calls if "new-session" in " ".join(map(str, c))]
        self.assertEqual(spawns, [f"{rc_sessions.CLAUDE} --remote-control proj"])

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

    def test_stop_reports_stopped_only_when_gone(self):
        # dies on SIGINT: no kill-session needed, and "stopped" is a confirmed fact
        self.responses = {"has-session": proc(returncode=1)}
        self.assertEqual(rc_sessions.stop("proj"), ("stopped", None))
        self.assertFalse(any("kill-session" in c for c in self._cmds()))

    def test_tmux_targets_are_exact_match(self):
        # A bare -t prefix-matches: with rc-proj absent and rc-proj-sub live, stop()
        # would C-c the SIBLING's session and launch() report "already" (verified
        # against a live tmux). Every -t target must be the exact-match `=name` form.
        rc_config.RESUME, rc_config.SPAWN, rc_config.TAKEOVER = (
            "continue",
            "same-dir",
            False,
        )
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

        def boom(cmd, **kw):
            raise FileNotFoundError

        subprocess.run = boom
        self.assertEqual(rc_tmux.running(), set())

    # --- snapshot failure branches ---

    def test_resume_with_takeover_off_leaves_desk_sessions_alone(self):
        rc_config.RESUME, rc_config.SPAWN, rc_config.TAKEOVER = (
            "continue",
            "same-dir",
            False,
        )
        self._seed_desk_thread("proj")
        self.desk = {"777": desk(os.path.join(rc_config.PARENT, "proj"))}
        self.responses = spawn_ok()
        self.assertEqual(rc_sessions.launch("proj"), ("launched", None))
        self.assertEqual(self.killed, [])  # RC_TAKEOVER=0 was mutation-deletable

    def test_launch_logs_snapshot_and_takeover(self):
        rc_config.RESUME, rc_config.SPAWN, rc_config.TAKEOVER = (
            "continue",
            "same-dir",
            True,
        )
        self._seed_desk_thread(
            "proj"
        )  # takeover only guards a real resume; needs a cli thread
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


if __name__ == "__main__":
    unittest.main()
