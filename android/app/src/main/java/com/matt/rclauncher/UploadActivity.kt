package com.matt.rclauncher

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import android.util.Log
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * Share target with a progress dialog. Send file(s) to "Remote Control" from any app and
 * they upload to ~/rc-share via the launcher PUT endpoint, no web view. Shows a per-file
 * progress bar + byte count, then finishes. Streams straight from the content URI so a
 * large video never buffers into memory.
 */
class UploadActivity : Activity() {

    private lateinit var title: TextView
    private lateinit var bar: ProgressBar
    private lateinit var status: TextView

    // (base, token) parsed from the URL MainActivity persisted on paste. No token is baked
    // into CI APKs (public repo, world-downloadable artifacts), so the pasted credential is
    // the only source — and it also carries the right host if it differs from RC_HOST.
    private val creds: Pair<String, String>? by lazy {
        getSharedPreferences("rc", MODE_PRIVATE).getString("url", null)?.let {
            val u = Uri.parse(it)
            val t = u.getQueryParameter("token")
            if (t.isNullOrEmpty() || u.scheme == null) null
            else "${u.scheme}://${u.authority}" to t
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val d = resources.displayMetrics.density
        val pad = (20 * d).toInt()
        title = TextView(this).apply { textSize = 16f }
        bar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 100 }
        status = TextView(this).apply { textSize = 13f; alpha = 0.7f }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
            addView(title, gap(bottom = 12 * d))
            addView(bar, gap())
            addView(status, gap(top = 10 * d))
        })
        setFinishOnTouchOutside(false)

        val uris = uris(intent)
        if (uris.isEmpty()) {
            Toast.makeText(this, "no file found in the share", Toast.LENGTH_LONG).show(); finish(); return
        }
        if (creds == null) { toast("no token — open Remote Control once and paste it"); finish(); return }
        thread {
            var ok = 0
            uris.forEachIndexed { i, uri ->
                val name = displayName(uri)
                ui { title.text = if (uris.size == 1) name else "${i + 1}/${uris.size}  $name"; bar.progress = 0 }
                if (uploadOne(uri)) ok++
            }
            ui {
                title.text = "Uploaded $ok/${uris.size} to rc-share"
                bar.isIndeterminate = false; bar.progress = 100; status.text = ""
                bar.postDelayed({ finish() }, 1200)
            }
        }
    }

    private fun uploadOne(uri: Uri): Boolean {
        val name = displayName(uri)
        val total = fileSize(uri)
        if (total < 0) {  // unknown size -> can't resume; single buffered attempt, with retry
            repeat(4) { a ->
                if (a > 0) { ui { status.text = "link dropped — retry $a…" }; Thread.sleep(1500) }
                if (putWhole(uri, name)) return true
            }
            return false
        }
        // Resume via the pure policy in UploadLogic.kt: probe what the server holds (HEAD), PUT
        // the rest from there; a dropped link keeps the partial so the next attempt picks up.
        val rid = "$total-${lastModified(uri)}"  // key the resume so a stale same-name partial isn't merged onto
        return resumeUpload(
            total,
            probe = { serverHave(name, rid) },
            send = { off -> putFrom(uri, name, off, total, rid) },
            onRetry = { ui { status.text = "link dropped — resuming…" }; Thread.sleep(1500) },
        )
    }

    private fun serverHave(name: String, rid: String): Long = try {
        val c = (url(name).openConnection() as HttpURLConnection).apply {
            requestMethod = "HEAD"; connectTimeout = 15000
            setRequestProperty("X-Rc-Id", rid)
        }
        val h = haveFromResponse(c.responseCode, c.getHeaderField("X-Rc-Have"))
        c.disconnect(); h
    } catch (e: Exception) { Log.w(TAG, "HEAD probe failed", e); -1L }  // unknown, NOT "0 bytes"

    private fun putFrom(uri: Uri, name: String, offset: Long, total: Long, rid: String): Boolean = try {
        ui {
            bar.isIndeterminate = false; bar.progress = (offset * 100 / total).toInt()
            status.text = "${human(offset)} / ${human(total)}"
        }
        val conn = (url(name).openConnection() as HttpURLConnection).apply {
            requestMethod = "PUT"; doOutput = true; connectTimeout = 15000
            setRequestProperty("X-Rc-Offset", offset.toString())
            setRequestProperty("X-Rc-Total", total.toString())
            setRequestProperty("X-Rc-Id", rid)
            setFixedLengthStreamingMode(total - offset)
        }
        contentResolver.openInputStream(uri)!!.use { input ->
            skipFully(input, offset)
            conn.outputStream.use { out ->
                val buf = ByteArray(65536); var sent = offset; var last = (offset * 100 / total).toInt(); var n: Int
                while (input.read(buf).also { n = it } >= 0) {
                    out.write(buf, 0, n); sent += n
                    val pct = (sent * 100 / total).toInt()
                    if (pct != last) {
                        last = pct; val s = sent
                        ui { bar.progress = pct; status.text = "${human(s)} / ${human(total)}" }
                    }
                }
            }
        }
        // finalized only when the server says so — a 200 can be a partial write ({"done":false})
        conn.responseCode == 200 && conn.inputStream.use {
            it.readBytes().decodeToString().filterNot(Char::isWhitespace).contains("\"done\":true")
        }
    } catch (e: Exception) { Log.w(TAG, "PUT failed", e); false }

    private fun putWhole(uri: Uri, name: String): Boolean = try {
        ui { bar.isIndeterminate = true }
        val bytes = contentResolver.openInputStream(uri)!!.use { it.readBytes() }
        val conn = (url(name).openConnection() as HttpURLConnection).apply {
            requestMethod = "PUT"; doOutput = true; connectTimeout = 15000
            setFixedLengthStreamingMode(bytes.size)
        }
        ui { bar.isIndeterminate = false; bar.progress = 0 }
        conn.outputStream.use { it.write(bytes) }
        conn.responseCode == 200
    } catch (e: Exception) { Log.w(TAG, "PUT(whole) failed", e); false }

    private fun url(name: String): URL {
        val (base, token) = creds!!  // onCreate bailed if null
        return URL("$base/files/${Uri.encode(name)}?token=$token")
    }

    private fun gap(top: Float = 0f, bottom: Float = 0f) =
        LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { setMargins(0, top.toInt(), 0, bottom.toInt()) }

    private fun ui(block: () -> Unit) = runOnUiThread { if (!isFinishing && !isDestroyed) block() }

    // Find the shared file however the sending app delivered it: EXTRA_STREAM (single or
    // list) or ClipData. Uses the type-safe parcelable API on 33+ where the old overload
    // can return null.
    private fun uris(i: Intent): List<Uri> {
        val out = ArrayList<Uri>()
        when (i.action) {
            Intent.ACTION_SEND -> stream(i)?.let(out::add)
            Intent.ACTION_SEND_MULTIPLE -> out.addAll(streams(i))
        }
        if (out.isEmpty()) i.clipData?.let { c ->
            for (k in 0 until c.itemCount) c.getItemAt(k).uri?.let(out::add)
        }
        return out
    }

    private fun stream(i: Intent): Uri? =
        if (Build.VERSION.SDK_INT >= 33) i.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
        else @Suppress("DEPRECATION") i.getParcelableExtra(Intent.EXTRA_STREAM)

    private fun streams(i: Intent): List<Uri> =
        if (Build.VERSION.SDK_INT >= 33)
            i.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
        else @Suppress("DEPRECATION") i.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM).orEmpty()

    private fun displayName(uri: Uri): String {
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst() && !it.isNull(0)) return it.getString(0)
        }
        return uri.lastPathSegment?.substringAfterLast('/') ?: "upload.bin"
    }

    private fun fileSize(uri: Uri): Long {
        contentResolver.query(uri, arrayOf(OpenableColumns.SIZE), null, null, null)?.use {
            if (it.moveToFirst() && !it.isNull(0)) return it.getLong(0)
        }
        return -1
    }

    private fun lastModified(uri: Uri): Long = try {
        contentResolver.query(uri, arrayOf(DocumentsContract.Document.COLUMN_LAST_MODIFIED), null, null, null)?.use {
            if (it.moveToFirst() && !it.isNull(0)) it.getLong(0) else 0L
        } ?: 0L
    } catch (e: Exception) { 0L }

    private fun human(n: Long): String {
        if (n < 1024) return "$n B"
        var v = n.toDouble(); var i = -1
        while (v >= 1024 && i < 3) { v /= 1024; i++ }
        return String.format("%.1f %sB", v, "KMGT"[i])
    }

    private fun toast(m: String) = Toast.makeText(this, m, Toast.LENGTH_SHORT).show()

    companion object { private const val TAG = "rc-upload" }
}
