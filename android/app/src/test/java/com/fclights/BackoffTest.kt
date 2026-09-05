package com.fclights

import com.fclights.api.Backoff
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BackoffTest {

    private val backoff = Backoff(firstMillis = 500, maxMillis = 15_000)

    @Test
    fun `the first retry is quick, because the usual cause is a phone waking up`() {
        assertEquals(500L, backoff.delayMillis(0))
        assertEquals(1_000L, backoff.delayMillis(1))
        assertEquals(2_000L, backoff.delayMillis(2))
    }

    @Test
    fun `it stops growing at the ceiling`() {
        assertEquals(15_000L, backoff.delayMillis(20))
        assertEquals(15_000L, backoff.delayMillis(1_000_000))
    }

    @Test
    fun `it never returns a negative or overflowed delay`() {
        (0..64).forEach { attempt ->
            val delay = backoff.delayMillis(attempt)
            assertTrue("attempt $attempt gave $delay", delay in 500L..15_000L)
        }
    }
}
