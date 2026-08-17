# Changelog

Human-facing chronological record; newest first. One entry per change — what and why.

## 2026-08-16

- Headless launches no longer hang invisibly at interactive prompts: a huge thread now
  makes claude ask "resume from summary or full?" before it registers with the relay, so
  a phone tap read "launched" while the session sat at a prompt nobody could see (hit
  live on a 9h/833k-token bigproj thread — three taps, zero appearances in the app).
  _spawn now auto-confirms the summary-resume prompt (the recommended, usage-saving
  choice) and fails loudly with the prompt text for any other confirm-style screen.

- Launcher shows live desk sessions: a blue "desk" dot/tag on projects where a plain
  interactive `claude` is running (auto-paired with the phone app but invisible to the
  launcher's tmux-based dots — the owner hit this twice in one afternoon). Detection is a
  TTL-cached pgrep/cwd scan; bridge-pointer.json was rejected after live testing (desk
  sessions don't reliably write one; stale ones point at dead pids).
- Backlog paydown across four areas (each independently reviewed before merge):
  - Browser upload client rewritten around a tested pure policy (`rc_upload.js`, spliced
    into the files page; 11 node-test cases mirroring the Android `UploadLogicTest`).
    Fixes four client bugs: a failed HEAD probe was treated as offset 0 (restarting the
    PUT truncates the server's kept partial — the exact policy the Kotlin client pins
    against), zero-byte files faked success, a failed upload's message was wiped by the
    unconditional page reload, and the sort tie-break inherited the primary direction.
    CI now runs the JS suite.
  - Launcher: breadcrumb links no longer double-percent-encode (dirs with spaces/# were
    404ing from the crumb), phone sessions get `~/.local/bin` on PATH via a per-session
    tmux `-e` (MCP servers/hooks spawned by name failed remotely; the plist route was
    non-deterministic — tmux sessions inherit the server's env), `has_desk_thread` reads
    only the first 256KB per transcript (one project's transcript is 525MB), `_fill`
    orders keys longest-first, `rows_html` drops a redundant stat syscall, and the
    sort-key test gained a mixed-case fixture.
  - Android share target: zero-byte files now report "empty file — skipped" instead of
    silently faking success (the server rejects total<=0 by design).
  - RUNBOOK: documented the X-Rc-* resumable-upload wire contract (implemented in three
    runtimes with no spec until now), corrected the resume-symmetry claims to the
    post-fix reality, and dropped stray personal traces.
- Skip the resume attempt when nothing is locally resumable: `launch()` now checks for a
  desk (`entrypoint: cli`/`claude-vscode`) transcript up front and goes straight to the
  fresh flag-form launch. The doomed `--continue` on phone-born projects could die *after*
  `_spawn`'s 3s aliveness window — logged as "launched", then the session evaporated with
  `remain-on-exit` already off (observed live on sandbox: "launched" at 13:46, no tmux session,
  app showing disconnected). Also removes the permanent 3s stall those projects paid per tap.
- Fresh same-dir launches use `claude --remote-control <name>` (the top-level flag) instead
  of the `remote-control` subcommand — the subcommand births relay-only threads that neither
  the desk nor the launcher's own resume can ever reopen, so every phone-created project
  silently lost desk continuity (and re-forked a fresh thread on each tap). Proven by A/B:
  sandbox (subcommand-born, 68/68 `sdk-cli` records, unresumable) vs a probe project
  (flag-born, phone conversation recalled at the desk via `claude --continue`).
  Affected projects self-heal on their next tap: the fallback now launches flag-form, so new
  turns land in a desk-resumable transcript (old relay-only history stays app-only). The
  resume fallback also logs the real death reason instead of a hardcoded "no history".
- Remove the auth token from every public and shared channel — a review panel found the
  `RC_TOKEN` GitHub secret was baked into CI debug APKs uploaded as artifacts on this
  public repo (downloadable by any logged-in GitHub user; 23 unexpired copies deleted,
  secret deleted, token rotated):
  - CI builds untokened APKs; the app asks once and keeps the credential in prefs
    (stable signing preserves it across updates). `UploadActivity` now reads the pasted
    credential too instead of the baked field it used to hard-require.
  - A value-independent CI guard fails the Android build if a token field, secret
    wiring, or an `RC_TOKEN` dex symbol ever reappears; artifacts expire after 7 days.
  - The token also left the LaunchAgent plist and systemd unit — the launcher reads the
    0600 token file directly, so service files are secret-free and rotation is
    write-file + kickstart.
  - Closed two local leak channels: the launcher's ≥400 access log now records the path
    only (the app's uploads carry `?token=` in the query), and `install.sh` no longer
    echoes the tokened URL into terminal scrollback.
  - `allowBackup="false"` so the tokened prefs URL stops leaving the device via Google
    Auto Backup; the paste dialog accepts a full URL for a non-default host.
  - `RC_HOST` for CI builds comes from a repo variable, keeping the tracked source on
    the generic placeholder.
- gitignore `coverage.json` (written by the coverage gate) — audit hygiene item.
- Start this changelog (standing per-project rule; first entry is the remediation above).
