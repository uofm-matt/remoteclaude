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
SHARE_DIR="${RC_SHARE_DIR:-$HOME/rc-share}"
RESUME="${RC_RESUME:-continue}"    # continue | fork | off
TAKEOVER="${RC_TAKEOVER:-1}"       # 1 = close the project's desktop session first
SPAWN="${RC_SPAWN:-same-dir}"      # same-dir | worktree | session
GROUPS="${RC_PROJECT_GROUPS:-}"    # category dirs to descend one level into (e.g. work,hobby)
SNAPSHOT="${RC_SNAPSHOT:-}"        # 1 = git-checkpoint the tree before each remote turn
CLAUDE_BIN="${RC_CLAUDE_BIN:-$HOME/.local/bin/claude}"
PY="$(command -v python3 || true)"
TMUX_BIN="${RC_TMUX_BIN:-$(command -v tmux || true)}"
NOTIFY_URL="${RC_NOTIFY_URL:-}"

# --reload: pick up edited rc_*.py by restarting ONLY the launcher service — no bootout/
# bootstrap, no plist/unit rewrite. Confirm the new code is live via the unauthenticated
# /version (its stamp is a hash of the rc_*.py, so it advances when the source does).
if [ "${1:-}" = "--reload" ]; then
  case "$OS" in
    Darwin) launchctl kickstart -k "gui/$(id -u)/com.matt.rc-launcher" || { echo "!! kickstart failed"; exit 1; } ;;
    Linux)  systemctl --user restart rc-launcher.service || { echo "!! restart failed"; exit 1; } ;;
    *) echo "!! unsupported OS for --reload: $OS"; exit 1 ;;
  esac
  echo "==> reloaded; curl -s localhost:${PORT}/version to confirm the new build stamp"
  exit 0
fi

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

# 3. token (generate once, reuse thereafter; never in the repo). Only the 0600 file
#    holds it — the launcher reads it directly, so the service files below stay
#    secret-free and rotation is delete-file + re-run (or write-file + kickstart).
mkdir -p "$(dirname "$TOKEN_FILE")"
if [ ! -s "$TOKEN_FILE" ]; then
  "$PY" -c "import secrets;print(secrets.token_urlsafe(24))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi

# 3b. dedicated file-share drop dir — served at /files (browse, download, upload) and, if you enable
#     SMB by hand, mountable as a Windows drive. A dir of its own, never ~/projects.
mkdir -p "$SHARE_DIR"
chmod 700 "$SHARE_DIR"

# 4. register the turn-state hook (guarded by $RC_REMOTE so it only fires for
#    phone-driven sessions) for desk-side awareness of live remote turns.
"$PY" "$REPO/rc_state_hook.py" --install-hook "$REPO" || { echo "!! hook registration failed"; exit 1; }

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
    <key>RC_PROJECTS_PARENT</key><string>${PROJECTS_PARENT}</string>
    <key>RC_CLAUDE_BIN</key><string>${CLAUDE_BIN}</string>
    <key>RC_TMUX_BIN</key><string>${TMUX_BIN}</string>
    <key>RC_LAUNCHER_PORT</key><string>${PORT}</string>
    <key>RC_SHARE_DIR</key><string>${SHARE_DIR}</string>
    <key>RC_RESUME</key><string>${RESUME}</string>
    <key>RC_TAKEOVER</key><string>${TAKEOVER}</string>
    <key>RC_SPAWN</key><string>${SPAWN}</string>
    <key>RC_PROJECT_GROUPS</key><string>${GROUPS}</string>
    <key>RC_SNAPSHOT</key><string>${SNAPSHOT}</string>
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
    <key>RC_LAUNCHER_PORT</key><string>${PORT}</string>
    <key>RC_SHARE_DIR</key><string>${SHARE_DIR}</string>
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
Environment=RC_PROJECTS_PARENT=${PROJECTS_PARENT}
Environment=RC_CLAUDE_BIN=${CLAUDE_BIN}
Environment=RC_TMUX_BIN=${TMUX_BIN}
Environment=RC_LAUNCHER_PORT=${PORT}
Environment=RC_SHARE_DIR=${SHARE_DIR}
Environment=RC_RESUME=${RESUME}
Environment=RC_TAKEOVER=${TAKEOVER}
Environment=RC_SPAWN=${SPAWN}
Environment=RC_PROJECT_GROUPS=${GROUPS}
Environment=RC_SNAPSHOT=${SNAPSHOT}
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
Environment=RC_LAUNCHER_PORT=${PORT}
Environment=RC_SHARE_DIR=${SHARE_DIR}
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
echo "    http://${IP}:${PORT}/?token=<token>     (token: cat $TOKEN_FILE)"
echo "    (never echoed here — a printed token lands in terminal scrollback/transcripts;"
echo "     it's stored once as a cookie, then dropped from the URL)"
echo
echo "==> do these by hand (see RUNBOOK.md):"
echo "    one-time:  $CLAUDE_BIN   then /login   (caches the OAuth token RC needs)"
if [ "$OS" = "Darwin" ]; then
  echo "    sudo pmset -a autorestart 1 sleep 0 disksleep 0   (survive power loss, stay awake)"
  echo "    System Settings -> Users & Groups: temporary auto-login (loads keychain at boot)"
  echo "    System Settings -> General -> Sharing -> Remote Login (optional SSH fallback)"
  echo "    Windows drive mount (optional): Sharing -> File Sharing, share ONLY ${SHARE_DIR}"
  echo "      over SMB (guest off; tick your user 'On' in Options), then on Windows by IP:"
  echo "      net use Z: \\\\${IP}\\$(basename "$SHARE_DIR")"
else
  echo "    keep the host awake / auto-starting per your distro (lingering is already enabled)"
fi
echo "    file share: sessions drop into ${SHARE_DIR}; browse at http://${IP}:${PORT}/files"
echo "    resume: taps reopen the project's last thread and close its desktop session first"
echo "      (RC_RESUME=${RESUME}, RC_TAKEOVER=${TAKEOVER}); disable with RC_RESUME=off / RC_TAKEOVER=0 ./install.sh"
echo "    reach it: same LAN, or a VPN / tailnet subnet route to ${IP}"
echo "    optional: RC_NOTIFY_URL=https://ntfy.sh/your-topic ./install.sh  (phone push on login lapse)"
echo "    optional: RC_SNAPSHOT=1 ./install.sh  (git-checkpoint the tree before each remote turn)"
