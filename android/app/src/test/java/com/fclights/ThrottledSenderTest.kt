package com.fclights

import com.fclights.ui.ThrottledSender
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * How a dragged control reaches the controller.
 *
 * Two guarantees, and they pull against each other: pace a drag so the phone is
 * not racing a 60 fps engine, and never lose the value the finger came to rest
 * on. A third sits underneath both - only one request for a parameter may be in
 * flight, because two have no ordering guarantee and the loser is whichever
 * value the controller happens to apply last.
 */
class ThrottledSenderTest {

    /**
     * Run [body] against a sender on its own scope.
     *
     * Not `backgroundScope`: `advanceUntilIdle` deliberately ignores work
     * scheduled there, so a send loop living in it would never run and every
     * assertion below would pass on an empty list.
     */
    private fun senderTest(
        send: suspend TestScope.(final: Boolean) -> Unit,
        body: suspend TestScope.(ThrottledSender) -> Unit,
    ) = runTest {
        val scope = CoroutineScope(StandardTestDispatcher(testScheduler))
        try {
            body(ThrottledSender(scope, THROTTLE) { final -> send(final) })
        } finally {
            scope.cancel()
        }
    }

    @Test
    fun `a release while a request is in flight waits its turn rather than racing it`() {
        var inFlight = 0
        var overlapped = false
        val sent = mutableListOf<Boolean>()
        senderTest(
            send = { final ->
                inFlight++
                if (inFlight > 1) overlapped = true
                sent += final
                delay(SLOW_REQUEST)
                inFlight--
            },
        ) { sender ->
            sender.request(final = false)
            advanceTimeBy(SLOW_REQUEST / 2)
            // The finger lifts while the throttled request is still out.
            sender.request(final = true)
            advanceUntilIdle()

            assertTrue("two requests were in flight at once", !overlapped)
            assertEquals(listOf(false, true), sent)
        }
    }

    @Test
    fun `the value a drag ends on is always sent, and sent last`() {
        val sent = mutableListOf<Boolean>()
        senderTest(send = { final -> sent += final }) { sender ->
            repeat(5) { sender.request(final = false) }
            sender.request(final = true)
            advanceUntilIdle()

            assertTrue("a release was swallowed by conflation: $sent", sent.contains(true))
            assertEquals(true, sent.last())
        }
    }

    @Test
    fun `a rapid drag is paced rather than sent frame by frame`() {
        val at = mutableListOf<Long>()
        senderTest(send = { at += currentTime }) { sender ->
            // 20 frames of dragging across 200ms, as a 60 fps finger would.
            repeat(20) {
                sender.request(final = false)
                advanceTimeBy(10)
            }
            advanceUntilIdle()

            assertTrue("nothing was sent at all", at.isNotEmpty())
            assertTrue("sent ${at.size} times for 200ms of dragging", at.size <= 4)
            at.zipWithNext { earlier, later ->
                assertTrue("sends only ${later - earlier}ms apart", later - earlier >= THROTTLE)
            }
        }
    }

    @Test
    fun `a release is not made to wait out the throttle`() {
        val at = mutableListOf<Long>()
        senderTest(send = { at += currentTime }) { sender ->
            sender.request(final = false)
            advanceUntilIdle()
            val afterDrag = currentTime
            sender.request(final = true)
            advanceUntilIdle()

            assertEquals(2, at.size)
            assertEquals("the release should go out at once", afterDrag, at.last())
        }
    }

    @Test
    fun `a release sends the value the drag left, not one captured when it was requested`() {
        // A drag frame and the release can arrive in the same input batch, with
        // no composition between them. Nothing may be carried from the request.
        var pending = 0
        val sent = mutableListOf<Int>()
        senderTest(send = { sent += pending }) { sender ->
            pending = 1
            sender.request(final = false)
            pending = 2
            sender.request(final = true)
            advanceUntilIdle()

            assertEquals(listOf(2), sent)
        }
    }

    private companion object {

        const val THROTTLE = 100L
        const val SLOW_REQUEST = 250L
    }
}
