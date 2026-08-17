# Remote Control — Android app

A thin native frame around the launcher's web UI. It loads `rc_launcher.py`'s own
page in a full-screen `WebView`, so you get a home-screen icon and a chrome-less
window (no browser URL bar) while the whole UI — project list, filter,
launch/stop/create, live status, `/files` — stays the launcher's live page. Nothing
to keep in sync: change the page, the app shows it on next load.

## Why a wrapper and not a PWA

The launcher is plain HTTP on the LAN/tailnet by design. Android blocks the parts of
a PWA that would matter (service workers, offline, true install) unless the origin is
a *secure context* — HTTPS or `localhost`, and a LAN IP over `http://` is neither.
The only way to a real installable PWA is putting HTTPS in front (e.g. `tailscale
serve`), which would mean running Tailscale on the Mac — the invariant this project
keeps. So a WebView, which declares its own cleartext trust, is the clean path to an
app frame here.

## Sideload from CI (no Android toolchain)

The `Android APK` GitHub Actions workflow builds the debug APK on every push that
touches the app, so you never need the SDK on the Mac. With the phone plugged in and
USB debugging on, `sideload.sh` pulls the newest build and installs it:

    ./android/sideload.sh            # install the latest green build (waits if still building)
    ./android/sideload.sh --build    # rebuild the committed code first, then install
    ./android/sideload.sh --watch    # loop: auto-install each new build as it lands (Ctrl-C to stop)
    ./android/sideload.sh --local app.apk   # install a local APK, skip CI

Builds are signed with a **stable key** (the `KEYSTORE_*` Actions secrets), so
`sideload.sh` updates in place with `adb install -r` — your home-screen icon and app
data survive every update. Only a change of the signing key forces a one-time
uninstall+reinstall (after which the icon needs re-adding once). Needs `adb` and `gh`.

Set the signing secrets up once:

    keytool -genkeypair -keystore rc.keystore -storetype PKCS12 -alias rc \
      -keyalg RSA -keysize 2048 -validity 10000 -dname CN=remoteclaude
    gh secret set KEYSTORE_B64  -R <you>/remoteclaude --body "$(base64 -i rc.keystore | tr -d '\n')"
    gh secret set KEYSTORE_PASS -R <you>/remoteclaude --body '<the store password>'
    gh secret set KEY_ALIAS     -R <you>/remoteclaude --body rc

On first launch the app offers to add itself to the home screen — tap **Add**. After
that, in-place updates keep it there.

## Sharing files to the rc (no web view)

Besides the **+ upload** button on the `/files` page, the app is a **share target**: in
any app (Photos, Files, a browser) tap **Share → Remote Control** and the file(s) upload
straight to `~/rc-share` via the launcher's PUT endpoint — no web page involved, authed
with the token you pasted on first run (CI APKs carry no baked secret; see First run). `UploadActivity` shows a progress dialog (per-file bar + byte
count), streams each file straight from its content URI, then finishes. Handles multiple
files at once. This is the reliable upload path — it doesn't touch the WebView, so it
isn't affected by the reload-on-resume.

Uploads are **resumable**: the app `HEAD`s the target for `X-Rc-Have`, seeks the file to
that offset, and `PUT`s the rest with `X-Rc-Offset`/`X-Rc-Total`. A dropped connection
(common on Starlink/Tailscale) resumes from the last received byte instead of restarting,
continuing as long as it makes progress and giving up only after ~6 consecutive stalls that
don't advance the offset. Files whose size the content provider doesn't report fall back to
a single buffered PUT.

## Build

Needs the Android SDK, JDK 17, and Gradle 8.7 (AGP 8.5). Easiest path is Android
Studio.

- **Android Studio:** File → Open → this `android/` folder, let it sync (it provides
  the Gradle wrapper and writes `local.properties` with your SDK path), then Run, or
  Build → Build APK(s).
- **Command line:** the Gradle wrapper jar isn't committed (it's a binary). Generate
  it once, then build:
  ```sh
  cd android
  gradle wrapper            # one-time, needs a system Gradle >= 8.7
  ./gradlew assembleDebug
  ```
  APK lands at `app/build/outputs/apk/debug/app-debug.apk`.

`minSdk` is 29 (Android 10), which keeps `/files` downloads permission-free.

## Install

No Play Store. Sideload the APK:

- `adb install app/build/outputs/apk/debug/app-debug.apk`, or
- transfer the APK to the phone and tap it (enable "install unknown apps" for your
  file manager when prompted).

CI APKs are signed with the stable keystore (the `KEYSTORE_*` Actions secrets). To update
in place you must keep signing with the **same** keystore — a differently-signed APK won't
install over the old one (uninstall first, which loses the saved URL/cookie).

## First run

CI APKs are **untokened by design**: this repo is public and workflow artifacts are
downloadable by any logged-in GitHub user, so no secret is ever baked in — a CI guard
step fails the build if a token field or secret wiring ever reappears. On first launch
the app asks once: paste the token (`cat ~/.config/rc-launcher/token` on the Mac), or the
full launcher URL if your host differs from the baked `RC_HOST` default (set the
`RC_HOST` repo *variable* to bake yours: `gh variable set RC_HOST --body "http://<mac-lan-ip>:8787"`).
Stable signing means updates install in place and the pasted token survives — it really
is once per device.

**Long-press** anywhere to re-enter it (e.g. after rotating the token; the "Can't reach
Remote Control" screen you'll see right after a rotation is expected — its third bullet
points at exactly this).

Reach it the same way the web page does: same LAN, or a Tailscale subnet route to the
Mac's LAN IP. The Mac still runs no Tailscale.

## Notes

- Cleartext HTTP is allowed via `res/xml/network_security_config.xml`. The app only
  talks to the URL you configure. If you ever reach the Mac by a different
  IP/hostname, no change needed — cleartext is permitted for all hosts (LAN/tailnet
  only in practice).
- Tapping a file in `/files` downloads it (non-renderable files) or previews it
  (PDF/image), via `DownloadManager` with the auth cookie attached.
- No third-party dependencies — just the Android SDK and Kotlin stdlib, matching the
  launcher's stdlib-only ethos.
