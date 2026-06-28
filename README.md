# remoteclaude

Start a Claude Code Remote Control session on your computer from your phone, in
any of your project directories, with that project's full context. No SSH, no VS
Code left running, no Tailscale on the host. Runs on macOS or Linux.

## What it does

A small always-on web server on the host shows a searchable list of every
directory under `~/projects`. Tap one and it launches `claude remote-control` in
that project's root, held in a detached `tmux` session so it survives the HTTP
request returning. The session shows up in the Claude app under Code with a green
dot, and you drive it from there. Because it launched in the project root, it
loads that project's `CLAUDE.md`, `.claude/` settings, and project MCP exactly
like the VS Code extension.

You can also create a new project from the interface: tap the **+** button (or
type a name that matches nothing and pick the "create & start" row). It makes the
folder under `~/projects`, runs `git init`, writes a starter `CLAUDE.md`, marks
the directory trusted, and launches the session.

When a phone-driven turn is running on a repo you also have open locally, a desk
indicator (a zsh prompt tag, and working/waiting dots in the launcher) tells you,
so you don't edit the same files out from under it.

## Why per-project launch

`claude remote-control` is rooted at its launch directory, so switching projects
genuinely requires a per-directory launch. With dozens of projects, a daemon per
project doesn't scale, and a single server over the parent folder would read
files but wouldn't anchor any one project's `CLAUDE.md`/`.claude/`. On-demand
launch in the project root is the only model that preserves full per-project
context. One server already accepts many concurrent sessions within a directory,
so the launcher only handles switching between projects.

## Components

| File | Role |
|---|---|
| `rc_launcher.py` | Token-guarded web server: launch, stop, live status, create-new-project. Runs under launchd (macOS) or systemd --user (Linux). |
| `rc_state_hook.py` | Claude hook recording a remote session's turn state (working/waiting/idle) for desk-side awareness. |
| `rc_status.py` / `rc_prompt.zsh` | Reader + opt-in zsh prompt tag showing when a remote turn is live in your current repo. |
| `rc_healthcheck.py` | Watchdog that runs `claude auth status` every 30 min; notifies (desktop + optional ntfy) if the login lapses. |
| `install.sh` / `uninstall.sh` | Service setup and teardown; generates the token outside the repo, registers the state hook. |
| `RUNBOOK.md` | Full setup, daily use, login recovery, and design notes. |

## Install

```sh
git clone https://github.com/uofm-matt/remoteclaude.git
cd remoteclaude
./install.sh
```

It installs `tmux` if missing, generates a token (stored at
`~/.config/rc-launcher/token`, never in the repo), registers the state hook,
loads the service (launchd on macOS, systemd --user on Linux), and prints your
phone URL plus the remaining host-specific steps. See [RUNBOOK.md](RUNBOOK.md)
for the complete walkthrough, including the network setup and how to recover if
the login lapses while you're away.

## Security

The launcher binds `0.0.0.0` and is guarded by a random token kept in the service
environment, not in version control. The token is sent once (URL or bookmark),
stored as an HttpOnly cookie, then dropped from the URL so it stays out of logs
and history. Reach it over your LAN or a VPN/subnet route; don't expose the port
directly to the internet. Remote Control sessions need the claude.ai OAuth login
(API keys won't open them).
