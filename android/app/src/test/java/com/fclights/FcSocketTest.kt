package com.fclights

import com.fclights.api.Endpoint
import com.fclights.api.FcSocket
import com.fclights.api.Link
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The live feed's end-of-session handling.
 *
 * A socket does not only die abruptly. Restarting the service on the Pi closes
 * every client socket politely, which is a different code path on the client
 * and the one a bring-up session hits over and over. Both have to end the
 * session, because until it ends the app goes on showing the last state it saw
 * as though it were live.
 */
class FcSocketTest {

    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /** Collect until the session ends, or fail rather than hang if it never does. */
    private fun linksUntilDown(): List<Link> = runBlocking {
        val socket = FcSocket(OkHttpClient.Builder().build())
        val seen = mutableListOf<Link>()
        withTimeout(TIMEOUT_MILLIS) {
            socket.connect(Endpoint(server.hostName, server.port)).first { link ->
                seen += link
                link is Link.Down
            }
        }
        seen
    }

    @Test
    fun `a clean close from the controller ends the session`() {
        server.enqueue(
            MockResponse().withWebSocketUpgrade(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocket.send(Fixtures.hello)
                    webSocket.close(1000, "restarting")
                }
            })
        )

        val seen = linksUntilDown()

        assertTrue("never saw the hello: $seen", seen.any { it is Link.Up })
        assertTrue("session never ended: $seen", seen.last() is Link.Down)
    }

    @Test
    fun `a socket that never opens ends the session too`() {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))

        val seen = linksUntilDown()

        assertTrue("session never ended: $seen", seen.last() is Link.Down)
    }

    private companion object {
        const val TIMEOUT_MILLIS = 10_000L
    }
}
