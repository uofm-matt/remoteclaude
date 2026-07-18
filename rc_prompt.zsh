# remoteclaude — desk-side awareness of remote Claude turns on the shared tree.
#
# Opt-in: add to ~/.zshrc
#   source /path/to/remoteclaude/rc_prompt.zsh
#
# Shows a right-prompt tag (rc:working / rc:waiting) when a phone-driven turn is
# live in the repo you're standing in. Silent and ~free otherwise: with no state
# files it skips Python entirely, so the common case costs only a glob.
#
# Note: this sets RPROMPT. If you already use RPROMPT, fold $(_rc_prompt) into
# yours instead of sourcing this verbatim.

_RC_DIR="${${(%):-%x}:A:h}"

_rc_prompt() {
  local dir="${RC_STATE_DIR:-$HOME/.cache/rc-state}"
  local files=(${dir}/*.json(N))
  (( ${#files} )) || return
  python3 "$_RC_DIR/rc_status.py" 2>/dev/null
}

setopt prompt_subst
RPROMPT='%F{yellow}$(_rc_prompt)%f'
