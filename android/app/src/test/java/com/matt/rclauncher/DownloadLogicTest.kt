package com.matt.rclauncher

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The completion call the app injects into the files page must be guarded (older pages
 *  lack the hook) and must survive hostile file names without breaking out of the string. */
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
        val s = DownloadLogic.doneScript("a\"b\\c</script>\nd", true)
        assertFalse(s.contains("<"))            // no raw angle brackets can close a script
        assertFalse(s.contains("\n"))           // no raw newline can end the statement
        assertTrue(s.contains("\\\""))          // the quote is escaped, not terminating
        assertTrue(s.contains("\\\\"))          // the backslash is escaped
        assertTrue(s.startsWith("window.rcDownloadDone&&rcDownloadDone(\""))
    }

    @Test
    fun controlCharactersAreEscapedNotPassedThrough() {
        // a NUL or a tab in a Content-Disposition name must not reach evaluateJavascript raw
        val s = DownloadLogic.jsString("a\u0000b\tc\u007fd")
        assertEquals("\"a\\u0000b\\u0009c\\u007fd\"", s)
    }
}
