# TODO

Backlog for remoteclaude, ranked by leverage. Dates are targets, not deadlines.
Grades 2026-08-17 (@55ab16a): architecture B+, code A-, tests B+, process A- (net B+).
Grades 2026-08-08 (@7a530c1, superseded, kept for the trend): arch A-, code A-, tests A-, process B+ (net A-).

When an item is done, **leave it unticked with a `DONE <date>:` note above it** rather than
deleting it — the original wording records what was wrong, the note records the fix. Verify
before you schedule from a stale entry: check the claim still holds, then act.

## Now — target 2026-08-18

> **DONE 2026-08-17** — `test_has_desk_thread_discrimination_and_cap`
> (tests/test_orchestration.py): sdk-cli → False, cli within 256KiB → True, cli after
> byte 262144 → False. Both formerly-green mutations now fail the suite.

- [ ] **`has_desk_thread` has zero direct tests; both load-bearing properties unguarded.**
  rc_launcher.py:374-388 — loosening the entrypoint regex to `"entrypoint":` (phantom-launch
  regression returns) or removing the `fh.read(262144)` cap (525MB slurp returns) both leave
  all 99 tests green. Add the trio in tests/test_orchestration.py: sdk-cli-only transcript →
  False; cli marker inside first 256KiB of a >256KiB file → True; cli marker after byte
  262144 → False. (~15 lines; the audit's one High.)
> **DONE 2026-08-17** — `test_stop_desk_route_sigterms_desk_session` (tests/test_upload.py):
> asserts SIGTERM fired, no SIGKILL, and zero tmux send-keys/kill-session calls.

- [ ] **`/stop?desk=1` route wiring is untested.** rc_launcher.py:881 (coverage-missed) —
  delete the conditional and the desk ✕ falls through to `stop()`, C-c's a nonexistent tmux
  session, reports "stopped" while the desk claude lives; suite stays green. One RouteTest
  case: hit `/stop?proj=X&desk=1`, assert SIGTERM fired and no tmux `C-c`.
> **DONE 2026-08-17** — RUNBOOK:17/87/131/299, README:29/107, android/README:51 all synced
> to the flag-form / desk-badge / token-file reality; CHANGELOG backfilled (icons, desk ✕);
> `rc_templates` added to ci.yml `--source` (measured 100%).

- [ ] **Doc-sync: three token-era claims instruct re-creating the removed leak channel.**
  RUNBOOK.md:299 ("Token in the LaunchAgent env" under *don't undo these*), README.md:107,
  android/README.md:51 ("baked-in token") all describe the pre-remediation design; also
  RUNBOOK.md:17 + README.md:29 still say the subcommand launch form, ✕ semantics lack the
  desk case, and CHANGELOG has no entries for c206cbc (icons) / d1b9918 (desk ✕). One
  ~15-line commit; add `rc_templates` to ci.yml:24 `--source` in the same pass.

## Next — target 2026-08-24

- [ ] **Unify the desk-claude probe chain.** rc_launcher.py:282-298 vs 305-323 — two
  definitions of "what counts as a desk claude" (badge scope vs kill scope), three of four
  filter steps verbatim; converged on independently by two finders. One `_desk_claude_pids()`
  generator yielding (pid, cwd); `desktop_sessions` filters, `_desk_scan` groups. Fold in
  `removeprefix` at :322 and a `_desk_invalidate()` next to the cache (desk_stop:517 does
  tuple surgery inline).
- [ ] **Hoist `_settle_prompt` out of `_spawn`.** rc_launcher.py:405-445 — prompt
  classification/answer is product policy (owner's never-compact preference) embedded in
  process mechanics; two of the last three launcher commits edited exactly that block. Table
  of prompt→action, single caller, behavior-preserving; existing two prompt tests cover it.
- [ ] **Pin the knob batch the mutations walked through.** Each described-green: git timeout
  kwarg never asserted on the mock (tests/test_functions.py:111 vs rc_launcher.py:142); PATH
  tail unpinned (empty-tail mutation green, tests/test_orchestration.py:224); JS mid-transfer
  `stalls++` deletable (rc_upload.js:56 — the one remaining infinite-spin); Stop/SubagentStop/
  SessionStart never pass through the hook (tests/test_hooks.py); `_fill` longest-first sort
  unasserted; TTL expiry untested on both caches; stub `desk_projects` in the page-placeholder
  test (it forks real pgrep mid-unit-run) and add `_desk_cache` to `_harness._ATTRS`.

## Later — audit 2026-08-17, unscheduled

- [ ] **One `status_payload()` for page() and /status.** rc_launcher.py:557-565 vs 859-860 —
  the payload is assembled twice; symptom: GITSTATES is page-load-only, so branch/dirty badges
  freeze during exactly the window a remote turn dirties the tree. Unifying makes "git in the
  poll" a one-line product call (server cache already bounds the cost).
- [ ] **Small-pass batch:** drop the dead `json=1` on the `/create` fetch (rc_templates.py:223
  — the route never reads it); `EVENT_STATE[event]` instead of `.get(event, "working")` in
  rc_state_hook.py:31 (unknown event should crash the hook, not paint green); `rows_html`
  OSError currently renders as "empty" (rc_launcher.py:596 — mislabels permission failures);
  refresh docs/launcher.png (predates icons/desk badges).

## Now — small, high value (target 2026-08-08)

> **DONE 2026-08-08** — `test_git_state_timeout_is_none_not_crash` (tests/test_functions.py):
> a `TimeoutExpired` from `subprocess.run` now asserts `_git_state` returns `None`, so narrowing
> the guard to `except OSError:` fails the suite instead of shipping a page-load 500.

- [ ] **Test the `git status` timeout guard.** `_git_state` (rc_launcher.py:136) catches
  `subprocess.TimeoutExpired`, but nothing exercises it — collapsing the `except` to `OSError`
  alone keeps every test green and lets a hung `git status` (index.lock / slow disk) 500 the
  launcher page. Add a test that monkeypatches `subprocess.run` to raise `TimeoutExpired` and
  asserts `_git_state` returns `None`. (~4 lines.)

> **DONE 2026-08-08** — `android.yml` now has a `pull_request:` trigger mirroring the push
> path filter, so the Kotlin tests + APK build gate PRs, not just post-merge pushes to main.

- [ ] **Gate Android on pull requests.** `.github/workflows/android.yml` triggers only on
  `push` + `workflow_dispatch`, so `testDebugUnitTest` / `assembleDebug` run post-merge, not on
  the PR. Add a `pull_request:` trigger mirroring `ci.yml`. (One line.)

## Next — medium (target 2026-08-15)

> **DONE 2026-08-09** — extracted `rc_claude.py` (CLAUDE + `auth_status()` + MT), imported by
> both the launcher and the watchdog; the `claude auth status` contract now has one definition.
> rc_claude depends on neither module, so the watchdog stays independent of the launcher.

- [ ] **Share the `claude auth status` probe.** `_login_status` (rc_launcher.py:68) and
  `rc_healthcheck.auth_state` (rc_healthcheck.py:30) parse the same JSON independently, and the
  `CLAUDE` path and `MT` tz are duplicated in both. A contract or default-path change silently
  desyncs the launcher badge from the watchdog. Extract a small `rc_claude.py` both import —
  keep it separate from `rc_launcher` so the watchdog stays independent of the launcher.

> **DONE 2026-08-09** — `test_server_handle_error_swallows_conn_noise_reraises_real` asserts a
> `ConnectionResetError` is swallowed and a `ValueError` reaches the traceback path.

- [ ] **Test `Server.handle_error`.** rc_launcher.py:796 — assert a `ConnectionError`/
  `BrokenPipeError` is swallowed and a generic exception still reaches the traceback path, so a
  future broadening of that filter can't silently flood or silence the error log.

> **DONE 2026-08-09** — `page()`/`share_page()` now fill via a single-pass `_fill()` (one regex
> over the known keys), so a project named `__LOGIN__` survives as data instead of being
> rewritten into broken JS. Guarded by `test_page_placeholder_named_project_not_reinterpreted`.

- [ ] **Single-pass templating.** `page()` / `share_page()` fill placeholders with ordered
  `str.replace`, so a project directory named `__LOGIN__` or `__HOST__` gets rewritten by a
  later pass and breaks the page. Replace with one mapping pass (or non-data sentinels).

## Later — low

> **DONE 2026-08-16** — `stat.S_ISDIR` reuse, mixed-case `Zoo.txt` fixture, `_BASE` CSS splice
> (the 3 byte-identical rules only — the long-press blocks are structurally divergent between
> pages and were deliberately left per-page), `coverage.json` gitignored, RUNBOOK traces
> dropped, and the upload wire contract documented in RUNBOOK. All merged after review.

- [ ] `rows_html` calls `os.stat` then `os.path.isdir` on each entry (two syscalls) — reuse
  `stat.S_ISDIR(st.st_mode)` from the first stat.
- [ ] Add a mixed-case fixture so `rows_html`'s `data-n` `.lower()` sort key is actually
  asserted (current fixtures are already lowercase).
- [ ] Splice the long-press gesture and the shared base CSS rules across the two pages, the way
  `_PTR` / `_THEME` already are.
- [ ] gitignore `coverage.json` (written by the coverage gate); drop the stray "~57 projects"
  and machine-codename lines left in RUNBOOK.md.
- [ ] Document the `X-Rc-Offset/Total/Id/Have` resumable-upload protocol in one RUNBOOK section
  — it's implemented three times (Python server, browser JS, Kotlin) with no shared spec.

> **DONE 2026-08-16** — blue "desk" dot + tag on the launcher, fed by a TTL-cached process
> scan (`desk_projects()`, in `/status` and the page). bridge-pointer.json was rejected as
> the signal after live testing: desk sessions don't reliably write one, and stale ones
> point at dead pids. Tooltip says a tap takes the session over.

- [ ] **Show live desk sessions on the launcher page.** The launcher's green dots track only
  its own tmux `rc-*` sessions; a desk-started `claude` (auto-paired, phone-drivable) shows as
  not-running. Scan `~/.claude/projects/*/bridge-pointer.json`, pid-check each, and render a
  distinct "desk" badge — so the page shows where each project is live and that a tap would
  take it over. Cheap (file reads + `os.kill(pid, 0)`), no process sniffing.

## Deferred — needs a decision or new tooling

> **RE-RANKED 2026-08-17** — audit verdict: this deferral is *strengthened* (the share side
> has had zero churn since 7a530c1; you extract where change happens) and is superseded as
> the right Tier-H cut by `rc_sessions.py` — the orchestration cluster is now 337/945 lines,
> absorbed all recent growth, and faces the Handler through eight plain function calls. Hold
> until that cluster stops moving (four commits touched it in two days).

- [ ] Extract the rc-share file server into `rc_files.py`. High-impact: the confinement tests
  inject `SHARE` as a live global, and `js` / `log_event` / `MT` are shared with the launcher.
  Do it as its own change with behavioral-equivalence checks, not folded into other work.
> **DONE 2026-08-16** — `rc_upload.js` (pure resume policy, spliced into the files page) +
> `tests/js/rc_upload.test.cjs` under `node --test`, wired into CI. Fixed the four client
> bugs found by review along the way. The sort comparator stays in-page (visual-only risk).

- [ ] Add a JS test harness (Node) for the client upload-resume and sort logic, currently
  untested by design.

> **DONE 2026-08-16** — CHANGELOG.md started during the token remediation.

- [ ] Add `CHANGELOG.md`.
