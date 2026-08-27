# remoteclaude — desk-side launch guard shim, sourceable from bash OR zsh.
#
# Opt-in: add to ~/.zshrc or ~/.bashrc
#   source /path/to/remoteclaude/rc_guard.sh
# then call `_rc_guard "$@" || return` in your claude wrapper before launching.
# Sets _RC_GUARD_ARGS="--new" when you picked a separate fresh session — feed it
# to your resume logic (never to claude itself). All logic lives in rc_guard.py;
# see RUNBOOK.md "Desk-side zsh integration" for a minimal wrapper.
#
# ${BASH_SOURCE[0]:-${(%):-%x}}: bash expands BASH_SOURCE and never evaluates the
# zsh-only fallback; zsh has no BASH_SOURCE and falls through to %x (this file).

_RC_GUARD_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-${(%):-%x}}")" && pwd)"

_rc_guard() {
  _RC_GUARD_ARGS=""
  python3 "$_RC_GUARD_DIR/rc_guard.py" "$@"
  case $? in
    0) return 0 ;;
    2) _RC_GUARD_ARGS="--new"; return 0 ;;
    *) return 1 ;;
  esac
}
