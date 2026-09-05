package com.fclights

import com.fclights.api.forAtMost
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A scan has to end.
 *
 * `NsdManager` browsing never completes on its own, and the connect sheet reads
 * "am I still discovering?" straight off the flow's lifetime: an unbounded
 * browse leaves a spinner where the Search button belongs, for the life of the
 * process, and keeps multicast traffic going with it.
 */
class DiscoveryTest {

    @Test
    fun `a browse that never ends still completes`() = runTest {
        val endless = flow<String> {
            emit("fadecandy")
            awaitCancellation()
        }
        assertEquals(listOf("fadecandy"), endless.forAtMost(8_000L).toList())
    }

    @Test
    fun `finding nothing is an ordinary, non-error outcome`() = runTest {
        val silent = flow<String> { awaitCancellation() }
        assertEquals(emptyList<String>(), silent.forAtMost(8_000L).toList())
    }

    @Test
    fun `the underlying browse is torn down, not merely ignored`() = runTest {
        var stopped = false
        val browse = callbackFlow {
            send("fadecandy")
            awaitClose { stopped = true }
        }
        browse.forAtMost(8_000L).toList()
        assertTrue("stopServiceDiscovery's equivalent never ran", stopped)
    }

    @Test
    fun `everything found inside the window is reported`() = runTest {
        val two = flow {
            emit("one")
            emit("two")
            awaitCancellation()
        }
        assertEquals(listOf("one", "two"), two.forAtMost(8_000L).toList())
    }
}
