# remoteclaude

Start a Claude Code Remote Control session on your Mac from your phone, in any of
your project directories, with that project's full context. No SSH, no VS Code
left running, no Tailscale on the Mac.

## What it does

A small always-on web server on the Mac shows a searchable list of every
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
| `rc_launcher.py` | Token-guarded web server: launch, stop, live status, and create-new-project. Runs as a `launchd` agent. |
| `rc_healthcheck.py` | Watchdog that runs `claude auth status` every 30 min and notifies if the login lapses. |
| `install.sh` / `uninstall.sh` | LaunchAgent setup and teardown; generates the launcher token outside the repo. |
| `RUNBOOK.md` | Full setup, daily use, login recovery, and design notes. |

## Install

```sh
git clone https://github.com/uofm-matt/remoteclaude.git
cd remoteclaude
./install.sh
```

It installs `tmux` if missing, generates a token (stored at
`~/.config/rc-launcher/token`, never in the repo), writes and loads the
LaunchAgent, and prints your phone URL plus the remaining manual steps. See
[RUNBOOK.md](RUNBOOK.md) for the complete walkthrough, including the network
setup and how to recover if the login lapses while you're away.

## Security

The launcher binds `0.0.0.0` and is guarded by a random token kept in the
LaunchAgent environment, not in version control. Reach it over your LAN or a
VPN/subnet route; don't expose the port directly to the internet. Remote Control
sessions need the claude.ai OAuth login (API keys won't open them).
