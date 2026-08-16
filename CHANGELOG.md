# Changelog

Human-facing chronological record; newest first. One entry per change — what and why.

## 2026-08-16

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
