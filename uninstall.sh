#!/usr/bin/env bash
# Unload the launcher and stop any live RC sessions. Keeps the saved token.
set -euo pipefail
LABEL="com.matt.rc-launcher"
HC_LABEL="com.matt.rc-healthcheck"
TMUX_BIN="/opt/homebrew/bin/tmux"

for l in "$LABEL" "$HC_LABEL"; do
  launchctl bootout "gui/$(id -u)/${l}" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/${l}.plist"
done

if [ -x "$TMUX_BIN" ]; then
  "$TMUX_BIN" list-sessions -F '#{session_name}' 2>/dev/null \
    | grep '^rc-' \
    | while read -r s; do "$TMUX_BIN" kill-session -t "$s"; done
fi

echo "rc-launcher unloaded; rc-* sessions stopped."
echo "(token kept at ~/.config/rc-launcher/token; delete it to rotate)"
