# TODO

Backlog for remoteclaude, ranked by leverage. Dates are targets, not deadlines.
Grades 2026-09-06 (@fb796f9, audit): architecture A, code A, tests A-, process A- (net A-). Process ticked down: this session shipped the reload/version fix + the disk/liveness watchdog but the operator-facing docs and the size_cap enforcement lagged the code.
Grades 2026-09-05 (@414ee97, superseded): architecture A, code A, tests A-, process A (net A-).
Grades 2026-09-02 (@3256853, superseded): architecture A-, code A, tests B+, process A- (net A-).
Grades 2026-08-17 evening (@87c58bf, superseded): architecture A-, code A-, tests A-, process A- (net A-).
Grades 2026-08-17 morning (@55ab16a, superseded): arch B+, code A-, tests B+, process A- (net B+).

When an item is done, **leave it unticked with a `DONE <date>:` note above it** rather than
deleting it — the original wording records what was wrong, the note records the fix. Verify
before you schedule from a stale entry: check the claim still holds, then act.

## Now — audit 2026-09-06, target 2026-09-09

- [ ] **The reload fix is undocumented — invisible where it's needed.** RUNBOOK.md (Verify
  :300, Troubleshooting :310) and README.md (0 mentions) never tell the operator to run
  `./install.sh --reload` after editing `rc_*.py`, or to `curl :8787/version` to confirm the
  build went live. The stale-serve footgun this session fixed (install.sh:22-33, /version)
  is unreachable by a reader hitting it. **Cost of delay: GROWING** — every uninformed
  edit-and-reload hits the exact wall the fix removes. Doc-only. (~10 lines across two files.)
- [ ] **RUNBOOK/README describe the watchdog as login-only; it now watches disk + liveness.**
  RUNBOOK.md:43,:69 and README.md:95 say "runs claude auth status … notifies if the login
  lapses"; rc_healthcheck also runs `check_disk` (<5 GiB) and `check_launcher` (GET /version).
  RUNBOOK.md:315 still shows the log as `login=ok …`; it is now `login=ok build=<stamp> …`.
  Half-true, not inverted — update the three component descriptions and the log sample.

## Now — audit 2026-09-05, target 2026-09-08

> **DONE 2026-09-05** — logic extracted to `rc_download.js` (size-cap decision, progress text, hum) with `node --test` pinning the 256 MB boundary (deleting it now goes red); the files page calls `downloadMode()`/`progressText()`. Panel then hardened it: unknown Content-Length -> native (no unbounded buffer).

- [ ] **Browser `download()` has no behavioral test — the one medium gap, and it is on
  code shipped this session.** `rc_files_page.py:65-83` — the fetch + progress + Blob +
  `>256 MB` hand-off + catch is covered only by `node --check` (parse validity) and
  substring presence (`window.rcDownloadDone=function`, `URL.createObjectURL`). Deleting the
  256 MB threshold leaves every test green — a performance guard with no test asserting what
  it costs. Contrast `resumeUpload` (12 node tests) and `DownloadLogic.kt` (exact-string
  tests). Extract the pure slice (offset/threshold/blob decision) the way `rc_upload.js` was
  and give it `node --test` cases. **Cost of delay: GROWS** — new code on an active surface
  that silently breaks while the parse/substring tests stay green. (~30 lines, via /gate.)

## Now — audit 2026-09-02, target 2026-09-05

> **DONE 2026-09-04** — `test_resume_below_have_truncates_the_stale_tail` (have=450 not 500), `connection: close` pinned on the write-error 200 and the 403 PUT, `test_put_to_escaping_path_is_404` (the PUT path answers 403 "bad target"); truncate and `offset>have` mutants both killed on the real tree.

- [ ] **Resumed-upload truncate is unpinned — a stale tail reads as stored.** rc_launcher.py:834
  `f.truncate(offset)` deletable green, and `offset > have` → `!=` at :823 green. Reproduced on
  the real code: PUT 500 bytes, re-PUT 50 at offset 400 → real `have=450`, mutant `have=500`.
  One 3-request test in tests/test_upload.py that PUTs below `have` and asserts the final size.
  Same batch: assert `connection: close` on the write-error 200 (:842) and the 403 PUT (:916),
  which the `_guard_body` GET tests already do; and one handler-level PUT to an escaping
  path (`/files/../x`, `/files/esc/x` via a symlink out of the share) asserting 404 —
  `share_target()` is pinned against five escape forms in tests/test_confinement.py:29,
  the PUT call site at rc_launcher.py:781 is not. Panel-ranked first: the only
  demonstrated silent data corruption in the audit. Note gemini's proposed fix (reject
  `offset < have`) is a design change, not this pin: a lower offset is the documented
  restart path the clients rely on. (~35 lines.)
> **DONE 2026-09-04** — fresh launch with a live desk claude kills nothing; `RC_TAKEOVER=0` on resume kills nothing; the SIGTERM grace loop must sleep before SIGKILL; `ensure_trusted` lands the flag; `RC_PROJECT=proj` in `-e`. All five mutants killed (source md5-restored, bytecode purged).

- [ ] **Four load-bearing launcher paths are mutation-deletable with 127/127 green.** 54 targeted
  mutants on shadow copies: 17 red, 36 green, 1 hang. The ones that matter: rc_launcher.py:502
  takeover guard (drop `resuming` or `TAKEOVER` — a phone tap that SIGTERMs desk sessions on
  every fresh launch passes), :362-364 SIGTERM grace loop (immediate SIGKILL passes;
  `test_takeover_sigkills_straggler` builds a tick clock and never asserts it advanced), :474
  `ensure_trusted` call site (the untrusted-dir silent fail the launcher exists to prevent),
  :486 `RC_PROJECT` in the session env (the hook and badge key on it). Pins: a live
  pgrep/cwd fixture on `test_launch_fresh_success` + a `TAKEOVER=False` case asserting
  `killed == []`; assert ≥3 ticks before SIGKILL; assert `CLAUDE_JSON` gains
  `hasTrustDialogAccepted` after `launch()`; assert `RC_PROJECT=proj` in `-e` opts. (~40 lines.)
> **DONE 2026-09-04** — `_read_token()` returns "" without the file and main() refuses to start; `grep RC_LAUNCHER_TOKEN rc_*.py` → only the `_FILE` variant remains.

- [ ] **Delete the `RC_LAUNCHER_TOKEN` env fallback.** rc_launcher.py:54 — `_read_token()`
  still accepts the secret from the environment when the 0600 file is unreadable, while
  RUNBOOK:347 says the token lives nowhere else. Panel (3 of 3 models) rates this High: an
  env-carried token is readable via `launchctl print`/`ps`/`/proc/environ` and inherited by
  every child of the HTTP process — the exact channel the 2026-08-16 remediation closed.
  Audit's own read: latent, not live — install.sh never sets it and `launchctl print` on
  this host carries no token — logged at the panel's severity per the dissent rule. Fix:
  drop the fallback (2 lines; the env-covered line at :54 is host-dependent in coverage
  anyway) and let a missing file fail loudly at :959.
> **DONE 2026-09-04** — stderr-isatty half runs under `_cwd("/parent/alpha")` with a live session; the stale-PWD case uses `parent/other`; the JS 'gives up' test carries the onRetry tripwire.

- [ ] **Two guard tests and one JS test are hollow.** tests/test_guard.py:141 stderr-isatty half
  never patches cwd (PROCEED regardless — run under `_cwd("/parent/alpha")` with
  `has_session_rcs=[0]`); :120-122 "stale $PWD ignored" passes with `_same_dir` deleted (use a
  stale PWD of `parent/other-project`); tests/js/rc_upload.test.cjs:118 'gives up' lacks the
  throwing `onRetry` tripwire its sibling at :95 uses, so deleting `lastHave` (rc_upload.js:52-53)
  hangs node 240s instead of failing. (~15 lines.)
> **DONE 2026-09-04** — `serve_forever(poll_interval=0.05)` in `_harness.serve`; suite 20s → ~4s (`Ran 140 tests in 4.2s`).

- [ ] **Suite spends 16.5 of 18.4s in `srv.shutdown()`.** tests/_harness.py:36 — 33 server-backed
  tests each pay `serve_forever`'s default 0.5s poll interval. `srv.serve_forever(poll_interval=0.05)`
  → ~2s suite. (1 line; verify with `time python3 -m unittest discover -s tests`.)
> **DONE 2026-09-04** — Linux takeover reworded to "untested there"; install.sh writes `RC_SPAWN`/`RC_SNAPSHOT` into the plist/unit; pins re-verified against 2.1.258 with the subcommand caveat; retired wording, rc_guard.sh cross-ref, gradle placeholder, CHANGELOG backfill all done.

- [ ] **One inverted and one half-true RUNBOOK claim, both instructions.** RUNBOOK.md:149-150
  says Linux takeover "degrades to a no-op" while rc_launcher.py:272-277 reads `/proc/<pid>/cwd`
  — never true of the code, only untested: reword to "untested on Linux". RUNBOOK.md:342 "flip
  `RC_SPAWN=worktree` in the plist": install.sh:90-100 never writes `RC_SPAWN` (or `RC_SNAPSHOT`,
  RUNBOOK:211) into the plist/unit, so the hand edit is overwritten by the next `./install.sh` —
  add both to the installer's env block like RESUME/TAKEOVER. Same pass: version pins (:45/:371
  v2.1.169, :376 v2.1.195, installed 2.1.258); retired "read-only" wording in install.sh:48 and
  the rc_launcher.py:749 docstring; rc_guard.sh:8 cites the old RUNBOOK section title;
  CHANGELOG backfill for f364ea8 and 8e989a7;
  android/app/build.gradle.kts:21 `192.168.1.100` placeholder in a public repo → a non-subnet
  host. (~12 one-line edits.)

> **DONE 2026-09-04** — all shipped except the screenshot: `json=1` dropped, `EVENT_STATE[event]` fail-loud (+test), `rows_html` → "unreadable" (pin flipped), `stopSess` honest toasts (idle / failed), `create()` `FileExistsError`, `/launch?desk=1` pinned. docs/launcher.png still needs a phone screenshot — carried in Later.

- [ ] **Promoted from Later (panel: cost-of-delay ranking was backwards).** The two
  "Small-pass" batches below (all six claims re-verified TRUE this audit, ~8 fixes of 1-3
  lines each): dead `json=1` on the /create fetch; `EVENT_STATE[event]` fail-loud in the
  hook; `rows_html` OSError mislabel + its pin at tests/test_upload.py:339; `stopSess` toast
  honesty; `create()` `FileExistsError` → ("exists", None); the desk-conditional
  `/launch?desk=1` half-pin; refresh docs/launcher.png. Ship with the docs batch above.
## Next — audit 2026-09-02, target 2026-09-12

- [ ] **DONE 2026-09-04 — `rc_tmux.py` leaf, then the config leaf, then
  `status_payload()`, then the `rc_sessions.py` cut — in that order.** Landed as specified,
  plus `rc_share.py` and the rc_templates/rc_page/rc_files_page split; test migration rode
  along. Left open on purpose: the launcher's `stop()` still reports "stopped" without
  confirming — adopting `rc_tmux.graceful_stop()` there is the desk-✕ toast product change,
  tracked separately below. Original entry: The guard added a second graceful-stop
  (rc_guard.py:151-163 polls `has-session` and refuses if alive; rc_launcher.py:523-526 sleeps 2s
  and always says "stopped"), a second `RC_TMUX_BIN` default ("tmux" vs "/opt/homebrew/bin/tmux",
  rc_guard.py:24 / rc_launcher.py:44), 5 raw tmux `subprocess.run`s beside the launcher's
  `_tmux()`, and `rc-{proj}` at 5 sites in 3 files: an `rc_tmux.py` (bin, `_tmux`,
  `session_name`, `has_session`, `graceful_stop`) has two real consumers today and gives `stop()`
  the confirm the desk-✕ toast item needs. The `rc_sessions.py` cut needs a config leaf first:
  `launch()` reads `SHARE` (:487) and both clusters share `log_event`/`projects`/`NAME_RE` + 14
  env globals (:43-70), so extracting the cluster while importing `SHARE` creates the repo's
  only cycle. While in there: realpath `PARENT` (:43) the way `SHARE` is (:66) — desk scan and
  takeover compare physical cwds against an un-normalized parent; fold the three TTL caches
  (:81-89, :132-168, :315-350) into one decorator inside the `status_payload()` pass. Test
  migration is 125 `rc_launcher.X` refs over 31 names in test_orchestration + 78 in
  test_functions + `_harness._ATTRS`. The Deferred hold condition (cluster stops moving) has been
  met since 2026-08-17: one +3-line commit in 16 days.
- [ ] **DONE 2026-09-04 — Test-suite duplication.** The six inline desk responders were
  already one `respond()`; this pass took the rest — `ServerCase` and `MockedToolsCase` in
  `tests/_harness.py` now carry the server fixture and the mocked-tools setUp that four
  files were about to copy. Original entry: tests/test_orchestration.py:113-128, 137-145, 184-195, 211-226,
  414-422, 443-460 — six inline `run(cmd, **kw)` closures re-implementing the same
  pgrep/comm/command/-Fn desk-claude responder (~70 lines; pylint's duplicate-code misses them
  because each differs by a line). One `_desk_claude(pids, root)` responder that plugs into
  `self.responses` (which already accepts callables). Same pass: the `share = mkdtemp` +
  rmtree setUp ×5 and the `LIVE_SPAWN` responses dict ×6 into `_harness`. (~-60 lines.)
- [x] **DONE (2026-09-02 for the eight sites, 2026-09-04 for the rest).** `_json_error`,
  `_is_files` and the `match` landed then; the /launch|/stop and /create arms are now
  `_session_verb`/`_create` and the status payload is assembled once in
  `rc_sessions.status_payload()`. Original entry: rc_launcher.py — 7× `self._send(<code>, b'{"error":...}', json)`
  (809-898), 4× the `/files` path predicate (876, 917, 926, 938), 4× the
  urlparse/parse_qs/_authed preamble, 2× the status payload dict (885-887, 906-908):
  `_json_error`, `_is_files`, `_query` (~20 lines net). `do_GET`'s 6-branch ladder → `match`.
> **DONE 2026-09-04** — `rc_state_hook.py --hook-command <repo>` is the one source; both scripts read it via `RC_HOOK_CMD`; `test_hook_command_is_the_single_source` asserts no copy reappears in either script.

- [ ] **install.sh:55-73 / uninstall.sh:34-52 embed the same Python.** Same settings.json path
  and hook command string in both; if the string changes in one, uninstall stops matching.
  Either a tiny `rc_hook.py --install|--remove` both call, or a shared shell variable for the
  command string. (~20 lines.)

## Decisions — operator's call, not backlog

> **DECIDED 2026-09-04** — adopted: whole repo formatted (13 files, every tracked module
> AST-identical before/after), `ruff format --check .` added to ci.yml.

- **`ruff format` gate.** 12 of 22 .py files (6 of 8 shipped modules) would be reformatted;
  format is not in CI and the local style says `ruff format`. Either add `ruff format --check .`
  to ci.yml after one whitespace-only commit (routed through /gate: it touches 12 files), or
  drop the local discipline. Not doing either leaves the drift growing silently.
> **DECIDED 2026-09-02, applied 2026-09-04** — tracked: `.gitignore` un-ignores
> `.claude/refutations.md` alone (review.toml stays private); the record is claims plus the
> measurements that killed them, public-safe.

- **Where the refutation record lives.** `refute.py` writes `.claude/refutations.md`, which
  this public repo gitignores to keep the panel address out of the tree, so the record cannot
  travel with the code. A tracked path publishes what was measured and refuted; a per-repo
  setting keeps that a decision. The codereview window has filed it and is waiting on you.

## Now — audit 2026-08-17 evening, target 2026-08-18

> **DONE 2026-08-17** — the desktop_sessions fixture gained pid 444 (cwd = the sibling
> `projx`, excluded by the boundary guard) and pid 555 (comm `claude-helper`, excluded by
> the equality check); both formerly-surviving mutants now fail the test.

- [ ] **The kill-scope sibling-prefix guard is untested.** rc_launcher.py:304-305 — mutating
  `cwd == root or cwd.startswith(root + os.sep)` to bare `startswith(root)` survives all 104
  tests, and ~/projects holds live sibling-prefix pairs (alpha/alpha-sub,
  beta/beta-sub) the mutant would cross-kill. Add to the desktop_sessions fixture: a
  pid whose cwd is `root + "x"` asserted excluded, and a `claude-helper` comm asserted
  excluded (the comm equality check is loosenable the same way). (~8 lines.)
> **DONE 2026-08-17** — `test_rejected_request_log_omits_query_token` (tests/test_upload.py):
> a 4xx with `?token=` asserts the path is logged and the credential is not; reverting the
> redaction now fails the suite.

- [ ] **Pin the ≥400 log redaction.** rc_launcher.py:708-711 — reverting
  `urlparse(self.path).path` to `self.path` keeps all 104 green; the app's uploads carry
  `?token=`, so that revert re-opens the log leak. 4-line test: fire a 4xx with `?token=`,
  assert the token absent from the captured log line. The last unpinned leg of the token
  remediation.
> **DONE 2026-08-17** — both passages rewritten in the name-the-hazard form ("do not
> 'restore' a read-only HTTP share: uploads are load-bearing for the share-target");
> retired-wording sweep (`git grep -in 'read-only' -- '*.md'`) now hits only the warning
> itself and this entry's preserved wording. The global CLAUDE.md echo remains the
> operator's to sync.

- [ ] **RUNBOOK still calls the share "read-only by design" — twice.** RUNBOOK.md:158, :176 —
  upload/delete shipped long ago and the same file documents the upload protocol at :196.
  A stale by-design sentence instructs deleting the upload path. Rewrite both naming what
  the design defends (HTTP read+upload, confined; SMB optional), then `git grep -in` the
  retired wording across every doc. The operator's global CLAUDE.md rc-share note carries
  the same retired phrasing (his call to sync).

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

> **DONE 2026-08-17** — `_desk_claude_pids()` generator is the single desk-claude
> definition; `desktop_sessions` filters it, `_desk_scan` groups it (`removeprefix`
> folded in); `_desk_invalidate()` replaces desk_stop's inline tuple surgery.

- [ ] **Unify the desk-claude probe chain.** rc_launcher.py:282-298 vs 305-323 — two
  definitions of "what counts as a desk claude" (badge scope vs kill scope), three of four
  filter steps verbatim; converged on independently by two finders. One `_desk_claude_pids()`
  generator yielding (pid, cwd); `desktop_sessions` filters, `_desk_scan` groups. Fold in
  `removeprefix` at :322 and a `_desk_invalidate()` next to the cache (desk_stop:517 does
  tuple surgery inline).
> **DONE 2026-08-17** — `_settle_prompt(sess, proj)` + the `_PROMPT_ANSWERS` table;
> `_spawn` is back to mechanics (spawn, liveness, cleanup). Behavior-preserved under the
> two existing prompt tests.

- [ ] **Hoist `_settle_prompt` out of `_spawn`.** rc_launcher.py:405-445 — prompt
  classification/answer is product policy (owner's never-compact preference) embedded in
  process mechanics; two of the last three launcher commits edited exactly that block. Table
  of prompt→action, single caller, behavior-preserving; existing two prompt tests cover it.
> **DONE 2026-08-17** — all seven pinned: timeout kwarg asserted on the mock; PATH
> exact-equality (prefix + tail); JS `stalls++` case (12th node test, onRetry throws
> before a spin can hang the runner); Stop/SubagentStop/SessionStart walk; `_fill`
> prefix-pair test; TTL-expiry tests for both caches; `desk_projects` stubbed in the
> page test and `_desk_cache` added to `_harness._ATTRS`.

- [ ] **Pin the knob batch the mutations walked through.** Each described-green: git timeout
  kwarg never asserted on the mock (tests/test_functions.py:111 vs rc_launcher.py:142); PATH
  tail unpinned (empty-tail mutation green, tests/test_orchestration.py:224); JS mid-transfer
  `stalls++` deletable (rc_upload.js:56 — the one remaining infinite-spin); Stop/SubagentStop/
  SessionStart never pass through the hook (tests/test_hooks.py); `_fill` longest-first sort
  unasserted; TTL expiry untested on both caches; stub `desk_projects` in the page-placeholder
  test (it forks real pgrep mid-unit-run) and add `_desk_cache` to `_harness._ATTRS`.

## Next — audit 2026-08-17 evening, target 2026-08-24

> **RE-RANKED 2026-09-02** — carried into the Next entry above with two prerequisites the
> audit found (an `rc_tmux.py` leaf and a config leaf to avoid the `SHARE` import cycle);
> refs now page():574-584 / /status:878-880, file 965 lines.

> **DONE 2026-09-04** — landed in the a3d5576 cut plus this batch: `status_payload()` feeds page() and /status, git is in the poll, `stop()` confirms via `graceful_stop`.

- [ ] **`status_payload()` then the `rc_sessions.py` cut, in that order.** The HOLD is
  lifted: the orchestration cluster's feature wave is complete (371/962 lines, eight plain
  Handler-facing calls, made *more* cuttable by `_desk_claude_pids`/`_settle_prompt`).
  Land one `status_payload()` feeding both `page()` (rc_launcher.py:572) and `/status`
  (:876) first — it redraws exactly the seam the cut formalizes, and makes git-in-the-poll
  a one-liner (badges currently freeze between page loads) — then take the Tier-H
  extraction so the seam moves once. Test migration rides along (~430 lines of
  test_orchestration reference module functions).

## Later — unscheduled

- [ ] **Multiroot availability + desk-cwd realpath (from the 2026-09-06 add-roots work).**
  `rc_config.extra_roots()` runs on every /status poll with no timeout, so an added root on a
  mount that dies AFTER add wedges the ThreadingHTTPServer poll thread (same class as the
  primary `os.listdir(PARENT)`; add-time probes readability once). If it bites, wrap
  `extra_roots`/`projects` in a short TTL cache like the desk scan. Also: `_desk_scan` compares
  an lsof/proc cwd to realpath'd roots, so a non-realpath'd macOS cwd (`/tmp` vs `/private/tmp`)
  can miss its root prefix and drop the desk badge (display-only). (Defect pass + panel, low.)
- [ ] **Nested-projects follow-ups (Phase 1 landed opt-in 2026-09-06).** Grouped UI section
  headers on the launcher page (per-`group/` sections, not one flat list — the plan's optional
  item 6); and creating a project *into* a group from the phone (`create()` is flat-only today,
  a name in `GROUPS` is rejected). Both are additive; the flat list + filter already work on
  `group/name` strings. (From building REORG-PLAN Phase 1.)
> **DONE 2026-09-05** — switched to `time.monotonic()` (deadline + loop); the test and `_harness._STDLIB` now patch/restore `time.monotonic`.

- [ ] **`rc_desk.takeover()` times its SIGTERM grace with `time.time()`; `rc_tmux.graceful_stop()`
  uses `time.monotonic()`.** rc_desk.py — a wall-clock step (NTP, DST) can shorten or stretch
  the 5s before SIGKILL. Switching is a behavior change with two test hooks on `time.time`
  (`test_takeover_sigkills_straggler`, `_harness._STDLIB`), so it is not a quality-pass edit.
  Fix: `monotonic` + re-point the two hooks. (/optimize finding, 2026-09-04.)
> **DONE 2026-09-05** — pruned per-key after each fill; and invalidate() now bumps a generation (a pre-invalidate in-flight fill is dropped) instead of clearing _inflight — the panel showed the wholesale clear caused last-writer-wins. Threaded test kills both mutants.

- [ ] **`TTLCache._inflight` is never pruned** — one `Lock` per distinct argument tuple for the
  process lifetime. rc_config.py. Bounded by the project count in practice (keys are project
  names or `()`), so a note, not a leak; prune on `invalidate()` if a keyed cache ever grows.
> **DONE 2026-09-05** — `test_parent_resolves_a_symlinked_projects_parent_to_physical` drives a fresh import under a symlinked parent and asserts PARENT resolved to the physical dir; goes red if the realpath at rc_config.py is dropped (the first version was a tautology, caught by the defect pass).

- [ ] **`PARENT` is realpath'd since the cut; a symlinked `RC_PROJECTS_PARENT` is untested.**
  rc_config.py — `ensure_trusted`'s `~/.claude.json` key, `has_desk_thread`'s transcript slug
  and tmux `-c` all now use the physical path (claude keys by physical getcwd, so probably
  right). Not live on this host. Test: symlinked PARENT → trust key equals the physical path
  and a transcript under the physical slug is found. (Defect-pass lead, 2026-09-04.)
- [ ] **Refresh docs/launcher.png.** Predates the session icons and desk badges (2026-08-16);
  needs a phone screenshot of the current page dropped into `docs/`. Not doable from a
  session.
- [ ] **`rc_config.py:82` crashes at import on an empty `RC_LAUNCHER_PORT`.**
  `int(os.environ.get("RC_LAUNCHER_PORT", "8787"))` → `ValueError` on a set-but-empty env
  (a plist/unit `Environment=RC_LAUNCHER_PORT=`), before the token check, bare traceback.
  rc_healthcheck.py:33 already uses the safe `... or "8787"` form; align rc_config. Verified
  live: `RC_LAUNCHER_PORT="" python3 -c "import rc_config"` raises. 1-char fix, via /gate.
  (Audit 2026-09-06.)
- [ ] **`MIN_FREE_GB` (the one number the watchdog enforces) is unpinned.** rc_healthcheck.py:70
  — `test_check_disk_alerts_below_floor_only` probes 1 GiB and 50 GiB, so any floor in (1,50]
  passes; `5.0 → 2.0` survives. Add a case straddling 5.0 (4.9 alerts, 5.1 quiet), which also
  pins the `<` vs `<=` boundary. Cheap; mutation-verified gap. (Audit 2026-09-06.)
- [ ] **`size_cap` is honor-system and already violated.** `.claude/review.toml:5` declares 350;
  `rc_sessions.py` is 360 (10 over, from the 2026-09-06 ensure_trusted concurrency fix).
  Nothing enforces it — no CI step, no test (grep of .github/tests/pyproject → none); the cap
  only bites when /gate runs on a diff *touching* the file. Architecture verdict is NOT to
  split (the view/lifecycle halves are the read/write faces of one domain): bump `size_cap`
  to 375 with a one-line note, and add a CI/test check over `git ls-files '*.py'` so the cap
  actually bites. (Audit 2026-09-06.)
- [ ] **`_build_stamp` docstring over-claims "the launcher's own source".** rc_config.py:66 —
  it hashes all 16 `rc_*.py` but the launcher imports 12, so editing rc_guard/rc_healthcheck/
  rc_state_hook/rc_status advances `/version` while the running launcher is byte-unchanged.
  Harmless for reload-verification (you reload after editing any shipped file); reword to
  "the shipped rc_*.py bundle." Doc-only. (Audit 2026-09-06.)
- [ ] **Doc module maps omit the JS assets and two leaves.** rc_download.js/rc_upload.js
  (tracked, node-tested) are in neither README Components (:84-97) nor the RUNBOOK map
  (:55-69); rc_claude.py/rc_state.py are in the RUNBOOK map but not the README table. Add
  them. (Audit 2026-09-06.)
- [ ] **`ensure_trusted` mkstemp/os.replace atomicity is untested.** rc_sessions.py — replacing
  the unique tempfile with a fixed-name open survives the whole suite; the concurrent-tear
  property the 2026-09-06 fix defends has no test (the success-path os.replace IS pinned).
  Genuinely hard without threading two first-time-trust writers; note it or write a threaded
  test. Low-med. (Audit 2026-09-06.)
- [ ] **install.sh core is nearly untested.** Only the hook-command single-source is checked
  (test_hooks.py:115); the plist/unit generation, the settings.json JSON mutation, and
  `--reload` have no test and there is no shell test harness. install.sh is a high_impact
  path that mutates ~/.claude/settings.json. Mitigated by idempotency + manual runs. Low.
  (Audit 2026-09-06.)
- [ ] **No typecheck gate in CI.** ci.yml runs format/lint(F,E9)/bandit/tests/JS but no
  ty/mypy/pyright despite PEP-484 hints throughout (`ty check` passes locally). Likely a
  deliberate stdlib-only choice — either add `uv run ty check` to ci.yml or record the
  decision not to. Low. (Audit 2026-09-06.)
- [ ] **install.sh/uninstall.sh still duplicate the settings.json add/remove Python.** The
  hook-command STRING is single-sourced now (the load-bearing half of the closed TODO:141),
  but the JSON mutation blocks (install.sh:73-90 vs uninstall.sh:36-53) remain two copies.
  Residual; low. (Audit 2026-09-06.)
> **DONE 2026-09-05** — test_orchestration gains a marker-TRAILING case and a `credential` case; both mutants (filter deletion, keyword) killed.

- [ ] **`death_reason()`'s "Pane is dead" filter is unpinned.** `rc_sessions.py` `death_reason`,
  test_orchestration.py:104 — deleting `and not s.startswith("Pane is dead")` stays green
  because the only multi-line fixture puts the marker on the FIRST line, so reversal finds the
  real error anyway. The filter's real job (skip the tmux marker when it TRAILS the error) is
  untested; the mutant reports "Pane is dead" as the failure reason to the phone. Add a case
  with the marker as the last non-empty line. Cost: flat (a wrong diagnostic on a rare failed
  launch). (~3 lines, via /gate. Audit 2026-09-05.)
> **DONE 2026-09-05** — `test_offset_beyond_total_is_bad_total_400` pins `_upload`'s total<offset (400 "bad total"); the `credential` keyword is pinned by the death_reason case above.

- [ ] **Two defensive clauses unpinned.** `_upload`'s `total < offset` guard (`rc_launcher.py`
  — no test sends offset>total) and `death_reason`'s `"credential"` login-classification
  keyword (no test exercises it). Both harmless if unpinned; one assertion each freezes them.
  Cost: flat. (Audit 2026-09-05.)
> **DONE 2026-09-05** — `cfg.project_dir(proj)` at all 7 sites (read at call time, so test PARENT-redirection still reaches every caller); defect pass confirmed no `from rc_config import`.

- [ ] **`os.path.join(cfg.PARENT, proj)` recurs 7-8× across rc_git/rc_desk/rc_sessions/rc_config.**
  A `cfg.project_dir(proj)` one-liner (read at call time, not imported) names the concept once.
  /optimize's refactor pass declined this as taste on 2026-09-04 and it is genuinely low value
  (`PARENT` is one realpath'd constant, so drift risk is near zero) — carried as the operator's
  call, not a defect. (Audit 2026-09-05.)
> **DONE 2026-09-05** — each now says why (systemUiVisibility needs androidx; startActivityForResult needs AppCompat; pre-33 has no typed getParcelableExtra). Android CI green.

- [ ] **Four Kotlin `@Suppress("DEPRECATION")` lack a why-comment.** MainActivity.kt:61,83 and
  UploadActivity.kt:187,192 — each is the pre-Tiramisu `else` branch of an SDK-version check
  around the untyped `getParcelableExtra`; correct and narrowly scoped, just uncommented. One
  line each. Cost: flat. (Audit 2026-09-05.)

## Later — audit 2026-08-17, unscheduled

> **RE-RANKED 2026-08-17 evening** — promoted into the Next entry above (sequenced before
> the rc_sessions.py cut).

> **DONE 2026-09-04** — rc_sessions.status_payload(); /status carries `git` and the page updates `GITSTATES` on every poll.

- [ ] **One `status_payload()` for page() and /status.** rc_launcher.py:557-565 vs 859-860 —
  the payload is assembled twice; symptom: GITSTATES is page-load-only, so branch/dirty badges
  freeze during exactly the window a remote turn dirties the tree. Unifying makes "git in the
  poll" a one-line product call (server cache already bounds the cost).
> **RE-RANKED 2026-09-02** — promoted into the Now batch above (panel dissent: verified
> defects were ranked below doc nits).
> *refs as of 2026-09-02: all four claims re-verified TRUE; rows_html OSError is now
> rc_launcher.py:615-616, its pin tests/test_upload.py:339; launcher.png still 2026-07-17.*

> **DONE 2026-09-04** — all four shipped 2026-09-04 (json=1, `EVENT_STATE[event]`, `rows_html` "unreadable" + pin, launcher.png carried).

- [ ] **Small-pass batch:** drop the dead `json=1` on the `/create` fetch (rc_templates.py:223
  — the route never reads it); `EVENT_STATE[event]` instead of `.get(event, "working")` in
  rc_state_hook.py:31 (unknown event should crash the hook, not paint green); `rows_html`
  OSError currently renders as "empty" (rc_launcher.py:596 — mislabels permission failures;
  NOTE: tests/test_upload.py:326 currently *pins* the mislabeling — update that assertion in
  the same commit); refresh docs/launcher.png (predates icons/desk badges).
> **RE-RANKED 2026-09-02** — promoted into the Now batch above.
> *refs as of 2026-09-02: all re-verified TRUE; the v2.1.195 line is now RUNBOOK:376;
> `grep FileExistsError rc_launcher.py tests/` still empty.*

> **DONE 2026-09-04** — all shipped 2026-09-04 (honest desk-✕ toast, `/launch?desk=1` pin, `create()` `FileExistsError`, RUNBOOK version pins).

- [ ] **Small-pass additions (2026-08-17 evening):** desk-✕ toast honesty — `stopSess`
  discards `desk_stop`'s `"idle"` status and toasts "closed" for a claude that already
  exited (rc_templates.py:210, 2 lines); pin the `u.path == "/stop"` half of the desk
  conditional (a `/launch?desk=1` currently would desk-stop, untested); `create()` double-tap
  TOCTOU — catch `FileExistsError` → `("exists", None)` (rc_launcher.py:551); RUNBOOK:45
  pins claude v2.1.169 while :337 verifies against v2.1.195 (state-table drift).
  Watch-items, fix only if observed live: the desk-badge invalidation race (an in-flight
  scan can re-cache a killed session for ≤DESK_TTL; self-heals) and `_settle_prompt`'s
  single capture at t=3s (a prompt rendering later is neither answered nor detected).

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

> **DONE 2026-09-04** — landed as `rc_share.py` (+ `rc_files_page.py`) in the a3d5576 cut, upload protocol as a Handler mixin.

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
