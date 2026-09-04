#!/usr/bin/env python3
"""Remote Control launcher — the HTTP surface.

Tap a project on your phone -> this starts a Claude Code Remote Control
session on the Mac, rooted in that project's directory (so its CLAUDE.md,
.claude/ settings and project MCP load exactly like the VS Code extension).
Each session is held in a detached tmux session so it survives the HTTP
request returning and any SSH/terminal closing.

This module is only the web tier: auth, routing, request framing, and the
/files byte-pushing. The work behind each route lives in rc_sessions (launch,
stop, create, what's live) and rc_share (what a path is allowed to reach);
settings come from rc_config. Refuses to start without the token file.
"""

import contextlib
import hmac
import json
import mimetypes
import os
import shutil
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import rc_config as cfg
import rc_sessions
import rc_share


def _is_files(path: str) -> bool:
    """The /files subtree, matched the same way by every verb — '/files' itself or
    anything under it, never a '/filesomething' sibling."""
    return path == "/files" or path.startswith("/files/")


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 keep-alive so a chunked upload reuses ONE connection (a single TCP
    # slow-start) instead of a fresh handshake + slow-start per chunk — the difference
    # between ~line-rate and a per-chunk ramp. Every response sets Content-Length, which
    # is what makes persistent connections framable. timeout reaps idle kept connections.
    protocol_version = "HTTP/1.1"
    timeout = 60

    def _send(
        self,
        code: int,
        body: bytes,
        ctype: str = "text/html; charset=utf-8",
        set_cookie: bool = False,
        close: bool = False,
    ):
        # non-2xx used to be invisible (log_message is silenced) — trace it. Path only,
        # never the query: the app's uploads carry ?token=, and a failed request would
        # otherwise write the live token into the world-readable log.
        if code >= 400:
            cfg.log_event(
                "http", f"{self.command} {urlparse(self.path).path}", str(code)
            )
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header(
                "Set-Cookie",
                f"rc_token={cfg.TOKEN}; HttpOnly; SameSite=Strict; Path=/; "
                "Max-Age=31536000",
            )
        # a bail-out that never read the request body must end the connection, or that
        # unread body desyncs the next request on the socket
        if close:
            self.close_connection = True
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # advertise the close (set by close=, _guard_body, or a client Connection: close)
        # so the client won't try to reuse a socket we're about to drop.
        if self.close_connection:
            self.send_header("Connection", "close")
        super().end_headers()

    def _json(self, payload: dict):
        self._send(200, json.dumps(payload).encode(), "application/json")

    def _json_error(self, code: int, msg: str, close: bool = False, **extra):
        """A refusal the app can parse. Compact separators keep the wire bytes identical
        to the hand-written literals these eight sites used to carry."""
        body = json.dumps({"error": msg} | extra, separators=(",", ":")).encode()
        self._send(code, body, "application/json", close=close)

    def _authed(self, q: dict) -> bool:
        """Token via ?token= (first contact / bookmark) or the rc_token cookie set
        on that first load, so the token stays out of later request URLs and logs."""
        if not cfg.TOKEN:
            return False
        if hmac.compare_digest(q.get("token", [""])[0], cfg.TOKEN):
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return "rc_token" in cookie and hmac.compare_digest(
            cookie["rc_token"].value, cfg.TOKEN
        )

    def _guard_body(self) -> None:
        """GET/HEAD/DELETE never read a request body; under keep-alive an unread body would
        desync the next request on the socket, so close the connection if one was sent."""
        if self.headers.get("Content-Length") or self.headers.get("Transfer-Encoding"):
            self.close_connection = True

    def do_GET(self):
        self._guard_body()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._send(403, b"forbidden")
        match u.path:
            case "/":
                return self._send(200, rc_sessions.page(), set_cookie=True)
            case "/status":
                return self._json(rc_sessions.status_payload())
            case "/create":
                return self._create(q.get("proj", [""])[0])
            case "/launch" | "/stop":
                return self._session_verb(u.path, q)
            case path if _is_files(path):
                return self._files(path)
            case _:
                self._send(404, b"not found")

    def _create(self, proj: str):
        """Make the project, then launch it — one tap on the phone's "+ new" row."""
        status, reason = rc_sessions.create(proj)
        cfg.log_event("create", proj, status)
        payload = {"status": status, "proj": proj}
        if reason:
            payload["reason"] = reason
        if status == "created":
            lstatus, lreason = rc_sessions.launch(proj)
            cfg.log_event("launch", proj, lstatus)
            payload["launch"] = lstatus
            if lreason:
                payload["launch_reason"] = lreason
        return self._json(payload)

    def _session_verb(self, path: str, q: dict):
        """/launch and /stop. desk=1 means something on /stop only — it is the ✕ on a
        desk-badged row, which closes the desktop claude rather than a tmux session."""
        proj = q.get("proj", [""])[0]
        if proj not in cfg.projects():
            return self._json_error(404, "unknown project")
        if path == "/stop":
            desk = q.get("desk", [""])[0] == "1"
            status, reason = (rc_sessions.desk_stop if desk else rc_sessions.stop)(proj)
        else:
            status, reason = rc_sessions.launch(proj)
        cfg.log_event(path[1:], proj, status)
        if q.get("json", [""])[0] != "1":
            return self._send(200, rc_sessions.page())
        payload = {"status": status, "proj": proj}
        if reason:
            payload["reason"] = reason
        return self._json(payload)

    def do_PUT(self):
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):
            # PUT carries a body we won't read -> close
            return self._send(403, b"forbidden", close=True)
        if _is_files(u.path):
            return self._upload(u.path)
        self._send(404, b"not found", close=True)

    def do_DELETE(self):
        self._guard_body()
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):
            return self._send(403, b"forbidden")
        if _is_files(u.path):
            return self._delete(u.path)
        self._send(404, b"not found")

    def do_HEAD(self):
        """Report how many bytes of a resumable upload are already on disk, so a client
        can resume from there: X-Rc-Have = size of the target's .rcpart (0 if none)."""
        self._guard_body()
        u = urlparse(self.path)
        if not self._authed(parse_qs(u.query)):
            return self._send(403, b"forbidden")
        have = 0
        if _is_files(u.path):
            rel = u.path.removeprefix("/files")
            _, tmp = rc_share.part_paths(rel, self.headers.get("X-Rc-Id", ""))
            if tmp:
                have = rc_share.have(tmp)
        self.send_response(200)
        self.send_header("X-Rc-Have", str(have))
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _files(self, path: str):
        """Browse/download under SHARE, behind the same token gate.
        share_target() resolves '..' and symlink escapes away, so this can only
        reach files inside SHARE (never ~/projects or $HOME)."""
        rel = path.removeprefix("/files")
        target = rc_share.share_target(rel)
        if target is None:
            return self._send(404, b"not found")
        if os.path.isfile(target):
            return self._stream_file(target)
        if os.path.isdir(target) or target == cfg.SHARE:
            # set the cookie here too: loading /files directly (not via /) must still
            # authenticate the cookie-based upload/HEAD/download/delete requests it fires.
            return self._send(200, rc_share.share_page(target, rel), set_cookie=True)
        return self._send(404, b"not found")

    def _stream_file(self, target: str):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(target)[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(os.path.getsize(target)))
        self.send_header(
            "Content-Disposition",
            f"inline; filename*=UTF-8''{quote(os.path.basename(target))}",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with (
            contextlib.suppress(BrokenPipeError, ConnectionResetError),
            open(target, "rb") as f,
        ):
            shutil.copyfileobj(f, self.wfile, 65536)

    @staticmethod
    def _uint(raw: str | None, default: int) -> int | None:
        """An optional non-negative-int header: the default when absent, None when
        present-but-invalid (negative, non-numeric, empty) so the caller can 400."""
        if raw is None:
            return default
        return int(raw) if raw.isdigit() else None

    def _upload(self, path: str):
        """Write or RESUME an upload into SHARE. Bytes stream into a .rcpart temp at
        X-Rc-Offset; the partial is KEPT across interruptions so a dropped upload
        resumes (via HEAD -> X-Rc-Have) instead of restarting. When the temp reaches
        X-Rc-Total it's atomically renamed to the final name. Confined like every path."""
        rel = path.removeprefix("/files")
        target, tmp = rc_share.part_paths(rel, self.headers.get("X-Rc-Id", ""))
        if target is None:
            return self._json_error(403, "bad target", close=True)
        folder = os.path.dirname(target)
        if not rc_share.within_share(folder) or not os.path.isdir(folder):
            return self._json_error(404, "no such folder", close=True)
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            return self._json_error(411, "length required", close=True)
        length = int(length)
        offset = self._uint(self.headers.get("X-Rc-Offset"), 0)
        if offset is None:
            return self._json_error(400, "bad offset", close=True)
        total = self._uint(self.headers.get("X-Rc-Total"), offset + length)
        if total is None or total <= 0 or total < offset:
            return self._json_error(400, "bad total", close=True)
        have = rc_share.have(tmp)
        if offset > have:  # gap: client is ahead of us — tell it what we actually have
            return self._json_error(409, "gap", close=True, have=have)
        remaining = self._drain_body(tmp, offset, length, target)
        # body not fully drained (drop or write error) — end the connection so its
        # leftover bytes can't be read as a next request
        if remaining:
            self.close_connection = True
        now = rc_share.have(tmp)
        if now >= total:
            os.replace(tmp, target)
            cfg.log_event("upload", os.path.relpath(target, cfg.SHARE), "ok")
            return self._json(
                {"ok": True, "done": True, "name": os.path.basename(target)}
            )
        with contextlib.suppress(OSError):
            self._json({"ok": True, "done": False, "have": now})

    def _drain_body(self, tmp: str, offset: int, length: int, target: str) -> int:
        """Stream length bytes of the request body into tmp at offset; returns how many
        bytes were NOT written. Single-writer assumption: the sequential (await-per-file)
        browser/app clients never run two PUTs to the same target+id concurrently, so the
        .rcpart needs no lock. An overlap would corrupt only the partial (reclaimed by the
        sweep) — os.replace keeps the finalized file atomic regardless."""
        remaining = length
        try:
            with open(tmp, "r+b" if rc_share.have(tmp) else "wb") as f:
                f.seek(offset)
                f.truncate(offset)
                while remaining > 0 and (
                    chunk := self.rfile.read(min(65536, remaining))
                ):
                    f.write(chunk)
                    remaining -= len(chunk)
        except (ConnectionError, TimeoutError):
            pass  # link dropped/stalled mid-body: keep the partial for the next resume
        except (
            OSError
        ) as e:  # a real disk error (ENOSPC/EACCES): log it, keep the partial
            cfg.log_event("upload", os.path.basename(target), f"err {e}")
        return remaining

    def _delete(self, path: str):
        """Delete a file inside SHARE. Same confinement as read/write; only regular
        files (never the root, never a directory)."""
        target = rc_share.share_target(path.removeprefix("/files"))
        if target is None or target == cfg.SHARE or not os.path.isfile(target):
            return self._json_error(403, "bad target")
        os.unlink(target)
        cfg.log_event("delete", os.path.relpath(target, cfg.SHARE), "ok")
        self._json({"ok": True})

    def log_message(self, format: str, *args: object) -> None:
        pass  # access lines are logged by _send (>=400 only), not by http.server


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # a client RST / dropped connection mid-request is normal on a lossy link (Starlink):
        # keep it out of the error log (which never rotates). Only real errors get a traceback.
        if not isinstance(sys.exc_info()[1], (ConnectionError, BrokenPipeError)):
            super().handle_error(request, client_address)


if __name__ == "__main__":
    if not cfg.TOKEN:
        raise SystemExit(
            "no launcher token: run install.sh (writes ~/.config/rc-launcher/token)"
        )
    print(f"rc-launcher on {cfg.BIND}:{cfg.PORT} parent={cfg.PARENT} spawn={cfg.SPAWN}")
    threading.Thread(target=rc_share.sweep_loop, daemon=True).start()
    Server((cfg.BIND, cfg.PORT), Handler).serve_forever()
