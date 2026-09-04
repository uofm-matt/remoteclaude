# Changelog

Human-facing chronological record; newest first. One entry per change — what and why.

## 2026-09-04

- `ruff format` adopted repo-wide and gated: 13 of 22 .py files reformatted (whitespace,
  quotes, wrapping only — every tracked module parses to an identical AST before and after,
  17 of 17), `ruff format --check .` added to ci.yml. Closes the audit's open decision; the
  drift had reached 6 of 8 shipped modules with nothing enforcing either way. Cosmetic cost:
  ruff splits a few long lines with trailing comments into three-line shapes — move those
  comments above the line when next touching them.
- Test flake fixed: the 411-without-Content-Length pin read the response with one `recv`
  and the JSON body can trail the headers in a second segment; reads to EOF now (the server
  sends `Connection: close`). Passed 5/5 after; it had passed on the day it was written.

## 2026-09-02

- Surface polish on top of the readability pass (mechanical, no behavior change):
  `Handler.log_message` drops its two unused params (`format` also shadowed the builtin) for
  `*_`, so the silenced-access-log override no longer advertises a signature it ignores;
  `_PROMPT_ANSWERS` and rc_status's `GLYPH` become `MappingProxyType` with tuple keystrokes,
  matching `rc_state.RANK` — these are read-only tables and nothing should be able to write
  them at runtime. rc_guard's two vocabulary frozensets, its `parents` set, the `rc_status`
  subprocess call and the still-alive warning go back to the repo's hand-wrapped style from
  the one-item-per-line form a formatter had left. 127 green, ruff clean.

- Gate on the readability pass: the defect subagent found `_git()` had silently added
  `text=True` to snapshot()'s two returncode-only git calls, so an undecodable byte in git's
  stderr would raise `UnicodeDecodeError` out of an unguarded `launch()` — the one place
  the refactor widened an exception surface; those calls take `text=False` now, pinned by
  a kwargs-capturing test. The changed-line ratchet also flagged four pre-existing error
  paths the refactor moved (411 without Content-Length, `launch_reason`, the failed
  `/launch?json=1` reason, the HTML fallback); each got the test it asked for instead of a
  lower number. Panel: 2 of 3 models (xAI returned 403, credits exhausted).
- Readability pass (`/optimize`, quality-only, no behavior change): one `_json_error` for the
  eight JSON refusals (compact separators keep the wire bytes identical to the literals),
  one `_is_files` for the four `/files` predicates, `do_GET`'s six-branch ladder as a `match`,
  and a `_git()` helper beside `_tmux()` for the four `git -C` calls — why: the launcher's HTTP
  surface had eight near-identical error sends and four copies of a confinement predicate, each
  a place the next edit could drift. `_git_state` reads branch/dirty with `next`/`any` instead of
  a two-variable loop; `sweep_rcparts` walks one flattened generator. rc_guard's `live_sess` now
  calls `session_alive` instead of re-inlining `tmux has-session` (two definitions of "is it
  alive" could drift apart), stops shadowing its `parent` parameter, and prints the menu as one
  block. rc_claude's `auth_status` narrows its `try` to the two calls that can fail. Tests: one
  `respond()`/`desk()` in `_harness` replaces six inline pgrep/ps/lsof responders, plus
  `share_dir`/`spawn_ok`/`env` for the setUp boilerplate (127 green before and after, -45 lines).

- Whole-repo audit (`/audit`, four read-only finders + cross-family panel): net A- —
  architecture A-, code A, tests B+, process A-. Tests dropped from A- because 54 targeted
  mutants on shadow copies left 36 green, four of them on load-bearing launcher paths (the
  takeover guard, its SIGTERM grace loop, the `ensure_trusted` call site, `RC_PROJECT` in the
  session env) and one on the resumed-upload truncate that a reproduction showed reporting a
  stale tail as stored. Process found one inverted RUNBOOK claim (Linux takeover "no-op" while
  the code reads /proc) and one half-true invariant (RC_SPAWN "in the plist" that the installer
  overwrites). TODO.md carries the dated paydown (Now 2026-09-05, Next 2026-09-12) and two
  operator decisions (gate `ruff format`; where the refutation record lives in a repo that
  gitignores `.claude/`). Scorecard the same morning: A+ (97.2% over 8 files, 0 lint, 0 debt
  markers) — the countable half cannot see any of the above, which is the point of running both.

## 2026-08-26

- Desk-side launch guard (`rc_guard.py` + `rc_guard.sh`, opt-in like the prompt tag):
  a desk `claude` wrapped with `_rc_guard` no longer launches blind while a phone
  (remote-control) session owns the project — it offers attach (one conversation, desk
  and phone), takeover (close the remote session the way the launcher's stop() does,
  resume the thread at the desk), a separate fresh session, or quit. Hit live: desk
  `claude --continue` under a running rc-* session died with "No conversation found to
  continue"; the launcher guarded the phone->desk direction, nothing guarded desk->phone.
  Started as zsh-only, rebuilt same-day as stdlib Python behind one bash+zsh shim so the
  logic is unit-tested (19 tests incl. a real-pty keypress; in the coverage floor at 99%)
  and one implementation serves every host the launcher runs on. The Tier-H gate's
  panel + defect pass then landed six fixes before it shipped: a missing tmux failed
  CLOSED (traceback -> exit 1 -> shim read "quit" -> every desk launch blocked; now "no
  tmux" means "no rc session"); a trailing slash on `RC_PROJECTS_PARENT` or a symlinked
  parent silently disabled detection (both paths realpath'd now); `[a]` inside an
  existing tmux client used `attach`, which tmux refuses (`switch-client` under `$TMUX`);
  takeover fired kill-session on a fixed 2s timer and returned PROCEED regardless (now
  polls has-session up to 5s, kills only as fallback, refuses to launch if the session
  survives); `claude --version`/`--help`/`mcp …`/`--resume=id` popped the menu; and the
  pty test raced a 50ms timer against `setraw`'s TCSAFLUSH (polls for raw mode now — the
  timer version hung a loaded runner forever). A second panel pass on the fixed code
  added two more: a project dir that is itself a symlink never matched (getcwd() is
  physical; the shell's logical `$PWD` is now tried first when it is the same dir, and
  the parent in both forms — macOS's /var -> /private/var made the test fail before
  the fix did), and `RC_PROJECTS_PARENT=/` produced `//` and matched nothing. Six
  further panel/defect leads were dismissed with an oracle each (recorded in
  `.claude/refutations.md`, local — that dir is gitignored here). No Windows port by
  design: the guard checks local tmux sessions on the launcher host, and the launcher
  (tmux + launchd/systemd) cannot run on Windows — a Windows desk reaches the host over
  SSH, where the shim already applies.
- Every launcher tmux `-t` target is now the exact-match `=name` form: bare `-t`
  prefix-matches, so with `rc-alpha` absent and `rc-alpha-sub` live, alpha's stop()
  C-c'd the sibling and launch() reported "already" (verified against a live tmux;
  found while building the guard — the sibling-prefix class from the kill-scope audit
  pin, one layer down). Pinned by a mutant-verified test.

## 2026-08-17

- Readability pass, no behavior change: one `_tmux()` wrapper now carries the twelve
  captured tmux control calls, so `capture_output=True` stopped outweighing the commands
  in `_spawn`/`stop`/`launch` (`new-session` stays raw — it is the one call whose stderr
  is meant to reach the launcher log); `path[len("/files"):]` became `removeprefix` at
  four sites, with `_files` binding it once instead of recomputing it; `running()` dropped
  its magic `line[3:]`; and `EVENT_STATE` moved up beside `RANK` in rc_state so the two
  vocabulary tables can be diffed by eye. Polish pass on top: `_upload`'s folder guard
  binds `os.path.dirname` once (walrus), and `RANK`/`EVENT_STATE` are `MappingProxyType`
  now — the shared state vocabulary can no longer be mutated across module boundaries,
  which is the drift rc_state exists to prevent, as a runtime property instead of a
  convention. 105/105 tests unchanged throughout.
- Evening-audit Now trio: pinned the kill-scope cwd boundary (a bare-startswith mutant
  survived while ~/projects holds live sibling-prefix pairs the mutant would cross-kill;
  panel-confirmed High) and the lookalike-binary comm check; pinned the ≥400 log's token
  redaction (reverting it kept the suite green — the last unpinned leg of the token
  remediation); rewrote RUNBOOK's two stale "read-only by design" share claims in the
  name-the-hazard form — the second stale invariant caught in that doc in two days, both
  by the half-true survival mechanism.
- Audit Next batch: one `_desk_claude_pids()` generator is now the single definition of
  "desk claude" for both the badge scan and the kill/takeover paths (two verbatim copies
  could drift scopes apart silently); `_settle_prompt` hoists the interactive-prompt
  policy out of `_spawn` as a data table; and seven knob-level test pins landed (git
  timeout requested, PATH tail exact, JS mid-transfer stall cap — the last unguarded
  spin — hook event trio, `_fill` ordering, TTL expiry on both caches, the real-pgrep
  unit-test leak stubbed + `_desk_cache` restored per test).
- Audit paydown (the 2026-08-17 audit's Now batch): pinned `has_desk_thread`'s two
  load-bearing properties (sdk-cli discrimination + the 256KiB read cap — both were
  mutation-deletable with a green suite) and the `/stop?desk=1` route wiring (the desk ✕
  could phantom-"stop" nothing); added `rc_templates` to the coverage floors; synced the
  three token-era doc claims (RUNBOOK's *don't-undo* list literally instructed re-creating
  the removed leak channel), the stale subcommand launch phrasing, and the ✕/desk-badge
  behavior into README/RUNBOOK; backfilled the two missing 2026-08-16 entries below.

## 2026-08-16

- *(backfilled)* Session-location icons on the launcher: 📱 for launcher-run sessions,
  🖥 for desk sessions — reads faster at a glance than the word tags they replaced.
- *(backfilled)* Graceful desk-session close: the ✕ on a desk-badged row SIGTERMs the
  desk claude via `/stop?desk=1` (transcript flushed, relay archived, thread resumable),
  with the scan cache invalidated so the badge clears on the next poll.

- The idle "Claude is waiting for your input" notification no longer paints a session
  amber "waiting" — under bypassPermissions it is nearly the only Notification that
  fires, so every finished session (often just showing a suggested next prompt) looked
  blocked. Idle ping -> idle; permission/question notifications still read waiting.

- Headless launches no longer hang invisibly at interactive prompts: a huge thread now
  makes claude ask "resume from summary or full?" before it registers with the relay, so
  a phone tap read "launched" while the session sat at a prompt nobody could see (hit
  live on a 9h/833k-token bigproj thread — three taps, zero appearances in the app).
  _spawn now auto-answers the resume-cost prompt with "Resume full session as-is"
  (owner's standing preference: never compact, always full resume) and fails loudly
  with the prompt text for any other confirm-style screen.

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
