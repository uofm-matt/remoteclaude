"""Starting, describing and closing the sessions a phone tap drives.

The cluster the launcher's HTTP layer calls into: pre-accept the workspace trust dialog,
decide resume-vs-fresh, spawn into tmux and prove the session came up, and report what is
live. Everything that talks to a process goes through the leaf modules (rc_tmux, rc_desk,
rc_git), so this file is the policy and they are the mechanics.

`launch()` is deliberately one long function: its steps share the decisions they make
(resuming, env_opts, the fresh-relaunch fallback) and splitting them would only hide the
order they must happen in.
"""

import contextlib
import html
import json
import os
import re
import subprocess
import time
from pathlib import Path
from types import MappingProxyType

import rc_config as cfg
import rc_desk
import rc_git
import rc_tmux
from rc_claude import CLAUDE, auth_status
from rc_page import PAGE
from rc_state import RANK, STATE_DIR, valid_states
from rc_templates import fill, js


@cfg.ttl_cached(lambda: cfg.LOGIN_TTL)
def login_status() -> str:
    """'ok' | 'loggedout' | 'unknown'. `claude auth status` spawns a process and the phone
    polls /status every few seconds, so the answer is cached for cfg.LOGIN_TTL."""
    return auth_status()[0]  # shared probe; the badge needs only the state


def session_states() -> dict[str, str]:
    """{project: most-urgent turn state} from the files rc_state_hook.py writes,
    so the UI can show working/waiting, not just live. Stale files are ignored."""
    out: dict[str, str] = {}
    for d in valid_states(STATE_DIR):
        # st is already a RANK key (valid_states)
        proj, st = d.get("project") or "", d["state"]
        if proj and RANK[st] > RANK.get(out.get(proj, ""), 0):
            out[proj] = st
    return out


def status_payload() -> dict:
    """The live view the phone polls: which projects have an rc session, whether the
    login still works, each session's turn state, and the desk sessions the tmux dots
    can't see. /status and page() both read it here so they can't drift — git state is
    NOT in it, deliberately: it forks a git per repo and belongs to the page load."""
    return {
        "running": sorted(rc_tmux.running()),
        "login": login_status(),
        "states": session_states(),
        "desk": rc_desk.desk_projects(),
    }


def page() -> bytes:
    """status_payload() rendered as the launcher page, plus the two things only a page
    load pays for: the project list (scanned once and shared with git_states) and the
    per-repo branch/dirty badges."""
    projs = cfg.projects()
    live = status_payload()
    return fill(
        PAGE,
        {
            "__PROJECTS__": js(projs),
            "__RUNNING__": js(live["running"]),
            "__STATES__": js(live["states"]),
            "__GITSTATES__": js(rc_git.git_states(projs)),
            "__DESK__": js(live["desk"]),
            "__LOGIN__": js(live["login"]),
            "__HOST__": html.escape(cfg.HOST),
        },
    )


def ensure_trusted(proj: str) -> None:
    """Pre-accept the workspace trust dialog for the project dir.

    `claude remote-control` refuses to start in an untrusted dir, exiting
    status 1 before it registers with the relay — so the app never sees the
    session and the phone tap silently does nothing. No interactive trust
    dialog is reachable from the phone, so we accept it here. Atomic replace,
    and we only write when the flag is missing, to avoid racing claude's own
    frequent writes to this file.
    """
    key = os.path.join(cfg.PARENT, proj)
    try:
        d = json.loads(Path(cfg.CLAUDE_JSON).read_text())
    except FileNotFoundError:
        return  # no ~/.claude.json yet — nothing to pre-trust
    except (OSError, json.JSONDecodeError) as e:
        # unreadable/corrupt: surface it, don't 500 the launch
        cfg.log_event("trust", proj, f"skip: {e}")
        return
    entry = d.setdefault("projects", {}).setdefault(key, {})
    if entry.get("hasTrustDialogAccepted"):
        return
    entry.setdefault("allowedTools", [])
    entry.setdefault("mcpServers", {})
    entry["hasTrustDialogAccepted"] = True
    tmp = cfg.CLAUDE_JSON + ".rctmp"
    Path(tmp).write_text(json.dumps(d, indent=2))
    os.replace(tmp, cfg.CLAUDE_JSON)


def fresh_cmd(proj: str) -> list[str]:
    """Fresh-launch invocation. same-dir uses the top-level FLAG form: it starts a
    local-first session whose phone-driven turns land in a normal desk-resumable
    transcript. The `remote-control` subcommand/server form births relay-only threads
    that neither the desk nor the launcher's own --continue can ever reopen (proven
    2026-08-16: sandbox, subcommand-born, 68/68 sdk-cli records, resume always fell back;
    rcprobe-flag, flag-born, `claude --continue` recalled the phone conversation).
    worktree/session keep the subcommand form — the flag form takes no --spawn, and
    those modes are isolated by design, so desk resumability isn't their point."""
    if cfg.SPAWN == "same-dir":
        return [CLAUDE, "--remote-control", proj]
    return [CLAUDE, "remote-control", "--name", proj, "--spawn", cfg.SPAWN]


def launch_cmd(proj: str) -> tuple[list[str], bool]:
    """The claude invocation for proj, and whether it resumes. Resume is the
    top-level flag form `claude --continue --remote-control <proj>` (the
    remote-control subcommand can't resume); it exists only for same-dir,
    doesn't take --spawn, and reloads the project's most recent thread so the
    phone opens where you left off. Otherwise launch fresh."""
    if cfg.RESUME in ("continue", "fork") and cfg.SPAWN == "same-dir":
        cmd = [CLAUDE, "--continue"]
        if cfg.RESUME == "fork":
            cmd.append("--fork-session")
        return [*cmd, "--remote-control", proj], True
    return fresh_cmd(proj), False


def has_desk_thread(proj: str) -> bool:
    """Anything locally resumable for proj? Desk/flag-form sessions write transcripts with
    entrypoint "cli" (or "claude-vscode"); phone-born relay-only sessions leave only
    "sdk-cli" mirrors that `--continue` refuses. Deciding up front skips the doomed resume
    attempt entirely — its death can also land AFTER _spawn's 3s aliveness window, which
    read as a phantom "launched" whose session then evaporated (remain-on-exit already off).
    Only the first 256 KiB of each transcript is read: the entrypoint field appears within
    the first records of every real transcript, and transcripts grow to hundreds of MB —
    slurping them whole made every launch tap pay for the largest project's history."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.join(cfg.PARENT, proj))
    for f in cfg.CLAUDE_PROJECTS.glob(f"{slug}/*.jsonl"):
        with contextlib.suppress(OSError), open(f, "rb") as fh:
            if re.search(rb'"entrypoint":"(cli|claude-vscode)"', fh.read(262144)):
                return True
    return False


def death_reason(sess: str) -> str:
    """Why a just-launched RC session died, read from its dead pane."""
    out = rc_tmux.tmux("capture-pane", "-t", f"={sess}", "-p").stdout
    last = next(
        (
            s
            for ln in reversed(out.splitlines())
            if (s := ln.strip()) and not s.startswith("Pane is dead")
        ),
        "",
    )
    low = last.lower()
    if "trust" in low:
        return "untrusted dir"
    if any(w in low for w in ("auth", "logged out", "log in", "login", "credential")):
        return "login expired — run `claude /login` on the Mac"
    return last[:80] or "exited immediately"


# Interactive-prompt policy: pane sentinel -> (keys to answer with, audit-log note).
# This is PRODUCT policy (the owner's standing "never compact, always full resume"
# choice), kept as data so the next claude prompt is a table row, not a _spawn rewrite.
_PROMPT_ANSWERS = MappingProxyType(
    {
        "Resume from summary": (("Down", "Enter"), "auto-confirmed FULL resume"),
    }
)


def _settle_prompt(sess: str, proj: str) -> str:
    """Detect and answer a known interactive prompt in the freshly-spawned session.
    Claude can survive the liveness window stuck at a prompt the phone never sees —
    it then never registers with the relay, so the tap would read "launched" while
    the session is absent from the app (hit live: the resume-cost prompt on a
    9h/833k-token thread). Returns '' when there is no prompt or it was answered;
    a death reason for an UNKNOWN confirm-style prompt (fail loudly, never
    phantom-succeed)."""
    pane = rc_tmux.tmux("capture-pane", "-t", f"={sess}", "-p").stdout
    for sentinel, (keys, note) in _PROMPT_ANSWERS.items():
        if sentinel in pane:
            rc_tmux.tmux("send-keys", "-t", f"={sess}", *keys)
            cfg.log_event("launch", proj, note)
            return ""
    if "Enter to confirm" in pane:
        first = next((s for ln in pane.splitlines() if (s := ln.strip())), "prompt")
        return f"stuck at interactive prompt: {first[:60]}"
    return ""


def _spawn(sess: str, proj: str, cmd: list[str], env_opts: list[str]) -> str:
    """Start cmd detached in tmux, rooted in proj. Returns '' if it's still alive
    after the startup window, else the death reason (and kills the session). RC
    dies within ~2s on any startup error — untrusted dir, expired login, or
    nothing to --continue — taking its tmux session with it; remain-on-exit
    holds the dead pane so death_reason can read WHY."""
    # the one tmux call that is NOT rc_tmux.tmux(): that captures output, and a
    # new-session failure must reach the launcher's log, not be swallowed
    subprocess.run(
        [
            rc_tmux.TMUX,
            "new-session",
            "-d",
            "-s",
            sess,
            *env_opts,
            "-c",
            os.path.join(cfg.PARENT, proj),
            " ".join(cmd),
        ],
        check=False,
    )
    rc_tmux.tmux("set-option", "-t", f"={sess}", "remain-on-exit", "on")
    time.sleep(3)
    dead = rc_tmux.tmux(
        "list-panes", "-t", f"={sess}", "-F", "#{pane_dead}"
    ).stdout.strip()
    if dead != "0":
        reason = death_reason(sess)
        rc_tmux.tmux("kill-session", "-t", f"={sess}")
        return reason
    if reason := _settle_prompt(sess, proj):
        rc_tmux.tmux("kill-session", "-t", f"={sess}")
        return reason
    rc_tmux.tmux("set-option", "-t", f"={sess}", "remain-on-exit", "off")
    return ""


def _session_env(sess: str, proj: str) -> list[str]:
    """The `-e` pairs a spawned session carries.

    RC_REMOTE/RC_PROJECT tag the session so the state hook fires for remote
    (phone-driven) sessions only, not local desk ones; the sessions the RC server
    spawns inherit them, so rc_status.py can tell when a remote turn is live on the
    shared working tree. PATH goes in per-session, not the plist: tmux sessions inherit
    the tmux SERVER's environment, set by whoever started the server first, so a plist
    PATH is non-deterministic; -e is order-immune and carries to the future systemd
    host. Without ~/.local/bin, MCP servers and hooks claude spawns by name (uvx, uv,
    ruff) fail on phone-launched sessions while working at the desk.
    """
    env = {
        "RC_REMOTE": sess,
        "RC_PROJECT": proj,
        "RC_SHARE_DIR": cfg.SHARE,
        "PATH": f"{os.path.expanduser('~/.local/bin')}:"
        f"{os.environ.get('PATH', '/usr/bin:/bin')}",
    }
    if state_dir := os.environ.get("RC_STATE_DIR"):
        env["RC_STATE_DIR"] = state_dir
    return [opt for name, value in env.items() for opt in ("-e", f"{name}={value}")]


def launch(proj: str) -> tuple[str, str | None]:
    sess = rc_tmux.session_name(proj)
    if rc_tmux.has_session(sess):
        return "already", None
    ensure_trusted(proj)
    if snap := rc_git.snapshot(proj):
        cfg.log_event("snap", proj, snap)
    env_opts = _session_env(sess, proj)
    cmd, resuming = launch_cmd(proj)
    if resuming and not has_desk_thread(proj):
        # Brand-new or phone-born (relay-only history): nothing to --continue. Go
        # straight to the fresh flag-form launch instead of paying the 3s stall and
        # racing the aliveness window on an attempt that can only die.
        cfg.log_event("resume", proj, "no desk thread; fresh launch")
        cmd, resuming = fresh_cmd(proj), False
    # Resume reopens the project's last thread, so the phone would be a second
    # client on it. Hand the project off first: close any live desktop (non-RC)
    # claude rooted inside it, letting it flush its transcript, then launch.
    if resuming and cfg.TAKEOVER and (killed := rc_desk.takeover(proj)):
        cfg.log_event("takeover", proj, ",".join(map(str, killed)))
    reason = _spawn(sess, proj, cmd, env_opts)
    # A brand-new / never-used project has no thread to --continue; the resume
    # form exits 1. Fall back to a plain fresh launch so create-and-start works.
    # Log the REAL death reason — this also fires on login-expiry etc., and a
    # hardcoded "no history" mislabeled those in the audit trail.
    if reason and resuming:
        cfg.log_event("resume", proj, f"fresh relaunch after: {reason}")
        reason = _spawn(sess, proj, fresh_cmd(proj), env_opts)
    return ("failed", reason) if reason else ("launched", None)


def stop(proj: str) -> tuple[str, str | None]:
    sess = rc_tmux.session_name(proj)
    # Graceful first: SIGINT the claude server (Ctrl-C to the pane's foreground
    # process) so it can deregister from Anthropic's relay. An abrupt kill-session
    # sends SIGHUP, which the relay can't tell apart from the Mac dropping off the
    # network — so the app keeps showing the session "connected" until the relay's
    # inactivity timeout (~10 min) evicts it. Give claude a moment to disconnect,
    # then hard-kill the session as fallback. (rc_tmux.graceful_stop() is the
    # confirming version the desk guard uses; adopting it here is a product change —
    # this reports "stopped" without checking — tracked in TODO.md.)
    rc_tmux.tmux("send-keys", "-t", f"={sess}", "C-c")
    time.sleep(2)
    rc_tmux.tmux("kill-session", "-t", f"={sess}")
    return "stopped", None


def desk_stop(proj: str) -> tuple[str, str | None]:
    """Gracefully close the project's desk session(s) from the phone: the same
    SIGTERM -> wait -> SIGKILL as takeover, so claude flushes its transcript and
    deregisters from the app pairing — the thread stays resumable afterwards
    (desk `claude` or a launcher tap both pick it up)."""
    pids = rc_desk.takeover(proj)
    if pids:
        cfg.log_event("stopdesk", proj, ",".join(map(str, pids)))
    # drop the scan cache so the badge reflects the just-changed reality next poll
    rc_desk.desk_projects.invalidate()
    return ("stopped" if pids else "idle"), None


def create(proj: str) -> tuple[str, str | None]:
    """Make a new project dir under PARENT, git-init it, drop a CLAUDE.md stub.

    NAME_RE keeps proj a single path segment, so it can't escape PARENT. git
    runs best-effort: if it's missing the dir and CLAUDE.md still stand and the
    session launches anyway. The route launches it after this returns 'created'.
    """
    if not cfg.NAME_RE.match(proj):
        return "badname", "letters, digits, dot, dash, underscore only"
    path = os.path.join(cfg.PARENT, proj)
    try:
        os.makedirs(path)
    except FileExistsError:  # an existing project, or a second tap racing the first
        return "exists", None
    subprocess.run([cfg.GIT, "init", "-q"], cwd=path, capture_output=True)
    Path(path, "CLAUDE.md").write_text(f"# {proj}\n")
    return "created", None
