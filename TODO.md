# TODO

Backlog for remoteclaude, ranked by leverage. Dates are targets, not deadlines.

When an item is done, **leave it unticked with a `DONE <date>:` note above it** rather than
deleting it — the original wording records what was wrong, the note records the fix. Verify
before you schedule from a stale entry: check the claim still holds, then act.

## Now — small, high value (target 2026-08-08)

- [ ] **Test the `git status` timeout guard.** `_git_state` (rc_launcher.py:136) catches
  `subprocess.TimeoutExpired`, but nothing exercises it — collapsing the `except` to `OSError`
  alone keeps every test green and lets a hung `git status` (index.lock / slow disk) 500 the
  launcher page. Add a test that monkeypatches `subprocess.run` to raise `TimeoutExpired` and
  asserts `_git_state` returns `None`. (~4 lines.)
- [ ] **Gate Android on pull requests.** `.github/workflows/android.yml` triggers only on
  `push` + `workflow_dispatch`, so `testDebugUnitTest` / `assembleDebug` run post-merge, not on
  the PR. Add a `pull_request:` trigger mirroring `ci.yml`. (One line.)

## Next — medium (target 2026-08-15)

- [ ] **Share the `claude auth status` probe.** `_login_status` (rc_launcher.py:68) and
  `rc_healthcheck.auth_state` (rc_healthcheck.py:30) parse the same JSON independently, and the
  `CLAUDE` path and `MT` tz are duplicated in both. A contract or default-path change silently
  desyncs the launcher badge from the watchdog. Extract a small `rc_claude.py` both import —
  keep it separate from `rc_launcher` so the watchdog stays independent of the launcher.
- [ ] **Test `Server.handle_error`.** rc_launcher.py:796 — assert a `ConnectionError`/
  `BrokenPipeError` is swallowed and a generic exception still reaches the traceback path, so a
  future broadening of that filter can't silently flood or silence the error log.
- [ ] **Single-pass templating.** `page()` / `share_page()` fill placeholders with ordered
  `str.replace`, so a project directory named `__LOGIN__` or `__HOST__` gets rewritten by a
  later pass and breaks the page. Replace with one mapping pass (or non-data sentinels).

## Later — low

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

## Deferred — needs a decision or new tooling

- [ ] Extract the rc-share file server into `rc_files.py`. High-impact: the confinement tests
  inject `SHARE` as a live global, and `js` / `log_event` / `MT` are shared with the launcher.
  Do it as its own change with behavioral-equivalence checks, not folded into other work.
- [ ] Add a JS test harness (Node) for the client upload-resume and sort logic, currently
  untested by design.
- [ ] Add `CHANGELOG.md`.
