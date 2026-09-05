"""Unit tests for the launcher's filesystem/logic functions — project discovery, the
create/trust flow, the session-state rank+TTL filter, breadcrumb/size rendering and the
one-pass template fill. Every filesystem-touching global is redirected to a tmp dir; the
tmux/process-orchestration paths (launch/stop/spawn/takeover) are left to integration, git
state to test_git.py."""

import io
import json
import os
import shutil
import socketserver
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import sys
import subprocess
import rc_config
import rc_desk
import rc_git
import rc_launcher
import rc_sessions
import rc_share
import rc_state
import rc_templates
import rc_tmux

from tests._harness import env, restore_globals

_REAL_LOG = rc_config.log_event  # captured before any test stubs it


class FunctionTest(unittest.TestCase):
    def setUp(self):
        restore_globals(self)
        self.tmp = tempfile.mkdtemp()
        rc_config.PARENT = os.path.join(self.tmp, "projects")
        os.makedirs(rc_config.PARENT)
        rc_sessions.STATE_DIR = Path(self.tmp, "state")
        rc_config.CLAUDE_JSON = os.path.join(self.tmp, "claude.json")
        rc_config.log_event = lambda *a: None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parent_resolves_a_symlinked_projects_parent_to_physical(self):
        # PARENT is realpath'd at import so the trust key matches claude's own
        # physical-getcwd key. Drive a FRESH import with a symlinked RC_PROJECTS_PARENT: a
        # test that sets rc_config.PARENT itself can't see the realpath being dropped at
        # rc_config.py:31. This goes red if that realpath is removed.
        real = os.path.realpath(os.path.join(self.tmp, "real"))
        os.makedirs(real)
        link = os.path.join(self.tmp, "link")
        os.symlink(real, link)
        repo = os.path.dirname(os.path.abspath(rc_config.__file__))
        out = subprocess.run(
            [sys.executable, "-c", "import rc_config; print(rc_config.PARENT)"],
            capture_output=True,
            text=True,
            cwd=repo,
            env={**os.environ, "RC_PROJECTS_PARENT": link},
        ).stdout.strip()
        self.assertEqual(out, real)  # resolved to the physical dir
        self.assertNotEqual(out, link)  # not the symlink path

    def _proj(self, name: str) -> str:
        p = os.path.join(rc_config.PARENT, name)
        os.makedirs(p, exist_ok=True)
        return p

    # --- create() ---

    def test_create_makes_dir_git_and_stub(self):
        status, reason = rc_sessions.create("myproj")
        self.assertEqual(status, "created")
        self.assertIsNone(reason)
        root = os.path.join(rc_config.PARENT, "myproj")
        self.assertTrue(os.path.isdir(os.path.join(root, ".git")))
        self.assertEqual(Path(root, "CLAUDE.md").read_text(), "# myproj\n")

    def test_create_rejects_bad_name(self):
        self.assertEqual(rc_sessions.create("../evil")[0], "badname")
        self.assertEqual(rc_sessions.create("a b")[0], "badname")
        # a dot is banned: rc-<name> is a tmux target and "rc-a.b" parses as session.pane,
        # so the session would be untargetable and stop() would report a phantom success
        self.assertEqual(rc_sessions.create("a.b")[0], "badname")
        self.assertEqual(rc_sessions.create(".claude")[0], "badname")

    def test_create_refuses_existing(self):
        self._proj("dup")
        self.assertEqual(rc_sessions.create("dup")[0], "exists")

    # --- projects() ---

    def test_projects_lists_valid_dirs_sorted(self):
        self._proj("beta_1")
        self._proj("alpha")
        self._proj("with space")  # NAME_RE rejects the space
        self._proj(".hidden")  # a dot-dir (.claude, .ruff_cache) is not a project
        open(os.path.join(rc_config.PARENT, "afile"), "w").close()  # a file, not a dir
        self.assertEqual(rc_config.projects(), ["alpha", "beta_1"])

    # --- session_states() ---

    def test_session_states_ranks_and_expires(self):
        os.makedirs(rc_sessions.STATE_DIR)
        now = time.time()

        def write(sid, state, proj, ts):
            (rc_sessions.STATE_DIR / f"{sid}.json").write_text(
                json.dumps({"state": state, "project": proj, "ts": ts})
            )

        write("s1", "working", "alpha", now)  # fresh, working
        write("s2", "idle", "alpha", now)  # fresh, idle — lower rank than working
        write("s3", "waiting", "beta", now)  # fresh, waiting
        # stale -> ignored
        write("s4", "working", "gamma", now - rc_state.STATE_TTL - 60)
        got = rc_sessions.session_states()
        # working outranks idle for the same project
        self.assertEqual(got["alpha"], "working")
        self.assertEqual(got["beta"], "waiting")
        self.assertNotIn("gamma", got)  # stale entry dropped

    # --- human_size() / crumb_html() ---

    def test_human_size_units(self):
        self.assertEqual(rc_share.human_size(512), "512 B")
        self.assertEqual(rc_share.human_size(1536), "1.5 KB")
        self.assertEqual(rc_share.human_size(1024**2), "1.0 MB")
        # the loop-fallthrough tail
        self.assertEqual(rc_share.human_size(1024**5), "1.0 PB")

    def test_crumb_html_builds_breadcrumb(self):
        html = rc_share.crumb_html("sub/deep")
        self.assertIn('href="/files">rc-share</a>', html)
        self.assertIn('href="/files/sub">sub</a>', html)
        self.assertIn('href="/files/sub/deep">deep</a>', html)

    def test_crumb_html_encoded_rel_not_double_encoded(self):
        # _files hands crumb_html the still-percent-encoded URL remainder; requoting
        # the encoded segment produced /files/my%2520file (404) labeled "my%20file".
        share = os.path.realpath(os.path.join(self.tmp, "share"))
        os.makedirs(os.path.join(share, "my file"))
        rc_config.SHARE = share
        crumb = rc_share.crumb_html("/my%20file")
        # once-encoded, decoded label
        self.assertIn('<a href="/files/my%20file">my file</a>', crumb)
        self.assertNotIn("%2520", crumb)
        page = rc_share.share_page(
            os.path.join(share, "my file"), "/my%20file"
        ).decode()
        self.assertIn('<a href="/files/my%20file">my file</a>', page)
        self.assertNotIn("%2520", page)

    def test_ttl_cached_is_single_flight_under_concurrent_misses(self):
        # two threads missing the same key at once must produce ONE computation — the
        # page's 5s poll fires whether or not the last one finished, so without this a
        # slow scan fanned out into parallel git forks per repo
        started, release, calls = threading.Event(), threading.Event(), []

        @rc_config.ttl_cached(lambda: 60.0)
        def slow(key):
            calls.append(key)
            started.set()
            release.wait(2)
            return key

        results = []
        t1 = threading.Thread(target=lambda: results.append(slow("k")))
        t2 = threading.Thread(target=lambda: results.append(slow("k")))
        t1.start()
        started.wait(2)
        t2.start()
        release.set()
        t1.join(2)
        t2.join(2)
        self.assertEqual(results, ["k", "k"])
        self.assertEqual(calls, ["k"])
        slow.invalidate()
        slow("k")
        self.assertEqual(calls, ["k", "k"])

    def test_git_ttl_default_is_thirty_seconds(self):
        # raised 15 -> 30 on 2026-09-04 when /status started driving it; pinned so a
        # silent revert (or a silent bump) shows up here
        self.assertEqual(rc_config.GIT_TTL, float(os.environ.get("RC_GIT_TTL", "30")))

    def test_read_token_without_a_file_is_empty_not_env(self):
        # no file -> "" (main() then refuses to start); there is no env fallback any more
        env(
            self,
            RC_LAUNCHER_TOKEN_FILE=os.path.join(self.tmp, "nope"),
            RC_LAUNCHER_TOKEN="should-be-ignored",
        )
        self.assertEqual(rc_config._read_token(), "")

    def test_rows_html_carries_sort_keys_dirs_first(self):
        share = os.path.realpath(os.path.join(self.tmp, "share"))
        os.makedirs(os.path.join(share, "sub"))  # a directory
        Path(share, "a.txt").write_text("xxxxx")  # a 5-byte file
        Path(share, "b.log").write_text("")  # a 0-byte file
        Path(share, "Zoo.txt").write_text("y")  # mixed case — the key must lowercase
        rc_config.SHARE = share
        rows = rc_share.rows_html(share, "")
        self.assertIn('data-d="1"', rows)  # the dir carries is-dir=1
        self.assertIn('data-d="0"', rows)  # a file carries is-dir=0
        self.assertIn('data-n="a.txt"', rows)  # lowercased name key
        # "Zoo.txt" lowercased, not passed through
        self.assertIn('data-n="zoo.txt"', rows)
        self.assertIn('data-s="5"', rows)  # size in bytes for the client sort
        self.assertRegex(rows, r'data-t="\d+"')  # mtime key
        # dirs precede files (no-JS default)
        self.assertLess(rows.index("sub/"), rows.index("a.txt"))

    # --- ensure_trusted() ---

    def test_ensure_trusted_sets_flag_idempotently(self):
        Path(rc_config.CLAUDE_JSON).write_text(json.dumps({"projects": {}}))
        key = self._proj("proj")
        rc_sessions.ensure_trusted("proj")
        d = json.loads(Path(rc_config.CLAUDE_JSON).read_text())
        self.assertTrue(d["projects"][key]["hasTrustDialogAccepted"])
        rc_sessions.ensure_trusted("proj")  # idempotent — no error, flag preserved
        d = json.loads(Path(rc_config.CLAUDE_JSON).read_text())
        self.assertTrue(d["projects"][key]["hasTrustDialogAccepted"])

    def test_ensure_trusted_missing_file_is_noop(self):
        # CLAUDE_JSON absent -> FileNotFoundError guard, no raise
        rc_sessions.ensure_trusted("proj")

    def test_ensure_trusted_unreadable_logs_and_noops(self):
        events = []
        rc_config.log_event = lambda *a: events.append(a)
        # a dir where a file is expected -> IsADirectoryError
        os.makedirs(rc_config.CLAUDE_JSON)
        rc_sessions.ensure_trusted("proj")  # must not raise / 500 the launch
        # the real error is surfaced, not swallowed
        self.assertTrue(any(e[0] == "trust" for e in events))

    def test_ensure_trusted_corrupt_json_logs_and_noops(self):
        events = []
        rc_config.log_event = lambda *a: events.append(a)
        # partial write / race with claude
        Path(rc_config.CLAUDE_JSON).write_text("{not json")
        self._proj("proj")
        # JSONDecodeError (a ValueError, not OSError) -> no raise
        rc_sessions.ensure_trusted("proj")
        self.assertTrue(any(e[0] == "trust" for e in events))

    # --- page() templating / Server.handle_error ---

    def test_fill_prefix_keys_longest_first(self):
        # A key that is a strict prefix of another: insertion order would match __A__
        # inside __A__B__ first and mangle it to "shortB__" — the longest-first sort
        # is what makes the mapping order-independent.
        out = rc_templates.fill(
            "__A__B__ + __A__", {"__A__": "short", "__A__B__": "long"}
        )
        self.assertEqual(out, b"long + short")

    def test_page_placeholder_named_project_not_reinterpreted(self):
        # a project dir named like a placeholder — NAME_RE permits it
        self._proj("__LOGIN__")
        stubs = {
            rc_sessions: {"login_status": lambda: "ok", "session_states": lambda: {}},
            rc_tmux: {"running": lambda: set()},
            rc_git: {"git_states": lambda projs=None: {}},
            rc_desk: {"desk_projects": lambda: []},  # else this forks real pgrep/ps
        }
        for module, funcs in stubs.items():
            for name, stub in funcs.items():
                self.addCleanup(setattr, module, name, getattr(module, name))
                setattr(module, name, stub)
        out = rc_sessions.page().decode()
        self.assertIn('["__LOGIN__"]', out)  # the name survives as data in PROJECTS...
        # ...and the real __LOGIN__ slot filled, not clobbered
        # (the old chained-replace produced [""ok""] here — a broken script)
        self.assertIn('LOGIN="ok"', out)

    def test_server_handle_error_swallows_conn_noise_reraises_real(self):
        self.addCleanup(
            setattr,
            socketserver.BaseServer,
            "handle_error",
            socketserver.BaseServer.handle_error,
        )
        reached = []
        # exact arity: Server.handle_error must forward (request, client_address)
        socketserver.BaseServer.handle_error = lambda self, req, addr: reached.append(
            (req, addr)
        )
        srv = rc_launcher.Server(("127.0.0.1", 0), rc_launcher.Handler)
        self.addCleanup(srv.server_close)
        try:
            raise ConnectionResetError("client RST")
        except ConnectionResetError:
            srv.handle_error(None, ("127.0.0.1", 0))
        self.assertEqual(reached, [])  # a dropped connection is swallowed, no traceback
        try:
            raise ValueError("a real bug")
        except ValueError:
            srv.handle_error(None, ("127.0.0.1", 0))
        # a real error still reaches the traceback path
        self.assertEqual(reached, [(None, ("127.0.0.1", 0))])  # forwarded intact

    # --- log_event / projects edge / session_states skips ---

    def test_log_event_prints_audit_line(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _REAL_LOG("launch", "proj", "ok")
        line = buf.getvalue()
        self.assertIn("launch", line)
        self.assertIn("proj", line)
        self.assertIn("ok", line)

    def test_projects_missing_parent_is_empty(self):
        shutil.rmtree(rc_config.PARENT)
        self.assertEqual(rc_config.projects(), [])

    def test_session_states_skips_nonjson_bad_and_projectless(self):
        os.makedirs(rc_sessions.STATE_DIR)
        (rc_sessions.STATE_DIR / "note.txt").write_text("x")  # not .json -> skipped
        # decode error -> skipped
        (rc_sessions.STATE_DIR / "bad.json").write_text("{not json")
        # no project
        (rc_sessions.STATE_DIR / "np.json").write_text('{"state":"working","ts":9e18}')
        self.assertEqual(rc_sessions.session_states(), {})


if __name__ == "__main__":
    unittest.main()
