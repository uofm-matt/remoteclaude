#!/usr/bin/env bash
# Mac-local setup for the Remote Control launcher.
# Idempotent: safe to re-run. Does NOT touch sudo/System Settings — those
# steps are printed at the end for you to do by hand (see RUNBOOK.md).
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.matt.rc-launcher"
HC_LABEL="com.matt.rc-healthcheck"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
HC_PLIST="$HOME/Library/LaunchAgents/${HC_LABEL}.plist"
TOKEN_FILE="$HOME/.config/rc-launcher/token"
PORT=8787
PY="/opt/homebrew/bin/python3"
CLAUDE_BIN="$HOME/.local/bin/claude"
TMUX_BIN="/opt/homebrew/bin/tmux"
PROJECTS_PARENT="$HOME/projects"

echo "==> rc-launcher install (repo: $REPO)"

# 1. tmux (holds each RC session so it survives the request returning)
if [ ! -x "$TMUX_BIN" ]; then
  echo "==> installing tmux"
  brew install tmux
fi

# 2. claude binary present?
[ -x "$CLAUDE_BIN" ] || { echo "!! claude not found at $CLAUDE_BIN"; exit 1; }

# 3. token (generate once, reuse thereafter)
mkdir -p "$(dirname "$TOKEN_FILE")"
if [ ! -s "$TOKEN_FILE" ]; then
  "$PY" -c "import secrets;print(secrets.token_urlsafe(24))" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi
TOKEN="$(cat "$TOKEN_FILE")"

# 4. LaunchAgent (keeps the launcher always-on; token lives here, not in the repo)
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>${PY}</string>
    <string>${REPO}/rc_launcher.py</string>
  </array>
  <key>WorkingDirectory</key><string>${REPO}</string>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>${HOME}</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>RC_LAUNCHER_TOKEN</key><string>${TOKEN}</string>
    <key>RC_PROJECTS_PARENT</key><string>${PROJECTS_PARENT}</string>
    <key>RC_CLAUDE_BIN</key><string>${CLAUDE_BIN}</string>
    <key>RC_TMUX_BIN</key><string>${TMUX_BIN}</string>
    <key>RC_LAUNCHER_PORT</key><string>${PORT}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/tmp/rc-launcher.log</string>
  <key>StandardErrorPath</key><string>/tmp/rc-launcher.err</string>
</dict></plist>
EOF

# 5. (re)load
launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/${LABEL}"

# 6. login-health watchdog (every 30 min; notifies if the claude.ai OAuth login
#    lapses, since you can't re-login from the phone). Set RC_NOTIFY_URL to an
#    ntfy topic / webhook for a phone push; otherwise it's a local Mac alert.
cat > "$HC_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${HC_LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>${PY}</string>
    <string>${REPO}/rc_healthcheck.py</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>${HOME}</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>RC_CLAUDE_BIN</key><string>${CLAUDE_BIN}</string>
    <key>RC_NOTIFY_URL</key><string>${RC_NOTIFY_URL:-}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>1800</integer>
  <key>StandardOutPath</key><string>/tmp/rc-healthcheck.log</string>
  <key>StandardErrorPath</key><string>/tmp/rc-healthcheck.err</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/${HC_LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HC_PLIST"
launchctl enable "gui/$(id -u)/${HC_LABEL}"

IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "<mac-ip>")"
echo
echo "==> launcher loaded. Bookmark / Add-to-Home-Screen on your phone:"
echo "    http://${IP}:${PORT}/?token=${TOKEN}"
echo
echo "==> do these by hand (see RUNBOOK.md):"
echo "    sudo pmset -a autorestart 1 sleep 0 disksleep 0"
echo "    System Settings -> Users & Groups: temporary auto-login"
echo "    System Settings -> General -> Sharing -> Remote Login (optional fallback)"
echo "    one-time:  $CLAUDE_BIN   then /login   (caches the OAuth token)"
echo "    home-ops:  DHCP reservation for ${IP}; tailnet subnet route; phone subnet-routes on"
