# Refutations

Claims that cost a measurement to kill. Read this before proposing a finding; add to it
after refuting one. A refutation leaves no trace in the diff, so this file is the only
place it exists.

Written by `refute.py`; the format is parsed, so keep the `- key: value` shape.

## rc_launcher.py:136/479 carry uncommitted regressions (except narrowed to OSError; .lower() dropped from the sort key)

- scope: `rc_launcher.py`
- verdict: REFUTED
- measured: 2026-08-08
- commit: 7a530c1e1a75aa7a388d4737d965c226018a6368
- oracle: git diff --stat empty at HEAD; sed -n '136p;479p' shows both lines at committed values
- cost: one git diff + one sed
- unmeasured: none
- evidence: rc_launcher.py
- evidence_sha: 6e6dfa60242df66e

## The zsh fallback ${(%):-%x} in the bash/zsh source-dir resolver is brittle and can break guard initialization in supported zsh setups

- scope: `rc_guard.sh`
- verdict: REFUTED
- measured: 2026-08-26
- commit: f364ea82d40e8848ef2b7716249bb508b02aca64
- oracle: sourced rc_guard.sh live from both bash and zsh (non-interactive -c and inside a function); _RC_GUARD_DIR resolved to the repo dir in both; the (%) flag forces prompt expansion independent of shell options and bash never evaluates the fallback because BASH_SOURCE is set
- cost: two shell invocations
- unmeasured: zsh invoked with emulate sh / KSH_ARRAYS-style compat modes was not tried

## After the module cut the upload finalize writes its 200/JSON inside a contextlib.suppress(OSError) block so a successful PUT gets no terminal response; and launch() retries a failed resume without killing the dead tmux session first

- scope: `rc_sessions.py`
- verdict: REFUTED
- measured: 2026-09-04
- commit: 64491cee9aed9f8969308a0093994c603f663707
- oracle: grep: the only suppress(OSError) in rc_share.py is sweep_rcparts (unlink loop), _upload's _json is unguarded; rc_sessions._spawn kills the session on both the dead-pane and stuck-prompt paths exactly as origin/main's rc_launcher._spawn did (diffed); 141 tests incl. test_launch_fresh_after_failed_resume green
- cost: two greps and one git show
- unmeasured: a live tmux run of the resume-then-fresh fallback was not repeated on this build

## Dropping the fixtures' own try/finally in test_desk leaks the patched os.path.islink, os.readlink and time.time into later tests because restore_globals() does not cover nested stdlib attributes

- scope: `tests/_harness.py`
- verdict: REFUTED
- measured: 2026-09-04
- commit: e75844ef38d3b6a99a265d66beae2edc86905428
- oracle: tests/_harness.py _STDLIB lists (time,'time'), (os.path,'islink'), (os,'readlink'); ran tests.test_desk in a fresh interpreter and compared identities before/after: all three restored (True, True, True), islink('/proc/777/cwd') False after the suite; a keep()-as-no-op mutant is killed by two suite failures, so the restores are observed, not decorative
- cost: one subprocess run
- unmeasured: a cleanup registered after restore_globals() in setUp still runs while patches are live (LIFO); documented in the restore_globals docstring, not guarded

## check_launcher's except (OSError,...) misses urllib HTTPError on a non-2xx /version, and install.sh --reload runs before PORT is initialized so ${PORT} expands empty

- scope: `install.sh`
- verdict: REFUTED
- measured: 2026-09-06
- commit: 43fbada91e382ca99f32b76aa422f7b049c1acf3
- oracle: urllib.error.HTTPError subclasses URLError subclasses OSError, so a non-2xx is caught (verified in the class hierarchy); install.sh defines PORT at line 10 and the --reload block is inserted after line 20, so PORT is always set first (read the file)
- cost: reading the exception hierarchy and the install.sh line order
- unmeasured: none

## Changing PORT to os.environ.get('RC_LAUNCHER_PORT') or '8787' breaks an explicitly configured port 0 by coercing it to the default

- scope: `rc_config.py`
- verdict: REFUTED
- measured: 2026-09-06
- commit: f4b97a87cbdc1b7b70c1a805f36283ac6e1f2e17
- oracle: os.environ.get returns a string; the string '0' is truthy, so "0" or "8787" -> "0" -> int("0") -> 0. Only None (unset) or "" (empty) fall through to the default. Verified: RC_LAUNCHER_PORT=0 python3 -c 'import rc_config;print(rc_config.PORT)' prints 0
- cost: one interpreter line
- unmeasured: none
