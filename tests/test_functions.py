"""Unit tests for the launcher's filesystem/logic functions — project discovery, the
create/trust flow, the session-state rank+TTL filter, breadcrumb/size rendering, and the
opt-in git snapshot. Every filesystem-touching global is redirected to a tmp dir; the tmux/
process-orchestration paths (launch/stop/spawn/takeover) are left to integration, not here."""

import io
import json
import os
import shutil
import socketserver
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import rc_launcher
import rc_state

from tests._harness import restore_globals

_REAL_LOG = rc_launcher.log_event  # captured before any test stubs it


class FunctionTest(unittest.TestCase):
    def setUp(self):
        restore_globals(self)
        self.tmp = tempfile.mkdtemp()
        rc_launcher.PARENT = os.path.join(self.tmp, "projects")
        os.makedirs(rc_launcher.PARENT)
        rc_launcher.STATE_DIR = Path(self.tmp, "state")
        rc_launcher.CLAUDE_JSON = os.path.join(self.tmp, "claude.json")
        rc_launcher.log_event = lambda *a: None
        rc_launcher._git_cache.clear()  # keyed by project name; names repeat across tmp PARENTs

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _proj(self, name: str) -> str:
        p = os.path.join(rc_launcher.PARENT, name)
        os.makedirs(p, exist_ok=True)
        return p

    # --- create() ---

    def test_create_makes_dir_git_and_stub(self):
        status, reason = rc_launcher.create("myproj")
        self.assertEqual(status, "created")
        self.assertIsNone(reason)
        root = os.path.join(rc_launcher.PARENT, "myproj")
        self.assertTrue(os.path.isdir(os.path.join(root, ".git")))
        self.assertEqual(Path(root, "CLAUDE.md").read_text(), "# myproj\n")

    def test_create_rejects_bad_name(self):
        self.assertEqual(rc_launcher.create("../evil")[0], "badname")
        self.assertEqual(rc_launcher.create("a b")[0], "badname")

    def test_create_refuses_existing(self):
        self._proj("dup")
        self.assertEqual(rc_launcher.create("dup")[0], "exists")

    # --- projects() ---

    def test_projects_lists_valid_dirs_sorted(self):
        self._proj("beta_1")
        self._proj("alpha")
        self._proj("with space")  # NAME_RE rejects the space
        open(os.path.join(rc_launcher.PARENT, "afile"), "w").close()  # a file, not a dir
        self.assertEqual(rc_launcher.projects(), ["alpha", "beta_1"])

    # --- _git_state() / git_states() ---

    def _git_repo(self, name: str, branch: str = "main") -> str:
        path = self._proj(name)

        def g(*a):
            subprocess.run([rc_launcher.GIT, "-C", path, *a], capture_output=True, check=True)

        g("init", "-q")
        Path(path, "f.txt").write_text("x")
        g("add", "f.txt")
        g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
        g("branch", "-M", branch)  # force a known branch so the parse can be asserted exactly
        return path

    def test_git_state_reports_exact_branch_and_dirty(self):
        path = self._git_repo("repo", branch="feat/x")
        st = rc_launcher._git_state("repo")
        self.assertEqual(st["b"], "feat/x")  # exact branch — a wrong prefix parse (e.g. branch.oid) fails
        self.assertFalse(st["d"])            # clean right after the commit
        Path(path, "f.txt").write_text("changed")
        self.assertTrue(rc_launcher._git_state("repo")["d"])  # a tracked edit reads dirty

    def test_git_state_untracked_counts_as_dirty(self):
        path = self._git_repo("repo")
        self.assertFalse(rc_launcher._git_state("repo")["d"])  # clean baseline
        Path(path, "new.txt").write_text("z")                   # untracked-only, nothing staged
        self.assertTrue(rc_launcher._git_state("repo")["d"])    # porcelain '?' line -> dirty, per the docstring

    def test_git_state_none_for_nonrepo(self):
        self._proj("plain")
        self.assertIsNone(rc_launcher._git_state("plain"))  # a bare dir isn't a work tree

    def test_git_state_missing_git_is_none_not_crash(self):
        self._git_repo("repo")
        self.addCleanup(setattr, rc_launcher, "GIT", rc_launcher.GIT)
        rc_launcher.GIT = "/nonexistent/git"  # simulate the missing binary under launchd PATH
        self.assertIsNone(rc_launcher._git_state("repo"))  # OSError swallowed -> None, page() won't 500

    def test_git_state_timeout_is_none_not_crash(self):
        self._git_repo("repo")

        def boom(*a, **k):  # a hung `git status` hitting the timeout
            raise subprocess.TimeoutExpired("git", rc_launcher.GIT_STATUS_TIMEOUT)

        rc_launcher.subprocess.run = boom  # restored by restore_globals()
        # TimeoutExpired is NOT an OSError; if the guard is narrowed to `except OSError:` this
        # raises through git_states() -> page() and 500s the launcher. It must return None.
        self.assertIsNone(rc_launcher._git_state("repo"))

    def test_git_states_maps_only_repos(self):
        self._git_repo("repo")
        self._proj("plain")
        got = rc_launcher.git_states()
        self.assertIn("repo", got)         # a repo -> present with branch/dirty
        self.assertNotIn("plain", got)     # non-repo -> dropped

    def test_git_states_serves_stale_from_cache(self):
        repo = self._git_repo("repo")
        self.assertFalse(rc_launcher.git_states()["repo"]["d"])  # clean, now cached
        Path(repo, "f.txt").write_text("changed")                # dirty it AFTER the cache fill
        # a fresh _git_state would report dirty; git_states must return the cached clean value,
        # which only holds if the cache READ path (not just the write) actually runs.
        self.assertFalse(rc_launcher.git_states()["repo"]["d"])

    # --- session_states() ---

    def test_session_states_ranks_and_expires(self):
        os.makedirs(rc_launcher.STATE_DIR)
        now = time.time()

        def write(sid, state, proj, ts):
            (rc_launcher.STATE_DIR / f"{sid}.json").write_text(
                json.dumps({"state": state, "project": proj, "ts": ts}))

        write("s1", "working", "alpha", now)   # fresh, working
        write("s2", "idle", "alpha", now)      # fresh, idle — lower rank than working
        write("s3", "waiting", "beta", now)    # fresh, waiting
        write("s4", "working", "gamma", now - rc_state.STATE_TTL - 60)  # stale -> ignored
        got = rc_launcher.session_states()
        self.assertEqual(got["alpha"], "working")  # working outranks idle for the same project
        self.assertEqual(got["beta"], "waiting")
        self.assertNotIn("gamma", got)             # stale entry dropped

    # --- human_size() / crumb_html() ---

    def test_human_size_units(self):
        self.assertEqual(rc_launcher.human_size(512), "512 B")
        self.assertEqual(rc_launcher.human_size(1536), "1.5 KB")
        self.assertEqual(rc_launcher.human_size(1024 ** 2), "1.0 MB")
        self.assertEqual(rc_launcher.human_size(1024 ** 5), "1.0 PB")  # the loop-fallthrough tail

    def test_crumb_html_builds_breadcrumb(self):
        html = rc_launcher.crumb_html("sub/deep")
        self.assertIn('href="/files">rc-share</a>', html)
        self.assertIn('href="/files/sub">sub</a>', html)
        self.assertIn('href="/files/sub/deep">deep</a>', html)

    def test_rows_html_carries_sort_keys_dirs_first(self):
        share = os.path.realpath(os.path.join(self.tmp, "share"))
        os.makedirs(os.path.join(share, "sub"))          # a directory
        Path(share, "a.txt").write_text("xxxxx")          # a 5-byte file
        Path(share, "b.log").write_text("")               # a 0-byte file
        rc_launcher.SHARE = share
        rows = rc_launcher.rows_html(share, "")
        self.assertIn('data-d="1"', rows)                 # the dir carries is-dir=1
        self.assertIn('data-d="0"', rows)                 # a file carries is-dir=0
        self.assertIn('data-n="a.txt"', rows)             # lowercased name key
        self.assertIn('data-s="5"', rows)                 # size in bytes for the client sort
        self.assertRegex(rows, r'data-t="\d+"')           # mtime key
        self.assertLess(rows.index("sub/"), rows.index("a.txt"))  # dirs precede files (no-JS default)

    # --- ensure_trusted() ---

    def test_ensure_trusted_sets_flag_idempotently(self):
        Path(rc_launcher.CLAUDE_JSON).write_text(json.dumps({"projects": {}}))
        key = self._proj("proj")
        rc_launcher.ensure_trusted("proj")
        d = json.loads(Path(rc_launcher.CLAUDE_JSON).read_text())
        self.assertTrue(d["projects"][key]["hasTrustDialogAccepted"])
        rc_launcher.ensure_trusted("proj")  # idempotent — no error, flag preserved
        d = json.loads(Path(rc_launcher.CLAUDE_JSON).read_text())
        self.assertTrue(d["projects"][key]["hasTrustDialogAccepted"])

    def test_ensure_trusted_missing_file_is_noop(self):
        rc_launcher.ensure_trusted("proj")  # CLAUDE_JSON absent -> FileNotFoundError guard, no raise

    def test_ensure_trusted_unreadable_logs_and_noops(self):
        events = []
        rc_launcher.log_event = lambda *a: events.append(a)
        os.makedirs(rc_launcher.CLAUDE_JSON)  # a dir where a file is expected -> IsADirectoryError
        rc_launcher.ensure_trusted("proj")    # must not raise / 500 the launch
        self.assertTrue(any(e[0] == "trust" for e in events))  # the real error is surfaced, not swallowed

    def test_ensure_trusted_corrupt_json_logs_and_noops(self):
        events = []
        rc_launcher.log_event = lambda *a: events.append(a)
        Path(rc_launcher.CLAUDE_JSON).write_text("{not json")  # partial write / race with claude
        self._proj("proj")
        rc_launcher.ensure_trusted("proj")  # JSONDecodeError (a ValueError, not OSError) -> no raise
        self.assertTrue(any(e[0] == "trust" for e in events))

    # --- page() templating / Server.handle_error ---

    def test_page_placeholder_named_project_not_reinterpreted(self):
        self._proj("__LOGIN__")  # a project dir named like a placeholder — NAME_RE permits it
        for fn in ("login_status", "running", "session_states", "git_states"):
            self.addCleanup(setattr, rc_launcher, fn, getattr(rc_launcher, fn))
        rc_launcher.login_status = lambda: "ok"
        rc_launcher.running = lambda: set()
        rc_launcher.session_states = lambda: {}
        rc_launcher.git_states = lambda projs=None: {}
        out = rc_launcher.page().decode()
        self.assertIn('["__LOGIN__"]', out)  # the name survives as data in PROJECTS...
        self.assertIn('LOGIN="ok"', out)     # ...and the real __LOGIN__ slot filled, not clobbered
        # (the old chained-replace produced [""ok""] here — a broken script)

    def test_server_handle_error_swallows_conn_noise_reraises_real(self):
        self.addCleanup(setattr, socketserver.BaseServer, "handle_error",
                        socketserver.BaseServer.handle_error)
        reached = []
        socketserver.BaseServer.handle_error = lambda self, req, addr: reached.append(True)
        srv = rc_launcher.Server(("127.0.0.1", 0), rc_launcher.Handler)
        self.addCleanup(srv.server_close)
        try:
            raise ConnectionResetError("client RST")
        except ConnectionResetError:
            srv.handle_error(None, ("127.0.0.1", 0))
        self.assertEqual(reached, [])       # a dropped connection is swallowed, no traceback
        try:
            raise ValueError("a real bug")
        except ValueError:
            srv.handle_error(None, ("127.0.0.1", 0))
        self.assertEqual(reached, [True])   # a real error still reaches the traceback path

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
        shutil.rmtree(rc_launcher.PARENT)
        self.assertEqual(rc_launcher.projects(), [])

    def test_session_states_skips_nonjson_bad_and_projectless(self):
        os.makedirs(rc_launcher.STATE_DIR)
        (rc_launcher.STATE_DIR / "note.txt").write_text("x")           # not .json -> skipped
        (rc_launcher.STATE_DIR / "bad.json").write_text("{not json")   # decode error -> skipped
        (rc_launcher.STATE_DIR / "np.json").write_text('{"state":"working","ts":9e18}')  # no project
        self.assertEqual(rc_launcher.session_states(), {})

    # --- snapshot() (opt-in) ---

    def test_snapshot_off_by_default(self):
        self.assertIsNone(rc_launcher.snapshot("repo"))  # RC_SNAPSHOT unset -> None

    def test_snapshot_creates_ref_when_enabled(self):
        path = self._proj("repo")
        ident = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        os.environ.update(ident)

        def git(*a):
            return subprocess.run(["git", "-C", path, *a], capture_output=True, text=True)

        git("init", "-q")
        Path(path, "f.txt").write_text("a")
        git("add", "f.txt")
        git("commit", "-q", "-m", "init")
        Path(path, "f.txt").write_text("b")  # a dirty tracked change for `stash create`
        os.environ["RC_SNAPSHOT"] = "1"
        try:
            ref = rc_launcher.snapshot("repo")
        finally:
            for k in (*ident, "RC_SNAPSHOT"):
                os.environ.pop(k, None)
        self.assertIsNotNone(ref)
        self.assertTrue(ref.startswith("refs/rc-snapshots/repo/"))
        self.assertEqual(git("rev-parse", "--verify", ref).returncode, 0)


if __name__ == "__main__":
    unittest.main()
