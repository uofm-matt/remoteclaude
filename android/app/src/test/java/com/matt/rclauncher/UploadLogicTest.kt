package com.matt.rclauncher

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream

/** Pins the resume policy and its I/O primitives — the stateful client half where upload
 *  corruption bugs live (a failed HEAD reporting 0, or a miscounted skip). */
class UploadLogicTest {

    // --- resumeUpload policy ---

    @Test
    fun failedProbeNeverSendsFromZero() {
        // a persistent failed HEAD (-1) must NOT restart the PUT at offset 0 (server truncates)
        val sent = mutableListOf<Long>()
        val ok = resumeUpload(1000L, probe = { -1L }, send = { sent.add(it); true })
        assertFalse(ok)                 // terminates after the stall cap
        assertTrue(sent.isEmpty())      // and never sent — no offset-0 truncation of the partial
    }

    @Test
    fun resumesFromReportedOffsetNotZero() {
        val haves = ArrayDeque(listOf(0L, 400L, 800L, 1000L))
        val sent = mutableListOf<Long>()
        val ok = resumeUpload(
            1000L,
            probe = { haves.removeFirst() },
            send = { sent.add(it); false },   // each PUT "drops" so the loop re-probes and advances
        )
        assertTrue(ok)                                 // probe eventually reports the full size
        assertEquals(listOf(0L, 400L, 800L), sent)     // resumed from each reported offset, never re-0
    }

    @Test
    fun transientProbeFailuresRecover() {
        // two failed probes then progress must NOT fail the upload — the stall counter resets
        val probes = ArrayDeque(listOf(-1L, -1L, 0L, 1000L))
        var sends = 0
        val ok = resumeUpload(1000L, probe = { probes.removeFirst() }, send = { sends++; false })
        assertTrue(ok)
        assertEquals(1, sends)  // sent once, from the recovered offset 0
    }

    @Test
    fun givesUpAfterConsecutiveNoProgress() {
        var probes = 0
        val ok = resumeUpload(1000L, probe = { probes++; 100L }, send = { false })
        assertFalse(ok)
        assertTrue("terminated, not an infinite spin: probes=$probes", probes in 6..8)
    }

    @Test
    fun succeedsWhenSendFinalizes() {
        assertTrue(resumeUpload(1000L, probe = { 0L }, send = { true }))
    }

    @Test
    fun alreadyCompleteNeedsNoSend() {
        val sent = mutableListOf<Long>()
        assertTrue(resumeUpload(500L, probe = { 500L }, send = { sent.add(it); true }))
        assertTrue(sent.isEmpty())  // server already has the whole file -> no PUT
    }

    // --- haveFromResponse: the sentinel producer (0e446d4 regressed exactly this) ---

    @Test
    fun haveFromResponseMapping() {
        assertEquals(400L, haveFromResponse(200, "400"))
        assertEquals(0L, haveFromResponse(200, null))    // 200, no header -> fresh (0)
        assertEquals(0L, haveFromResponse(200, "junk"))  // unparseable -> 0
        assertEquals(-1L, haveFromResponse(500, "400"))  // non-200 -> UNKNOWN (-1), never 0
        assertEquals(-1L, haveFromResponse(404, null))
    }

    // --- skipFully: byte-accurate offset positioning ---

    @Test
    fun skipFullyAdvancesExactly() {
        val data = ByteArray(1000) { it.toByte() }
        val input = ByteArrayInputStream(data)
        skipFully(input, 400)
        assertArrayEquals(data.copyOfRange(400, 1000), input.readBytes())
    }

    @Test
    fun skipFullyFallsBackToReadWhenSkipReturnsZero() {
        val data = ByteArray(1000) { it.toByte() }
        val input = object : ByteArrayInputStream(data) {
            override fun skip(n: Long): Long = 0  // force the read-and-discard path
        }
        skipFully(input, 400)
        assertArrayEquals(data.copyOfRange(400, 1000), input.readBytes())
    }

    @Test
    fun skipFullyPastEofTerminates() {
        val input = ByteArrayInputStream(ByteArray(100))
        skipFully(input, 5000)  // must terminate on EOF, not loop forever
        assertEquals(-1, input.read())
    }
}
