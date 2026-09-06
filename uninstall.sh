#!/usr/bin/env bash
# Unload the launcher + watchdog, stop any live RC sessions, and remove the state
# hook from settings.json. Keeps the saved token. Works on macOS or Linux.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
OS="$(uname -s)"
TMUX_BIN="${RC_TMUX_BIN:-$(command -v tmux || true)}"

case "$OS" in
  Darwin)
    for l in com.matt.rc-launcher com.matt.rc-healthcheck; do
      launchctl bootout "gui/$(id -u)/${l}" 2>/dev/null || true
      rm -f "$HOME/Library/LaunchAgents/${l}.plist"
    done
    ;;
  Linux)
    systemctl --user disable --now rc-launcher.service rc-healthcheck.timer 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/rc-launcher.service" \
          "$HOME/.config/systemd/user/rc-healthcheck.service" \
          "$HOME/.config/systemd/user/rc-healthcheck.timer"
    systemctl --user daemon-reload 2>/dev/null || true
    ;;
esac

# stop any live RC sessions
if [ -n "$TMUX_BIN" ] && [ -x "$TMUX_BIN" ]; then
  "$TMUX_BIN" list-sessions -F '#{session_name}' 2>/dev/null \
    | grep '^rc-' \
    | while read -r s; do "$TMUX_BIN" kill-session -t "$s"; done
fi

# remove the turn-state hook (leaves any other hooks, e.g. unrelated tools, intact)
python3 "$REPO/rc_state_hook.py" --remove-hook "$REPO" 2>/dev/null || true

echo "rc-launcher unloaded; rc-* sessions stopped."
echo "(token kept at ~/.config/rc-launcher/token; delete it to rotate)"
