package com.matt.rclauncher

import android.app.Activity
import android.app.AlertDialog
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.ShortcutInfo
import android.content.pm.ShortcutManager
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.drawable.Icon
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.text.InputType
import android.view.View
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast

/**
 * Native frame around the remoteclaude launcher's own web UI. The launcher is
 * plain HTTP on the LAN/tailnet, so a real installable PWA is impossible (service
 * workers and install need a secure context); a WebView is the clean way to get a
 * chrome-less app icon over cleartext. The whole UI — project list, filter,
 * launch/stop/create, 5s status polling, /files — is the launcher's live page, so
 * there is nothing to keep in sync: change the page, the app shows it.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private val prefs by lazy { getSharedPreferences("rc", Context.MODE_PRIVATE) }
    private val host = BuildConfig.RC_HOST  // Mac LAN IP, reached over the subnet route
    private var fileCallback: ValueCallback<Array<Uri>>? = null  // pending <input type=file>
    private var resumedBefore = false
    private var skipNextReload = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Follow the system theme; the launcher page does the rest via
        // prefers-color-scheme. A uiMode change recreates the activity (uiMode is
        // not in configChanges), so this re-runs and the WebView reloads light/dark.
        val night = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) ==
            Configuration.UI_MODE_NIGHT_YES
        val bg = Color.parseColor(if (night) "#0b0f14" else "#eef1f5")
        window.statusBarColor = bg
        window.decorView.setBackgroundColor(bg)
        @Suppress("DEPRECATION")
        if (!night) window.decorView.systemUiVisibility =
            window.decorView.systemUiVisibility or View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
        CookieManager.getInstance().setAcceptCookie(true)

        web = WebView(this).apply {
            setBackgroundColor(bg)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            // Route the page's <input type=file> (the /files upload button) to a
            // system file picker; without this the input does nothing in a WebView.
            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(
                    v: WebView, cb: ValueCallback<Array<Uri>>, params: FileChooserParams,
                ): Boolean {
                    fileCallback?.onReceiveValue(null)
                    fileCallback = cb
                    skipNextReload = true  // the resume after the picker must not reload —
                    return try {           // that would cancel the upload the file starts
                        @Suppress("DEPRECATION")
                        startActivityForResult(params.createIntent(), 42); true
                    } catch (e: ActivityNotFoundException) {
                        fileCallback = null; false
                    }
                }
            }
            webViewClient = object : WebViewClient() {
                // Replace the stock net::ERR page with our own themed "can't reach" screen
                // (with a Retry) for any main-frame connection or HTTP error.
                override fun onReceivedError(v: WebView, req: WebResourceRequest, e: WebResourceError) {
                    if (req.isForMainFrame) showConnError(v)
                }

                override fun onReceivedHttpError(
                    v: WebView, req: WebResourceRequest, r: WebResourceResponse,
                ) {
                    if (req.isForMainFrame) showConnError(v)
                }
            }
            setOnLongClickListener {
                if (hitTestResult.type == WebView.HitTestResult.UNKNOWN_TYPE) {
                    promptForToken(); true
                } else false
            }
            setDownloadListener { url, _, disposition, mime, _ -> download(url, disposition, mime) }
        }
        setContentView(web)

        val url = prefs.getString("url", null) ?: bakedUrl()
        if (url.isNullOrBlank()) promptForToken() else web.loadUrl(url)
    }

    override fun onPause() {
        super.onPause()
        CookieManager.getInstance().flush()  // persist rc_token before the app can be killed
    }

    override fun onResume() {
        super.onResume()
        offerPin()  // fires when foreground (after unlock), so the confirm actually shows
        // Reload on return so the launcher view is never stale. Skip the first resume
        // (onCreate just loaded) and the resume right after the file picker (reloading
        // there cancels the upload the picked file kicked off).
        if (resumedBefore && !skipNextReload) web.reload()
        resumedBefore = true
        skipNextReload = false
    }

    @Deprecated("stable and dependency-free; predictive back needs androidx.activity")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    @Deprecated("plain-Activity result API; ComponentActivity's needs androidx.activity")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 42) {
            fileCallback?.onReceiveValue(
                WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            fileCallback = null
        }
    }

    // URL built from the token baked in at CI build time (RC_TOKEN secret), or null
    // for a local build with no token — then we fall back to asking.
    private fun bakedUrl(): String? =
        BuildConfig.RC_TOKEN.takeIf { it.isNotEmpty() }?.let { "$host/?token=$it" }

    // Offer to place the app on the home screen once (the launcher shows a confirm).
    // Gated by a pref so it asks a single time; stable signing then keeps the icon
    // across in-place updates, so it never needs re-adding.
    private fun offerPin() {
        if (prefs.getBoolean("pinned", false)) return
        val sm = getSystemService(ShortcutManager::class.java) ?: return
        if (!sm.isRequestPinShortcutSupported) return
        val info = ShortcutInfo.Builder(this, "rc")
            .setShortLabel(getString(R.string.app_name))
            .setIcon(Icon.createWithResource(this, R.mipmap.ic_launcher))
            .setIntent(Intent(this, MainActivity::class.java).setAction(Intent.ACTION_MAIN))
            .build()
        sm.requestPinShortcut(info, null)
        prefs.edit().putBoolean("pinned", true).apply()
    }

    private fun promptForToken() {
        val field = EditText(this).apply {
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            hint = "launcher token"
        }
        AlertDialog.Builder(this)
            .setTitle("Launcher token")
            .setMessage("Paste the token from install.sh (~/.config/rc-launcher/token). Host is $host — long-press anywhere to re-enter it later.")
            .setView(field)
            .setPositiveButton("Connect") { _, _ ->
                val t = field.text.toString().trim()
                if (t.isNotEmpty()) {
                    val u = "$host/?token=$t"
                    prefs.edit().putString("url", u).apply()
                    web.loadUrl(u)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun download(url: String, disposition: String?, mime: String?) {
        val req = DownloadManager.Request(Uri.parse(url)).apply {
            CookieManager.getInstance().getCookie(url)?.let { addRequestHeader("Cookie", it) }
            setMimeType(mime)
            setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            setDestinationInExternalPublicDir(
                Environment.DIRECTORY_DOWNLOADS,
                URLUtil.guessFileName(url, disposition, mime),
            )
        }
        (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
        Toast.makeText(this, "Downloading…", Toast.LENGTH_SHORT).show()
    }

    // Render our own connection-error page into the WebView instead of the stock net:: page.
    // The Retry link just navigates back to the launcher URL; if it fails again the same page
    // reappears. Long-press still re-opens the token dialog (see the hint).
    private fun showConnError(v: WebView) {
        val retry = prefs.getString("url", null) ?: bakedUrl()
        v.loadDataWithBaseURL(null, errorHtml(retry), "text/html", "UTF-8", null)
    }

    private fun errorHtml(retry: String?): String {
        val button = if (retry != null) """<a class=btn href="$retry">Retry</a>""" else ""
        return """<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{--bg:#eef1f5;--fg:#1a2230;--mut:#67748a;--accent:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#0b0f14;--fg:#e6edf3;--mut:#8b98a9;--accent:#4b9fff}}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;padding:24px}
.card{max-width:340px;text-align:center}
.ico{font-size:46px}
h1{font-size:20px;margin:10px 0 4px}
.host{font:13px ui-monospace,monospace;color:var(--mut);word-break:break-all;margin:2px 0}
ul{text-align:left;color:var(--mut);font-size:14px;margin:18px auto 0;padding-left:20px;max-width:280px}
li{margin:5px 0}
.btn{display:inline-block;margin-top:24px;padding:13px 34px;background:var(--accent);color:#fff;text-decoration:none;border-radius:11px;font-weight:600}
</style></head><body><div class=card>
<div class=ico>&#128246;&#10060;</div>
<h1>Can't reach Remote Control</h1>
<div class=host>$host</div>
<ul>
<li>On the same Wi-Fi as the Mac, or Tailscale connected?</li>
<li>Is the launcher running on the Mac?</li>
<li>Long-press this screen to re-enter the token.</li>
</ul>
$button
</div></body></html>"""
    }
}
