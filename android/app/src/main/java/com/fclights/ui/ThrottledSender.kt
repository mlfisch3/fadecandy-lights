package com.fclights.ui

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * One control's send loop.
 *
 * A drag is throttled to roughly 10 Hz with a final value on release, because
 * the engine renders at 60 fps regardless and there is nothing to gain from
 * matching it.
 *
 * It is also the *only* sender for that control, which is the other half of the
 * job. Two requests for the same parameter in flight at once have no ordering
 * guarantee, and if the older one arrives last the controller settles on the
 * value the user already dragged away from - the revision guard on the replies
 * cannot help, because by then the wrong value is genuinely the newest state.
 * So a release does not open its own request; it joins the queue, and the queue
 * is one deep.
 *
 * The value itself is not carried here. [send] reads whatever is current when
 * its turn comes, so a tick raised while a request is in flight sends the newest
 * value rather than the one that raised it.
 *
 * Confined to the scope's dispatcher: with `viewModelScope` that is the main
 * thread, which is also where a control's callbacks call [request].
 */
class ThrottledSender(
    scope: CoroutineScope,
    private val throttleMillis: Long,
    private val send: suspend (final: Boolean) -> Unit,
) {
    /** Conflated: a drag only ever needs its most recent value sent. */
    private val ticks = Channel<Unit>(Channel.CONFLATED)

    /** Sticky until consumed, so conflating ticks cannot swallow a release. */
    private var finalRequested = false

    init {
        scope.launch {
            for (unused in ticks) {
                val final = finalRequested
                finalRequested = false
                send(final)
                // Pace the drag, but never hold back the value it ended on.
                if (!final) delay(throttleMillis)
            }
        }
    }

    fun request(final: Boolean) {
        if (final) finalRequested = true
        ticks.trySend(Unit)
    }
}
