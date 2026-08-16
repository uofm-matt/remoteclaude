# TODO

Backlog for remoteclaude, ranked by leverage. Dates are targets, not deadlines.

When an item is done, **leave it unticked with a `DONE <date>:` note above it** rather than
deleting it — the original wording records what was wrong, the note records the fix. Verify
before you schedule from a stale entry: check the claim still holds, then act.

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

- [ ] **Show live desk sessions on the launcher page.** The launcher's green dots track only
  its own tmux `rc-*` sessions; a desk-started `claude` (auto-paired, phone-drivable) shows as
  not-running. Scan `~/.claude/projects/*/bridge-pointer.json`, pid-check each, and render a
  distinct "desk" badge — so the page shows where each project is live and that a tap would
  take it over. Cheap (file reads + `os.kill(pid, 0)`), no process sniffing.

## Deferred — needs a decision or new tooling

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
