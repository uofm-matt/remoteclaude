# Remote Control launcher

Start a Claude Code **Remote Control** session on the Mac from your phone, in
any one of your ~57 project directories, with that project's full context —
without SSH, without leaving VS Code running, and without the Mac running
Tailscale.

> **Host:** this runbook is written for the macOS (launchd) host. `install.sh`
> also targets Linux via `systemd --user`; the daily-use, security, and design
> sections apply to both. Only the launchd, `pmset`, and auto-login specifics are
> macOS-only.

## How it works

- A tiny always-on web server on the Mac (`rc_launcher.py`) shows a searchable
  list of every directory under `~/projects`. Tap one.
- That fires `claude remote-control --name <project>` **in the project's root**,
  held in a detached `tmux` session so it survives the request returning.
- The session shows up in the **Claude app → Code** with a green dot. You drive
  it from there. Because it launched in the project root, it loads that project's
  `CLAUDE.md`, `.claude/` settings and project MCP exactly like the VS Code
  extension does.
- The phone reaches the launcher at the Mac's LAN IP (`<mac-lan-ip>:8787`)
  through the **Tailscale subnet route on your router** — the Mac itself needs no
  Tailscale. The Claude app reaches the RC sessions over Anthropic's own relay,
  so that path needs nothing from you.

Per-project switching genuinely requires a per-directory launch: `claude
remote-control` is rooted at its launch cwd. One server already accepts up to 32
concurrent sessions *within* a directory, so the launcher only handles switching
*between* projects.

## What runs on the Mac

This is the whole Mac-side footprint. Take the network items to ops-notes.

| Piece | What | State |
|---|---|---|
| `rc_launcher.py` + `com.matt.rc-launcher` LaunchAgent | always-on web server, one cheap process, binds `0.0.0.0:8787`, token-guarded | installed by `install.sh` |
| `rc_healthcheck.py` + `com.matt.rc-healthcheck` LaunchAgent | login-health watchdog, runs `claude auth status` every 30 min, notifies if the OAuth login lapses | installed by `install.sh` |
| `tmux` | holds each launched RC session | `brew install tmux` (was missing) |
| `claude` binary | `~/.local/bin/claude` v2.1.169, logged in once via `/login` so the OAuth token is cached | present; confirm login |
| `pmset` | `autorestart 1`, `sleep 0`, `disksleep 0` so it powers back on and stays awake | manual (sudo) |
| auto-login | temporary, so the user session + keychain load at boot and RC can authenticate | manual (System Settings) |
| Remote Login (SSH) | optional fallback way in | manual (System Settings) |

## Ops-notes (network side — not the Mac)

- **DHCP reservation** for the Mac (`<mac-lan-ip>`) so the bookmarked URL stays
  valid.
- **Tailnet subnet route**: the router advertises `<lan-subnet>`, route approved
  in the tailnet admin.
- **Phone**: Tailscale app installed with "use subnet routes" on.

## Install

```sh
cd ~/projects/remoteclaude
./install.sh
```

It installs tmux if missing, generates a token (stored at
`~/.config/rc-launcher/token`, not in the repo), writes and loads the
LaunchAgent, and prints your phone URL plus the manual steps. Re-runnable.

Then, by hand:

```sh
sudo pmset -a autorestart 1 sleep 0 disksleep 0
~/.local/bin/claude          # run once, /login if not already, then /exit
```

System Settings → Users & Groups → temporary auto-login; optionally General →
Sharing → Remote Login.

On the phone: open `http://<mac-lan-ip>:8787/?token=<token>` (the install
script prints the full URL), then **Add to Home Screen**.

## Daily use

Tap the home-screen icon → type to filter → tap a project → switch to the Claude
app, it's there by name with a green dot. The tapped row shows a spinner until
the session is confirmed up; if it can't start, a toast names why (untrusted
dir, login expired, …) instead of a false "launched". Live projects show a green
dot in the launcher too, refreshed every 5s from `/status`, so a session that
dies on its own clears without a manual reload. Tapping a live row again is a
no-op ("already live"). To close a live session, tap the **✕** on its row and
confirm — that kills the tmux session and ends its Claude context. Recents float
to the top (stored in the browser's localStorage).

To start a brand-new project, type a name that matches nothing: a dashed
**＋ create & start** row appears (or just press Enter). It makes the folder under
`~/projects`, runs `git init`, writes a one-line `CLAUDE.md`, marks the dir
trusted, and launches the RC session — after that it behaves like any other
project. Names allow letters, digits, dot, dash, underscore only (no path
separators), so a typed name can't escape `~/projects`.

The header shows login health. If it reads "logged out", new sessions will fail
until you run `claude /login` on the Mac — the watchdog also fires a notification
when it first detects this, so you usually hear about it before you tap.

Launches and stops are logged with Mountain-Time stamps to `/tmp/rc-launcher.log`
(`launch greenbutton -> launched`), an audit trail of what you started when.

## Resume & takeover

By default every tap **reopens the project's most recent thread** (`claude --continue
--remote-control <proj>`), so the phone lands where you left off — the recent turns are
right there, the full history scrollable above. Same "most recent session for this dir"
rule the VS Code extension uses, so a thread you started at your desk continues on the
phone.

Because the phone would then be a second live client on that thread, the launcher first
**hands the project off**: it closes any live *desktop* claude session (VS Code or
terminal) whose working dir is inside that project — SIGTERM, wait for it to flush its
transcript and exit, SIGKILL only if it won't. Scoped strictly by process cwd, so a
desktop session on any *other* project keeps running. The conversation is never lost
(it's on disk — that's what you resume), but a desktop session caught mid-turn has that
turn cut off.

A brand-new project (create-and-start) has no thread to continue: the resume form exits
1, and the launcher falls back to a normal fresh launch automatically, so nothing breaks.

Toggles (set at install time, e.g. `RC_RESUME=off ./install.sh`):
- `RC_RESUME` — `continue` (default), `fork` (branch a new thread from the last one,
  leaving the desktop thread untouched), or `off` (always start fresh).
- `RC_TAKEOVER` — `1` (default) closes the project's desktop session first; `0` leaves it
  running and you accept two clients on one thread.

Resume applies to `same-dir` spawn (the default); `RC_SPAWN=worktree`/`session` always
launch fresh, since those modes are isolated by design. Takeover uses `pgrep`/`ps`/`lsof`
and is verified on macOS; on Linux it degrades to a no-op (resume itself still works).

Caveat: if VS Code auto-restarts the session it just lost and reattaches, it re-creates
the collision — close that panel rather than letting it reconnect while you drive from the
phone.

## File share

A dedicated drop directory, `~/rc-share` (`RC_SHARE_DIR`), served read-only over HTTP at
`/files` behind the same token as the launcher, and — if you turn on SMB by hand —
mountable as a drive on Windows. It is a directory of its own, never `~/projects` or
`$HOME`: the `/files` handler resolves every request with `realpath` and 404s anything
that lands outside `~/rc-share`, including symlinks that point out of it.

- **Sessions copy files there.** Every phone-launched session inherits `RC_SHARE_DIR` in
  its env, so "copy the build output into the rc" resolves to a known path: `cp
  dist/app.zip "$RC_SHARE_DIR/"`. It appears immediately at `/files` and on the SMB
  mount. **Copy, don't symlink** anything you want to see on the web — a symlink pointing
  back into a project resolves outside the share and is refused over HTTP by design (it
  would work over SMB, but not both). See the global CLAUDE.md "rc file share" note.
- **Browse from the phone.** Tap **files** in the launcher header, or open
  `http://<mac-lan-ip>:8787/files`. Folders drill in; files stream (PDFs and images
  preview inline, everything else downloads). Upload with the **+ upload** button
  (multiple files at once); they PUT into the folder you're viewing. **Long-press** a
  file to delete it (asks first). Uploads and deletes work in the phone app's web view
  too, no app rebuild needed — they're page + server, not native.
- **Mount on Windows (optional).** HTTP is read-only by design; the read-write path is
  SMB. Enable it once: System Settings → General → Sharing → **File Sharing**, add *only*
  `~/rc-share`, protocol SMB, guest off. Two gotchas the Apple docs skip: tick your user
  **On** in *Options* (that stores the SMB password hash — without it auth fails with no
  clear error), and mount by **IP**, not name — the subnet route carries unicast only, so
  `\\<name>`-style resolution doesn't cross it. Then on Windows: `net use Z:
  \\<mac-lan-ip>\rc-share`. It rides the same subnet route as everything else; nothing is
  exposed to the internet and the Mac still runs no Tailscale.

Writes — HTTP upload, local `cp` from sessions, and SMB from Windows — all land in the
same folder. HTTP uploads are token-gated and `realpath`-confined to `~/rc-share` exactly
like reads (one `within_share()` boundary): a filename that resolves outside it is refused.
The Android share target uploads are **resumable**: bytes stream into a `.rcpart` temp
(hidden from the listing) and a dropped connection resumes from the last received byte —
the app `HEAD`s the path for `X-Rc-Have`, then `PUT`s from that offset with `X-Rc-Total`,
and the server renames to the final name once the temp reaches the total. Matters over a
flaky link (Starlink/Tailscale), where restarting an 82 MB upload from zero is painful.

## Verify

1. `cat /tmp/rc-launcher.err` — should be empty; `/tmp/rc-launcher.log` shows the
   bind line.
2. Hit the URL from a laptop browser first; the list should render.
3. Tap one project, then `tmux ls` on the Mac — expect `rc-<project>`. Open the
   Claude app → Code → green dot for that name. **This is the one thing worth
   confirming empirically** — that a headless `tmux`-launched RC session
   registers and shows the green dot.

## Troubleshooting

- **No green dot / session dies immediately**: the cached login expired. Run
  `~/.local/bin/claude` and `/login` again. RC needs the claude.ai OAuth token;
  API keys don't work. The launcher header and the watchdog both surface this;
  `cat /tmp/rc-healthcheck.log` shows the last few checks (`login=ok …`).
- **Want the login-lapse alert on your phone, not just the Mac**: the watchdog
  POSTs to `RC_NOTIFY_URL` if set. Pick an [ntfy.sh](https://ntfy.sh) topic,
  subscribe to it in the ntfy app, then re-run `install.sh` with
  `RC_NOTIFY_URL=https://ntfy.sh/your-topic ./install.sh`. Unset, it only does a
  local macOS notification.
- **Launcher 403**: wrong/missing `?token=`. Re-copy from
  `~/.config/rc-launcher/token`.
- **Phone can't reach the URL**: subnet route not approved or "use subnet routes"
  off on the phone — ops-notes side. Test the LAN IP from the Mac itself first.
- **`tmux: command not found` in the err log**: PATH in the plist; confirm
  `/opt/homebrew/bin/tmux` exists.
- **Turn it off after the power week**: `./uninstall.sh`, and switch auto-login
  back off in System Settings.

## Remote re-login (if the claude.ai login lapses while you're away)

You normally won't need this. The OAuth login refreshes itself, has no short
expiry, and survives reboots. It only drops if you `claude logout`, sign in
somewhere that revokes it, change the subscription, or the Mac's clock is wrong.
`claude setup-token` does **not** help — its token is inference-only and can't
open Remote Control sessions, so the only recovery is an interactive `/login`.

The recovery path is SSH from your phone. Remote Login is on, the Mac is
`<mac-lan-ip>`, reachable over the router's Tailscale subnet route:

1. From an SSH client app on the phone (Termius etc.): `ssh <you>@<mac-lan-ip>`.
2. **`security unlock-keychain`** (prompts for your Mac login password). Do this
   first — an SSH session is a separate security context and can't read the GUI
   login keychain where the OAuth token lives, so without it every `claude`
   command reports `loggedIn: false` even when the GUI session is fine. This is
   the easiest step to forget and the one that makes the rest work.
3. `~/.local/bin/claude auth login`, then press `c` to copy the login URL.
4. Open that URL in the phone browser and sign in (**Continue with Google** —
   the provider is handled entirely in the browser; nothing changes CLI-side).
5. The browser shows a **login code** instead of redirecting — expected, the
   Mac's localhost callback isn't reachable from the phone. Copy it.
6. Paste the code into the SSH session, press Enter. The launcher header flips
   back to "login ok".
7. Tap a project in the launcher as usual; it relaunches remote-control per tap.

Sanity check the whole path *before you leave*: SSH in, `security unlock-keychain`,
then `~/.local/bin/claude auth status` — `loggedIn: true` proves it end to end.

Test the phone→SSH hop *before* you leave — the recovery is useless if you can't
get a shell. And don't `claude logout` on the Mac while away: it kills the RC
login, and it's the same lever the stale-ghost note suggests, so skip it remotely.

## Design decisions (don't "helpfully" undo these)

- **One launcher in `~/projects`, on-demand per-project launch** — not one
  always-on RC server per project. With ~57 projects, per-project daemons don't
  scale; a single parent-folder RC server would read files but wouldn't anchor
  any project's `CLAUDE.md`/`.claude/`. The launch-in-root model is the only one
  that preserves full per-project context.
- **`tmux`, not a bare background process** — RC wants a pty and must outlive the
  HTTP request; tmux gives both and lets you `tmux attach -t rc-<proj>` to see
  status/QR.
- **`same-dir` spawn (the RC default)**, not `worktree` — most of these dirs
  aren't git repos, and worktree mode requires git. Flip `RC_SPAWN=worktree` in
  the plist only for repos you want isolated.
- **Mac needs no Tailscale** — reachability rides the router's subnet route. This
  removes the "Tailscale app must auto-start at boot on the Mac" failure mode,
  which matters for the hands-off-after-power scenario.
- **Token in the LaunchAgent env, not the repo** — the secret never lands in
  version control.
- **Remote taps resume the last thread and take over the desk by default** — `claude
  --continue --remote-control` (flag form; the subcommand can't resume, and the flag form
  doesn't take/need `--spawn` for same-dir) reopens the project's most recent thread, and
  a live desktop session for that project is closed first so the phone isn't a second
  client on it. Scoped by process cwd inside the project, never other projects. Falls back
  to a fresh launch when there's nothing to resume. `RC_RESUME=off` / `RC_TAKEOVER=0` opt
  out; resume is same-dir only.
- **File share is `realpath`-confined to `~/rc-share`; HTTP does read + upload** — `/files`
  browses, downloads, and PUT-uploads into only `~/rc-share`, with one `within_share()`
  boundary so `..` and escaping symlinks are refused on reads *and* writes; the launcher
  token gates it and it never serves `~/projects`. `tests/test_confinement.py` pins this. The Windows drive-mount is Apple's own
  SMB (config, not code, not a dependency), not WebDAV — WebDAV over plain HTTP on Windows
  needs per-client registry edits, caps transfers at 50 MB by default, and sends the
  password in clear text. `tailscale serve` was rejected for the same reason the Mac runs
  no Tailscale: it needs `tailscaled` on the Mac.

## Eval notes (the-mac, grounded against the real machine)

- `claude remote-control` confirmed in v2.1.169; flags `--name`, `--spawn`
  (`same-dir`/`worktree`/`session`), `--capacity` (default 32), `--permission-mode`
  are real. It is a persistent multi-session server.
- `claude` on the interactive shell is a function alias (`claude-sub`); the real
  binary is `~/.local/bin/claude`. Scripts/agents must use the absolute path.
- Resume verified against v2.1.195: `claude --continue --remote-control <name>` brings the
  RC session up with the project's last thread already loaded (the recent turns replay in
  the session view). The `remote-control` subcommand has no resume flag; the flag form
  doesn't accept `--spawn`/`--capacity` (subcommand-only); `--continue` on a dir with no
  history exits 1 under remote-control (hence the fresh-launch fallback), though it starts
  fresh cleanly under `-p`. Desktop-vs-RC sessions are told apart by exe basename
  (`claude`) + absence of `remote-control` in argv; each is tied to a project by its cwd
  (`lsof -d cwd`).
- Projects live in `~/projects` (57 dirs), not `~/code`/30.
- `tmux` was not installed; `install.sh` adds it.
- `python3` for the agent: `/opt/homebrew/bin/python3`.
