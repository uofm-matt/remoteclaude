package com.matt.rclauncher

/** The pure half of download confirmation: the JS the app runs in the page when
 *  DownloadManager finishes. Guarded so a page without the hook (an older launcher) is a
 *  no-op, and the name is escaped as a JS string literal — a file called `"</script>` must
 *  not break the call. Kept free of Android classes so it is unit-testable on the JVM. */
object DownloadLogic {
    fun doneScript(name: String, ok: Boolean): String =
        "window.rcDownloadDone&&rcDownloadDone(${jsString(name)},$ok)"

    fun jsString(s: String): String {
        val b = StringBuilder("\"")
        for (ch in s) {
            when (ch) {
                '\\' -> b.append("\\\\")
                '"' -> b.append("\\\"")
                '\n' -> b.append("\\n")
                '\r' -> b.append("\\r")
                '<', '>', '&', '\u2028', '\u2029' -> b.append("\\u%04x".format(ch.code))
                else -> if (ch.code < 0x20 || ch.code == 0x7f) b.append("\\u%04x".format(ch.code)) else b.append(ch)
            }
        }
        return b.append("\"").toString()
    }
}
