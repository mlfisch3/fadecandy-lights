package com.fclights.api

import com.fclights.model.WsMessage
import com.fclights.model.decodeWsMessage
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** What the app shows about its link to the controller. */
sealed interface Link {
    data object Connecting : Link
    data class Up(val message: WsMessage) : Link
    data class Down(val reason: String) : Link
}

/**
 * The live state feed.
 *
 * docs/api.md says to treat every `hello` as a full resync rather than trying
 * to reconcile what was missed while disconnected, so a reconnect simply
 * replays a `hello` and the app adopts it wholesale.
 */
class FcSocket(
    private val http: OkHttpClient,
    private val backoff: Backoff = Backoff(),
) {
    /**
     * Connect, and keep reconnecting for as long as the flow is collected.
     * Cancelling collection closes the socket.
     */
    fun connect(endpoint: Endpoint): Flow<Link> = flow {
        var attempt = 0
        while (true) {
            emit(Link.Connecting)
            var sawAnything = false
            var closedReason = "disconnected"
            try {
                session(endpoint).collect { message ->
                    if (!sawAnything) {
                        sawAnything = true
                        attempt = 0
                    }
                    emit(Link.Up(message))
                }
            } catch (e: SocketClosed) {
                closedReason = e.reason
            }
            emit(Link.Down(closedReason))
            delay(backoff.delayMillis(attempt))
            attempt++
        }
    }

    /**
     * One socket's lifetime, ending in [SocketClosed] when it drops.
     *
     * A drop is not always abrupt. Restarting the service on the Pi shuts the
     * broadcaster down, which sends every client a normal close frame, and a
     * half-closed socket delivers `onClosing` rather than `onClosed` until the
     * closing handshake is answered - so answering it is what makes an orderly
     * restart end the session here instead of leaving the app parked on state
     * that has stopped arriving.
     */
    private fun session(endpoint: Endpoint): Flow<WsMessage> = callbackFlow {
        val request = Request.Builder().url(endpoint.wsUrl).build()
        val socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                decodeWsMessage(text)?.let { trySend(it) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                close(SocketClosed(t.message ?: "connection failed"))
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, null)
                close(SocketClosed(reason.ifBlank { "closed by controller" }))
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                close(SocketClosed(reason.ifBlank { "closed by controller" }))
            }
        })
        awaitClose { socket.cancel() }
    }

    private class SocketClosed(val reason: String) : Exception(reason)
}

/**
 * Reconnect backoff. Deliberately short at the start: the common case is the
 * phone waking up on the same WiFi, and waiting seconds to show the lights'
 * real state is the difference between the app feeling broken and not.
 */
class Backoff(
    private val firstMillis: Long = 500,
    private val maxMillis: Long = 15_000,
) {
    fun delayMillis(attempt: Int): Long {
        if (attempt <= 0) return firstMillis
        var value = firstMillis
        repeat(attempt.coerceAtMost(30)) {
            value *= 2
            if (value >= maxMillis) return maxMillis
        }
        return value.coerceAtMost(maxMillis)
    }
}
