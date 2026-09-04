"""The /files write path — token/cookie auth, resumable finalize, the 409 gap, malformed
header rejection — and the .rcpart TTL sweep. Drives the real Handler over a loopback
ThreadingHTTPServer, the same contract a browser/app client speaks. These guard the two
paths that broke this cycle (the cookie-auth-on-write 403 and the resumable finalize)."""

import http.client
import json
import os
import shutil
import socket
import struct
import tempfile
import time
import unittest
from pathlib import Path

import rc_launcher

from tests._harness import (
    desk,
    proc,
    respond,
    restore_globals,
    serve,
    share_dir,
    spawn_ok,
)

TOKEN = "test-token-0123456789"


class SweepTest(unittest.TestCase):
    def setUp(self):
        self.share = rc_launcher.SHARE = share_dir(self)

    def _aged(self, name: str) -> str:
        p = os.path.join(self.share, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").close()
        old = time.time() - rc_launcher.RCPART_TTL - 60
        os.utime(p, (old, old))
        return p

    def test_removes_only_expired_rcparts(self):
        old = self._aged("old.rcpart")
        nested = self._aged("sub/deep.rcpart")  # proves the os.walk recursion
        keep_txt = self._aged("keep.txt")  # aged, but not a .rcpart
        fresh = os.path.join(self.share, "fresh.rcpart")
        open(fresh, "w").close()  # current mtime — an in-progress upload
        self.assertEqual(rc_launcher.sweep_rcparts(), 2)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(nested))
        self.assertTrue(os.path.exists(keep_txt))
        self.assertTrue(os.path.exists(fresh))


class UploadServerTest(unittest.TestCase):
    def setUp(self):
        restore_globals(self)
        self.share = rc_launcher.SHARE = share_dir(self)
        rc_launcher.TOKEN = TOKEN
        rc_launcher.log_event = lambda *a: (
            None
        )  # don't append test traffic to the real log
        self.port = serve(self)

    def req(self, method, path, body=None, headers=None, cookie=True):
        h = dict(headers or {})
        if cookie:
            h.setdefault("Cookie", f"rc_token={TOKEN}")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, hdrs, data

    def test_files_get_sets_auth_cookie(self):
        status, hdrs, _ = self.req("GET", f"/files?token={TOKEN}", cookie=False)
        self.assertEqual(status, 200)
        cookie = hdrs.get("set-cookie", "")
        self.assertIn("rc_token=", cookie)
        self.assertIn("HttpOnly", cookie)  # not readable from page JS
        self.assertIn(
            "SameSite=Strict", cookie
        )  # CSRF defense for the cookie-authed writes
        self.assertIn("Path=/", cookie)

    def test_valid_token_beats_stale_cookie(self):
        # a correct ?token= must authenticate even carrying a wrong cookie (the ac5b5d0 shape)
        status, _, _ = self.req(
            "GET",
            f"/files?token={TOKEN}",
            cookie=False,
            headers={"Cookie": "rc_token=stalevalue"},
        )
        self.assertEqual(status, 200)

    def test_cookie_authorizes_write_and_no_auth_is_403(self):
        # cookie-only PUT (no ?token=) authenticates — the regression that shipped as a 403
        status, _, _ = self.req(
            "PUT",
            "/files/c.bin",
            body=b"hi",
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "2"},
        )
        self.assertEqual(status, 200)
        status, _, _ = self.req(
            "PUT",
            "/files/n.bin",
            body=b"x",
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "1"},
            cookie=False,
        )
        self.assertEqual(status, 403)

    def test_full_upload_finalizes_without_leftover(self):
        data = bytes(i % 256 for i in range(1000))
        status, _, body = self.req(
            "PUT",
            "/files/f.bin",
            body=data,
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "1000"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["done"])
        self.assertEqual(Path(self.share, "f.bin").read_bytes(), data)
        self.assertFalse(os.path.exists(os.path.join(self.share, "f.bin.rcpart")))

    def test_resume_from_partial(self):
        data = bytes(i % 256 for i in range(1000))
        status, _, body = self.req(
            "PUT",
            "/files/r.bin",
            body=data[:400],
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "1000"},
        )
        self.assertEqual(status, 200)
        first = json.loads(body)
        self.assertFalse(first["done"])
        self.assertEqual(first["have"], 400)
        _, hdrs, _ = self.req("HEAD", "/files/r.bin")
        self.assertEqual(hdrs.get("x-rc-have"), "400")
        status, _, body = self.req(
            "PUT",
            "/files/r.bin",
            body=data[400:],
            headers={"X-Rc-Offset": "400", "X-Rc-Total": "1000"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["done"])
        self.assertEqual(Path(self.share, "r.bin").read_bytes(), data)

    def test_resume_identity_isolates_stale_partial(self):
        # a stale partial for x.bin under file-id A
        self.req(
            "PUT",
            "/files/x.bin",
            body=b"A" * 100,
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "1000", "X-Rc-Id": "idA"},
        )
        # a HEAD under a DIFFERENT id must see have=0 — it won't resume onto A's bytes
        _, hdrs, _ = self.req("HEAD", "/files/x.bin", headers={"X-Rc-Id": "idB"})
        self.assertEqual(hdrs.get("x-rc-have"), "0")
        # the SAME id still resumes
        _, hdrs, _ = self.req("HEAD", "/files/x.bin", headers={"X-Rc-Id": "idA"})
        self.assertEqual(hdrs.get("x-rc-have"), "100")
        # a realistic client resumes from what HEAD reports. With id-keying idB's HEAD is 0,
        # so it uploads pure B; if the keying were removed, HEAD would report 100 (idA's
        # partial) and this would resume ONTO it (A[0:100]+B[100:]) — which this catches.
        _, hb, _ = self.req("HEAD", "/files/x.bin", headers={"X-Rc-Id": "idB"})
        have = int(hb["x-rc-have"])
        full = b"B" * 500
        _, _, body = self.req(
            "PUT",
            "/files/x.bin",
            body=full[have:],
            headers={"X-Rc-Offset": str(have), "X-Rc-Total": "500", "X-Rc-Id": "idB"},
        )
        self.assertTrue(json.loads(body)["done"])
        self.assertEqual(Path(self.share, "x.bin").read_bytes(), full)

    def test_gap_offset_returns_409(self):
        self.req(
            "PUT",
            "/files/g.bin",
            body=b"x" * 100,
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "500"},
        )
        status, _, body = self.req(
            "PUT",
            "/files/g.bin",
            body=b"y" * 10,
            headers={"X-Rc-Offset": "200", "X-Rc-Total": "500"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["have"], 100)

    def test_malformed_headers_400_and_server_survives(self):
        for off in (
            "abc",
            "-5",
        ):  # non-numeric used to crash the thread; negative was swallowed
            status, _, _ = self.req(
                "PUT",
                "/files/b.bin",
                body=b"x",
                headers={"X-Rc-Offset": off, "X-Rc-Total": "10"},
            )
            self.assertEqual(status, 400, off)
        status, _, _ = self.req(
            "PUT",
            "/files/b.bin",
            body=b"x",
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "0"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(os.path.exists(os.path.join(self.share, "b.bin.rcpart")))
        status, _, _ = self.req("GET", f"/files?token={TOKEN}", cookie=False)
        self.assertEqual(status, 200)  # the handler thread survived the bad requests

    def test_delete_removes_file_and_refuses_root_and_dir(self):
        self.req(
            "PUT",
            "/files/d.bin",
            body=b"x",
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "1"},
        )
        self.assertTrue(os.path.exists(os.path.join(self.share, "d.bin")))
        self.assertEqual(self.req("DELETE", "/files/d.bin")[0], 200)
        self.assertFalse(os.path.exists(os.path.join(self.share, "d.bin")))
        os.makedirs(os.path.join(self.share, "sub"))
        self.assertEqual(self.req("DELETE", "/files")[0], 403)  # the share root
        self.assertEqual(self.req("DELETE", "/files/sub")[0], 403)  # a directory
        self.assertEqual(
            self.req("DELETE", "/files/..%2f..%2fx")[0], 403
        )  # a traversal escape

    def test_body_bearing_get_closes_connection(self):
        # a GET carrying a body: the handler won't read it, so under keep-alive the leftover
        # would desync the next request — the server must close instead of reusing the socket.
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(
            f"GET /files?token={TOKEN} HTTP/1.1\r\nHost: x\r\n"
            f"Content-Length: 5\r\n\r\nHELLO".encode()
        )
        resp = s.recv(65536).decode("latin1")
        s.close()
        self.assertIn(" 200 ", resp.splitlines()[0])  # the GET itself succeeded
        self.assertIn(
            "connection: close", resp.lower()
        )  # and the server closed the socket

    def test_rejected_request_is_logged(self):
        logged: list = []
        rc_launcher.log_event = lambda *a: logged.append(
            a
        )  # capture instead of silence
        self.req(
            "PUT",
            "/files/z",
            body=b"z",
            headers={"X-Rc-Offset": "bad", "X-Rc-Total": "1"},
        )
        self.assertTrue(
            any(a[0] == "http" for a in logged)
        )  # the 400 left an audit line

    def test_rejected_request_log_omits_query_token(self):
        # The app's uploads carry ?token= in the query, and the launcher log is a
        # world-readable /tmp file: the >=400 audit line must record the PATH only.
        # Reverting the redaction to raw self.path re-opens the leak the token
        # remediation closed — this pin is what makes that revert fail.
        logged: list = []
        rc_launcher.log_event = lambda *a: logged.append(a)
        self.req(
            "PUT",
            f"/files/z?token={TOKEN}",
            body=b"z",
            headers={"X-Rc-Offset": "bad", "X-Rc-Total": "1"},
        )
        line = " ".join(map(str, next(a for a in logged if a[0] == "http")))
        self.assertIn("/files/z", line)  # the path is still traceable
        self.assertNotIn(TOKEN, line)  # the credential never reaches the log

    def test_download_streams_file(self):
        data = b"hello world " * 50
        Path(self.share, "dl.bin").write_bytes(data)
        status, _, body = self.req("GET", f"/files/dl.bin?token={TOKEN}", cookie=False)
        self.assertEqual(status, 200)
        self.assertEqual(body, data)

    def test_upload_write_error_is_logged_not_finalized(self):
        logged: list = []
        rc_launcher.log_event = lambda *a: logged.append(a)
        os.chmod(
            self.share, 0o500
        )  # read-only dir -> the .rcpart open() raises OSError
        try:
            status, _, body = self.req(
                "PUT",
                "/files/e.bin",
                body=b"data",
                headers={"X-Rc-Offset": "0", "X-Rc-Total": "4"},
            )
        finally:
            os.chmod(self.share, 0o700)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["done"])
        self.assertTrue(any(a[0] == "upload" and "err" in str(a[2]) for a in logged))

    def test_put_unknown_path_404(self):
        status, _, _ = self.req(
            "PUT", "/nope", body=b"x", headers={"X-Rc-Offset": "0", "X-Rc-Total": "1"}
        )
        self.assertEqual(status, 404)

    def test_upload_without_total_header_finalizes(self):
        status, _, body = self.req(
            "PUT", "/files/nt.bin", body=b"12345", headers={"X-Rc-Offset": "0"}
        )  # no X-Rc-Total -> defaults
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["done"])
        self.assertEqual(Path(self.share, "nt.bin").read_bytes(), b"12345")

    def test_upload_to_directory_is_bad_target(self):
        os.makedirs(os.path.join(self.share, "adir"))
        status, _, _ = self.req(
            "PUT",
            "/files/adir",
            body=b"x",
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "1"},
        )
        self.assertEqual(status, 403)

    def test_upload_to_missing_folder_404(self):
        status, _, _ = self.req(
            "PUT",
            "/files/nodir/f.bin",
            body=b"x",
            headers={"X-Rc-Offset": "0", "X-Rc-Total": "1"},
        )
        self.assertEqual(status, 404)

    def test_files_traversal_get_404(self):
        status, _, _ = self.req(
            "GET", f"/files/..%2f..%2fetc?token={TOKEN}", cookie=False
        )
        self.assertEqual(status, 404)

    def test_delete_non_files_path_404(self):
        self.assertEqual(self.req("DELETE", "/nope")[0], 404)

    # --- auth: the secret comparison and per-verb denial, not just presence/absence ---

    def test_wrong_token_and_cookie_rejected(self):
        self.assertEqual(
            self.req("GET", "/files?token=wrongvalue", cookie=False)[0], 403
        )
        self.assertEqual(
            self.req(
                "GET", "/files", cookie=False, headers={"Cookie": "rc_token=wrongvalue"}
            )[0],
            403,
        )

    def test_no_auth_denied_on_every_verb(self):
        for verb in ("GET", "PUT", "DELETE", "HEAD"):
            hdr = {"X-Rc-Offset": "0", "X-Rc-Total": "1"} if verb == "PUT" else None
            self.assertEqual(
                self.req(verb, "/files/x", cookie=False, headers=hdr)[0], 403, verb
            )

    def test_fail_closed_when_no_token_configured(self):
        rc_launcher.TOKEN = (
            ""  # misconfigured server: an empty token must deny, not match empty creds
        )
        try:
            # empty ?token= and an empty rc_token cookie both reach compare_digest("","") — that
            # returns True, so only the `if not TOKEN: return False` guard makes these 403. Sending
            # a *non-empty* token here (as before) 403s on the mismatch regardless, proving nothing.
            self.assertEqual(self.req("GET", "/files/x?token=", cookie=False)[0], 403)
            self.assertEqual(
                self.req(
                    "GET", "/files/x", cookie=False, headers={"Cookie": "rc_token="}
                )[0],
                403,
            )
            # and a real-looking token is still rejected when the server holds none
            self.assertEqual(
                self.req("GET", f"/files/x?token={TOKEN}", cookie=False)[0], 403
            )
        finally:
            rc_launcher.TOKEN = TOKEN

    def test_chunked_body_bearing_get_closes(self):
        # a chunked (no Content-Length) body on GET must also close, or it desyncs keep-alive
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(
            f"GET /files?token={TOKEN} HTTP/1.1\r\nHost: x\r\n"
            f"Transfer-Encoding: chunked\r\n\r\n5\r\nHELLO\r\n0\r\n\r\n".encode()
        )
        resp = s.recv(65536).decode("latin1")
        s.close()
        self.assertIn("connection: close", resp.lower())

    def test_put_without_content_length_is_411_and_closes(self):
        # no length, no chunked: the server cannot size the body, so it must refuse (411)
        # and close rather than leave an unread body to desync the next request
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(
            f"PUT /files/nolen.bin HTTP/1.1\r\nHost: x\r\nCookie: rc_token={TOKEN}\r\n"
            f"X-Rc-Offset: 0\r\nX-Rc-Total: 10\r\n\r\n".encode()
        )
        chunks = []  # read to EOF: the server closes, and the body can trail the headers
        while chunk := s.recv(65536):
            chunks.append(chunk)
        resp = b"".join(chunks).decode("latin1")
        s.close()
        self.assertIn(" 411 ", resp.splitlines()[0])
        self.assertIn("connection: close", resp.lower())
        self.assertIn('"length required"', resp)

    def test_dropped_connection_keeps_partial(self):
        # a real mid-body RST must hit the ConnectionError branch that KEEPS the .rcpart —
        # mutate that branch to os.unlink and this assertion fails (the headline resume feature).
        pattern = (
            bytes(range(256)) * 300
        )  # 76800 distinctive bytes: catches stale-length/corruption
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(
            f"PUT /files/drop.bin HTTP/1.1\r\nHost: x\r\nCookie: rc_token={TOKEN}\r\n"
            f"X-Rc-Offset: 0\r\nX-Rc-Total: 200000\r\nContent-Length: 200000\r\n\r\n".encode()
        )
        s.sendall(pattern)  # more than one 64KB read, well short of total
        time.sleep(0.5)  # let the server read + write the first 64KB chunk
        s.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )  # RST on close
        s.close()
        time.sleep(0.5)
        _, hdrs, _ = self.req("HEAD", "/files/drop.bin")
        have = int(hdrs["x-rc-have"])
        self.assertGreaterEqual(
            have, 65536
        )  # partial survived the drop (ConnectionError branch)
        kept = Path(self.share, "drop.bin.rcpart").read_bytes()
        self.assertEqual(
            kept, pattern[:have]
        )  # exact prefix of what was sent, not garbage/stale


class RowsHtmlTest(unittest.TestCase):
    def setUp(self):
        restore_globals(self)
        self.share = rc_launcher.SHARE = share_dir(self)

    def test_hides_rcpart_and_escaping_symlink(self):
        open(os.path.join(self.share, "real.txt"), "w").close()
        open(os.path.join(self.share, "partial.rcpart"), "w").close()
        os.symlink(
            tempfile.mkdtemp(), os.path.join(self.share, "escape")
        )  # -> outside SHARE
        rows = rc_launcher.rows_html(self.share, "")
        self.assertIn("real.txt", rows)
        self.assertNotIn("partial", rows)  # .rcpart hidden
        self.assertNotIn("escape", rows)  # symlink out of SHARE not listed

    def test_script_context_values_are_escaped(self):
        self.assertNotIn("<", rc_launcher.js("</script>"))  # the escape at the source
        out = rc_launcher.share_page(self.share, "a</script>b")
        self.assertNotIn(b"</script>b", out)  # the rel didn't break out of the <script>
        self.assertNotIn(b"__REL__", out)  # no unfilled placeholder left

    def test_rows_html_dirs_first_empty_and_unreadable(self):
        os.makedirs(os.path.join(self.share, "adir"))
        open(os.path.join(self.share, "afile.txt"), "w").close()
        rows = rc_launcher.rows_html(self.share, "")
        self.assertLess(
            rows.index("adir"), rows.index("afile.txt")
        )  # dirs before files
        self.assertIn(
            "empty", rc_launcher.rows_html(os.path.join(self.share, "adir"), "/adir")
        )
        self.assertIn(
            "empty", rc_launcher.rows_html(os.path.join(self.share, "no"), "/no")
        )  # OSError


class RouteTest(unittest.TestCase):
    """The do_GET routes (/status, /create, /launch, /stop, root) over the real server with
    subprocess/os.kill/sleep mocked, so no tmux/claude is actually spawned."""

    def setUp(self):
        restore_globals(self)
        self.share = rc_launcher.SHARE = share_dir(self)
        self.aux = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.aux, True)
        rc_launcher.TOKEN = TOKEN
        rc_launcher.PARENT = os.path.join(self.aux, "projects")
        os.makedirs(rc_launcher.PARENT)
        rc_launcher.STATE_DIR = Path(self.aux, "state")
        rc_launcher.CLAUDE_JSON = os.path.join(self.aux, "claude.json")
        Path(rc_launcher.CLAUDE_JSON).write_text("{}")
        rc_launcher.log_event = lambda *a: None
        self.responses: dict = {}
        self.desk: dict = {}
        rc_launcher.subprocess.run = lambda cmd, **kw: self._resp(cmd)
        rc_launcher.os.kill = lambda *a: None
        rc_launcher.time.sleep = lambda *a: None
        rc_launcher.RESUME = (
            "off"  # fresh launches (no takeover) keep route tests simple
        )
        rc_launcher._login_status.cache_clear()
        self.port = serve(self)

    def _resp(self, cmd):
        return respond(cmd, self.desk, self.responses)

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers={"Cookie": f"rc_token={TOKEN}"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

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
        self.assertTrue(os.path.isdir(os.path.join(rc_launcher.PARENT, "newp")))

    def test_create_route_bad_name_reports_reason(self):
        status, body = self.get("/create?proj=bad%20name")  # space -> badname + reason
        d = json.loads(body)
        self.assertEqual(d["status"], "badname")
        self.assertIn("reason", d)

    def test_launch_route_unknown_project_404(self):
        self.assertEqual(self.get("/launch?proj=ghost")[0], 404)

    def test_launch_and_stop_routes(self):
        os.makedirs(os.path.join(rc_launcher.PARENT, "realp"))
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
        os.makedirs(os.path.join(rc_launcher.PARENT, "deskp"))
        root = os.path.join(rc_launcher.PARENT, "deskp")
        killed, calls = [], []

        def kill(pid, sig):
            killed.append((pid, sig))
            if sig == 0:
                raise ProcessLookupError  # SIGTERM worked; takeover needn't escalate

        rc_launcher.os.kill = kill
        rc_launcher.subprocess.run = lambda cmd, **kw: (
            calls.append(cmd),
            self._resp(cmd),
        )[1]
        self.desk = {"321": desk(root)}
        status, body = self.get("/stop?proj=deskp&desk=1&json=1")
        self.assertEqual(json.loads(body)["status"], "stopped")
        self.assertIn((321, rc_launcher.signal.SIGTERM), killed)  # graceful desk close
        self.assertNotIn((321, rc_launcher.signal.SIGKILL), killed)
        joined = [" ".join(map(str, c)) for c in calls]
        self.assertFalse(any("send-keys" in c or "kill-session" in c for c in joined))

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
        os.makedirs(os.path.join(rc_launcher.PARENT, "p"))
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
        os.makedirs(os.path.join(rc_launcher.PARENT, "p"))
        self.responses = spawn_ok()
        status, body = self.get("/launch?proj=p")
        self.assertEqual(status, 200)
        self.assertTrue(body.lstrip().lower().startswith(b"<!doctype html"), body[:40])


if __name__ == "__main__":
    unittest.main()
