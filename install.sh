#!/usr/bin/env bash
# Setup for the Remote Control launcher. Runs on macOS (launchd) or Linux
# (systemd --user). Idempotent: safe to re-run. Does not touch sudo / system
# settings beyond installing tmux — host-specific steps are printed at the end.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
OS="$(uname -s)"
TOKEN_FILE="$HOME/.config/rc-launcher/token"
PORT="${RC_LAUNCHER_PORT:-8787}"
PROJECTS_PARENT="${RC_PROJECTS_PARENT:-$HOME/projects}"
CLAUDE_BIN="${RC_CLAUDE_BIN:-$HOME/.local/bin/claude}"
PY="$(command -v python3 || true)"
TMUX_BIN="${RC_TMUX_BIN:-$(command -v tmux || true)}"
NOTIFY_URL="${RC_NOTIFY_URL:-}"

echo "==> rc-launcher install (repo: $REPO, os: $OS)"

# 1. python3 + tmux (tmux holds each session so it survives the request returning)
[ -x "$PY" ] || { echo "!! python3 not found on PATH"; exit 1; }
if [ -z "$TMUX_BIN" ]; then
  echo "==> installing tmux"
  case "$OS" in
    Darwin) brew install tmux ;;
    Linux)  sudo apt-get install -y tmux 2>/dev/null \
              || sudo dnf install -y tmux 2>/dev/null \
              || sudo pacman -S --noconfirm tmux 2>/dev/null \
              || { echo "!! install tmux manually, then re-run"; exit 1; } ;;
  esac
  TMUX_BIN="$(command -v tmux)"
fi

# 2. claude binary present
[ -x "$CLAUDE_BIN" ] || { echo "!! claude not found at $CLAUDE_BIN (set RC_CLAUDE_BIN)"; exit 1; }

# 3. token (generate once, reuse thereafter; never in the repo)
mkdir -p "$(dirname "$TOKEN_FILE")"
if [ ! -s "$TOKEN_FILE" ]; then
  "$PY" -c "import secrets;print(secrets.token_urlsafe(24))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi
TOKEN="$(cat "$TOKEN_FILE")"

# 4. register the turn-state hook (guarded by $RC_REMOTE so it only fires for
#    phone-driven sessions) for desk-side awareness of live remote turns.
"$PY" - "$REPO" <<'PY'
import json, os, sys
repo = sys.argv[1]
p = os.path.expanduser("~/.claude/settings.json")
d = json.load(open(p)) if os.path.exists(p) else {}
cmd = f'[ -n "$RC_REMOTE" ] && python3 {repo}/rc_state_hook.py; true'
hooks = d.setdefault("hooks", {})
added = False
for ev in ["UserPromptSubmit", "Notification", "Stop", "SubagentStop", "SessionStart", "SessionEnd"]:
    g = hooks.setdefault(ev, [])
    if not any(h.get("command") == cmd for x in g for h in x.get("hooks", [])):
        g.append({"hooks": [{"type": "command", "command": cmd}]})
        added = True
os.makedirs(os.path.dirname(p), exist_ok=True)
with open(p, "w") as f:
    json.dump(d, f, indent=2)
    f.write("\n")
print(f"   state hook {'registered' if added else 'already present'} in {p}")
PY

# 5. service + login-health watchdog, per OS
install_launchd() {
  local L="com.matt.rc-launcher" H="com.matt.rc-healthcheck"
  local PLIST="$HOME/Library/LaunchAgents/${L}.plist"
  local HC="$HOME/Library/LaunchAgents/${H}.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${L}</string>
  <key>ProgramArguments</key><array>
    <string>${PY}</string><string>${REPO}/rc_launcher.py</string>
  </array>
  <key>WorkingDirectory</key><string>${REPO}</string>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>${HOME}</string>
    <key>PATH</key><string>$(dirname "$PY"):$(dirname "$TMUX_BIN"):/usr/bin:/bin</string>
    <key>RC_LAUNCHER_TOKEN</key><string>${TOKEN}</string>
    <key>RC_PROJECTS_PARENT</key><string>${PROJECTS_PARENT}</string>
    <key>RC_CLAUDE_BIN</key><string>${CLAUDE_BIN}</string>
    <key>RC_TMUX_BIN</key><string>${TMUX_BIN}</string>
    <key>RC_LAUNCHER_PORT</key><string>${PORT}</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/tmp/rc-launcher.log</string>
  <key>StandardErrorPath</key><string>/tmp/rc-launcher.err</string>
</dict></plist>
EOF
  cat > "$HC" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${H}</string>
  <key>ProgramArguments</key><array>
    <string>${PY}</string><string>${REPO}/rc_healthcheck.py</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>${HOME}</string>
    <key>RC_CLAUDE_BIN</key><string>${CLAUDE_BIN}</string>
    <key>RC_NOTIFY_URL</key><string>${NOTIFY_URL}</string>
  </dict>
  <key>RunAtLoad</key><true/><key>StartInterval</key><integer>1800</integer>
  <key>StandardOutPath</key><string>/tmp/rc-healthcheck.log</string>
  <key>StandardErrorPath</key><string>/tmp/rc-healthcheck.err</string>
</dict></plist>
EOF
  for x in "$L:$PLIST" "$H:$HC"; do
    launchctl bootout "gui/$(id -u)/${x%%:*}" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "${x#*:}"
    launchctl enable "gui/$(id -u)/${x%%:*}"
  done
}

install_systemd() {
  local U="$HOME/.config/systemd/user"
  mkdir -p "$U"
  cat > "$U/rc-launcher.service" <<EOF
[Unit]
Description=Remote Control launcher
[Service]
ExecStart=${PY} ${REPO}/rc_launcher.py
WorkingDirectory=${REPO}
Environment=RC_LAUNCHER_TOKEN=${TOKEN}
Environment=RC_PROJECTS_PARENT=${PROJECTS_PARENT}
Environment=RC_CLAUDE_BIN=${CLAUDE_BIN}
Environment=RC_TMUX_BIN=${TMUX_BIN}
Environment=RC_LAUNCHER_PORT=${PORT}
Restart=always
RestartSec=10
[Install]
WantedBy=default.target
EOF
  cat > "$U/rc-healthcheck.service" <<EOF
[Unit]
Description=RC launcher login-health watchdog
[Service]
Type=oneshot
ExecStart=${PY} ${REPO}/rc_healthcheck.py
Environment=RC_CLAUDE_BIN=${CLAUDE_BIN}
Environment=RC_NOTIFY_URL=${NOTIFY_URL}
EOF
  cat > "$U/rc-healthcheck.timer" <<EOF
[Unit]
Description=Run RC login-health watchdog every 30 min
[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now rc-launcher.service
  systemctl --user enable --now rc-healthcheck.timer
  command -v loginctl >/dev/null && loginctl enable-linger "$USER" 2>/dev/null || true
}

case "$OS" in
  Darwin) install_launchd ;;
  Linux)  install_systemd ;;
  *) echo "!! unsupported OS: $OS (expected Darwin or Linux)"; exit 1 ;;
esac

# 6. phone URL + host-specific manual steps
case "$OS" in
  Darwin) IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '<host-ip>')" ;;
  Linux)  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; [ -n "$IP" ] || IP="<host-ip>" ;;
esac
echo
echo "==> launcher loaded. Bookmark / Add-to-Home-Screen on your phone:"
echo "    http://${IP}:${PORT}/?token=${TOKEN}"
echo "    (the token is stored once as a cookie, then dropped from the URL)"
echo
echo "==> do these by hand (see RUNBOOK.md):"
echo "    one-time:  $CLAUDE_BIN   then /login   (caches the OAuth token RC needs)"
if [ "$OS" = "Darwin" ]; then
  echo "    sudo pmset -a autorestart 1 sleep 0 disksleep 0   (survive power loss, stay awake)"
  echo "    System Settings -> Users & Groups: temporary auto-login (loads keychain at boot)"
  echo "    System Settings -> General -> Sharing -> Remote Login (optional SSH fallback)"
else
  echo "    keep the host awake / auto-starting per your distro (lingering is already enabled)"
fi
echo "    reach it: same LAN, or a VPN / tailnet subnet route to ${IP}"
echo "    optional: RC_NOTIFY_URL=https://ntfy.sh/your-topic ./install.sh  (phone push on login lapse)"
echo "    optional: RC_SNAPSHOT=1 in the service env  (git-checkpoint the tree before each remote turn)"
