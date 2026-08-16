package com.matt.rclauncher

import java.io.InputStream

/**
 * Resume policy for a file-share upload, decoupled from Android I/O so it is unit-testable.
 *
 * Keep going while making progress; a failed probe (`probe()` returns -1) means "unknown", NOT
 * "0 bytes on the server" — so retry the probe rather than restarting the PUT at offset 0, which
 * the server truncates the held partial to (the bug commit 0e446d4 fixed). Give up after a few
 * consecutive attempts that don't advance the byte offset, so a dead link terminates with a clean
 * failure instead of spinning forever.
 *
 * @param total   the file size; done once the server reports it has this many bytes
 * @param probe   bytes the server already holds, or -1 on a failed / non-200 HEAD
 * @param send    PUT the file from the given offset; returns true once the server finalizes it
 * @param onRetry backoff between attempts (a sleep + UI update in the app; a no-op in tests)
 */
internal fun resumeUpload(
    total: Long,
    probe: () -> Long,
    send: (Long) -> Boolean,
    onRetry: () -> Unit = {},
): Boolean {
    // The server rejects total <= 0 with 400, and have(0) >= total(0) would otherwise report
    // success without a single byte sent — an empty file must fail here, not fake an upload.
    if (total <= 0) return false
    val maxStalls = 6
    var stalls = 0
    var lastHave = -1L
    while (stalls < maxStalls) {
        val have = probe()
        if (have >= total) return true
        if (have < 0) {            // probe failed: unknown, not zero — never PUT from 0
            stalls++
            onRetry()
            continue
        }
        stalls = if (have > lastHave) 0 else stalls + 1
        lastHave = have
        if (send(have)) return true
        onRetry()
    }
    return false
}

/**
 * Map a HEAD probe response to the byte count the server holds: the X-Rc-Have count on 200, else
 * -1 (unknown, NOT 0 — a failed probe must never make the resume restart at offset 0 and truncate
 * the held partial). This is the producer side of resumeUpload's `probe` contract; the bug that
 * shipped in 0e446d4 was this returning 0 on a failed HEAD.
 */
internal fun haveFromResponse(code: Int, header: String?): Long =
    if (code == 200) header?.toLongOrNull() ?: 0L else -1L

/**
 * Advance [input] by exactly [n] bytes, tolerating a short skip() by reading and discarding.
 * Positions a content-URI stream at the resume offset; a miscount here silently corrupts the
 * uploaded file, so it's unit-tested against the skip()-returns-short and past-EOF cases.
 */
internal fun skipFully(input: InputStream, n: Long) {
    var left = n
    val buf = ByteArray(65536)
    while (left > 0) {
        val s = input.skip(left)
        if (s > 0) {
            left -= s
        } else {
            val r = input.read(buf, 0, minOf(left, buf.size.toLong()).toInt())
            if (r < 0) break
            left -= r
        }
    }
}
