#!/usr/bin/env bash
# Pull the latest CI-built debug APK and (re)install it on the connected phone.
#
# Every CI build is signed with a throwaway debug key, so a new APK won't install
# *over* the previous one — this script uninstalls first, which is the whole point.
#
#   ./sideload.sh                 # install the newest build now (waits if it's still building)
#   ./sideload.sh --build         # trigger a fresh build of the committed code first, then install
#   ./sideload.sh --watch         # loop: auto-install each new green build as it lands (Ctrl-C to stop)
#   ./sideload.sh --watch 15      # ...polling every 15s (default 30)
#   ./sideload.sh --local app.apk # skip CI, install a local APK
#   ./sideload.sh --no-launch     # don't start the app after installing
set -euo pipefail

PKG="com.matt.rclauncher"
ACT="$PKG/.MainActivity"
REPO="uofm-matt/remoteclaude"
WF="android.yml"
ART="remote-control-debug-apk"

usage() { echo "usage: $0 [--watch [seconds]] [--build] [--local <apk>] [--no-launch]"; exit 2; }

watch=0; interval=30; build=0; launch=1; local_apk=""
while [ $# -gt 0 ]; do
  case "$1" in
    --watch) watch=1; case "${2:-}" in ''|--*) : ;; *) interval="$2"; shift ;; esac ;;
    --build) build=1 ;;
    --local) local_apk="${2:-}"; shift ;;
    --no-launch) launch=0 ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
  shift
done

command -v adb >/dev/null 2>&1 || { echo "missing: adb"; exit 1; }
adb get-state >/dev/null 2>&1 || { echo "no adb device — plug in the phone with USB debugging on"; exit 1; }

install_apk() {
  local apk="$1"
  [ -f "$apk" ] || { echo "no APK at $apk"; return 1; }
  echo ">> install in place: $(basename "$apk")"
  # Stable signing means this updates in place and keeps the home-screen icon + data.
  # Only a signing-key change (e.g. the first switch to the stable key) forces a
  # uninstall+reinstall, after which the icon needs re-adding once.
  local out
  out="$(adb install -r -d "$apk" 2>&1)" || true
  echo "$out" | tail -1
  if echo "$out" | grep -qi 'INSTALL_FAILED_UPDATE_INCOMPATIBLE\|signatures do not match\|INSTALL_FAILED_VERSION'; then
    echo ">> signing/version changed — uninstall + reinstall (re-add the home icon once)"
    adb uninstall "$PKG" >/dev/null 2>&1 || true
    adb install "$apk"
  fi
  [ "$launch" = 1 ] && adb shell am start -n "$ACT" >/dev/null 2>&1 || true
}

if [ -n "$local_apk" ]; then
  install_apk "$local_apk"
  exit 0
fi

command -v gh >/dev/null 2>&1 || { echo "missing: gh"; exit 1; }

fetch_and_install() {
  local rid="$1" tmp
  tmp="$(mktemp -d)"
  gh run download "$rid" -R "$REPO" -n "$ART" -D "$tmp" >/dev/null
  install_apk "$(find "$tmp" -name '*.apk' | head -1)"
  rm -rf "$tmp"
}

if [ "$build" = 1 ]; then
  echo ">> dispatching a fresh build of the committed code"
  gh workflow run "$WF" -R "$REPO"
  sleep 8
fi

RID="$(gh run list -R "$REPO" --workflow="$WF" --limit 1 --json databaseId --jq '.[0].databaseId')"
[ -n "$RID" ] || { echo "no runs found for $WF"; exit 1; }
echo ">> waiting on run $RID"
gh run watch "$RID" -R "$REPO" --exit-status >/dev/null || { echo "build failed — not installing"; exit 1; }
fetch_and_install "$RID"

if [ "$watch" = 1 ]; then
  last="$RID"
  echo ">> watching for new successful builds every ${interval}s (Ctrl-C to stop)"
  while true; do
    sleep "$interval"
    rid="$(gh run list -R "$REPO" --workflow="$WF" --status success --limit 1 --json databaseId --jq '.[0].databaseId // empty')"
    if [ -n "$rid" ] && [ "$rid" != "$last" ]; then
      echo ">> new build $rid"
      fetch_and_install "$rid" && last="$rid"
    fi
  done
fi
echo ">> done."
