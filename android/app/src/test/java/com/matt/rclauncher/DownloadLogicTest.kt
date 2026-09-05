package com.matt.rclauncher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The completion call the app injects into the files page must be guarded (older pages
 *  lack the hook) and must survive hostile file names without breaking out of the string.
 *  Exact strings, so an over- or under-escaping implementation cannot pass on fragments. */
class DownloadLogicTest {

    @Test
    fun plainNameAndOutcome() {
        assertEquals(
            "window.rcDownloadDone&&rcDownloadDone(\"report.pdf\",true)",
            DownloadLogic.doneScript("report.pdf", true),
        )
        assertTrue(DownloadLogic.doneScript("x", false).endsWith(",false)"))
    }

    @Test
    fun hostileNameStaysInsideTheStringLiteral() {
        assertEquals(
            "window.rcDownloadDone&&rcDownloadDone(\"a\\\"b\\\\c\\u003c/script\\u003e\\nd\",true)",
            DownloadLogic.doneScript("a\"b\\c</script>\nd", true),
        )
    }

    @Test
    fun lineTerminatorsAndControlsAreEscaped() {
        // U+2028/2029 end a JS statement while being invisible in a file name; a NUL or a
        // tab must not reach evaluateJavascript raw either
        assertEquals("\"x\\u2028y\\u2029z\"", DownloadLogic.jsString("x\u2028y\u2029z"))
        assertEquals("\"a\\u0000b\\u0009c\\u007fd\\r\"", DownloadLogic.jsString("a\u0000b\tc\u007fd\r"))
    }
}
