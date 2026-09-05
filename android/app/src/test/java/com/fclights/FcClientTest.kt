package com.fclights

import com.fclights.api.Endpoint
import com.fclights.api.FcApiException
import com.fclights.api.FcClient
import com.fclights.model.ColorValue
import com.fclights.model.FcJson
import com.fclights.model.Params
import java.io.IOException
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The REST client, against a server that answers with real captured bodies.
 *
 * The API rejects an unknown request field with a 422 rather than ignoring it,
 * so several of these assert on exactly what goes out, not only on what comes
 * back: a stray field here is a command that silently never works.
 */
class FcClientTest {

    private lateinit var server: MockWebServer
    private lateinit var client: FcClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        client = FcClient(
            Endpoint(server.hostName, server.port),
            OkHttpClient.Builder().build(),
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun respondState() {
        server.enqueue(MockResponse().setBody(Fixtures.state).setHeader("content-type", "application/json"))
    }

    private fun bodyOf(recorded: okhttp3.mockwebserver.RecordedRequest): JsonObject =
        FcJson.parseToJsonElement(recorded.body.readUtf8()) as JsonObject

    @Test
    fun `power sends exactly the documented body`() = runTest {
        respondState()
        client.setPower(false)

        val request = server.takeRequest()
        assertEquals("PUT", request.method)
        assertEquals("/api/power", request.path)
        val body = bodyOf(request)
        assertEquals(setOf("on"), body.keys)
        assertEquals("false", body.getValue("on").toString())
    }

    @Test
    fun `brightness sends a bare float`() = runTest {
        respondState()
        client.setBrightness(0.4)

        val request = server.takeRequest()
        assertEquals("PUT", request.method)
        assertEquals("/api/brightness", request.path)
        assertEquals(setOf("brightness"), bodyOf(request).keys)
    }

    @Test
    fun `selecting an effect with no parameters omits the params key entirely`() = runTest {
        respondState()
        client.setEffect("rainbow")

        val body = bodyOf(server.takeRequest())
        // Sending `"params": null` would be a 422; the controller fills in the
        // effect's declared defaults when the key is absent.
        assertEquals(setOf("effect"), body.keys)
    }

    @Test
    fun `an effect selection carries the parameters it was given`() = runTest {
        respondState()
        client.setEffect("rainbow", mapOf("speed" to JsonPrimitive(0.3)))

        val body = bodyOf(server.takeRequest())
        assertEquals(setOf("effect", "params"), body.keys)
        assertEquals("0.3", (body.getValue("params") as JsonObject).getValue("speed").toString())
    }

    @Test
    fun `a drag patches only the parameter that moved`() = runTest {
        respondState()
        client.patchParams(mapOf("speed" to JsonPrimitive(0.7)))

        val request = server.takeRequest()
        assertEquals("PATCH", request.method)
        assertEquals("/api/effect/params", request.path)
        val params = bodyOf(request).getValue("params") as JsonObject
        assertEquals(setOf("speed"), params.keys)
    }

    @Test
    fun `a colour goes out as the object shape, keeping its temperature`() = runTest {
        respondState()
        client.patchParams(mapOf("color" to Params.encodeColor(ColorValue.ofKelvin(3400.0))))

        val params = bodyOf(server.takeRequest()).getValue("params") as JsonObject
        val color = params.getValue("color") as JsonObject
        assertEquals("\"kelvin\"", color.getValue("mode").toString())
        assertEquals("3400.0", color.getValue("kelvin").toString())
    }

    @Test
    fun `a rename sends only the name, and a capture only the flag`() = runTest {
        server.enqueue(MockResponse().setBody(Fixtures.sceneCreate))
        client.renameScene("a1b2c3", "Fireplace")
        assertEquals(setOf("name"), bodyOf(server.takeRequest()).keys)

        respondState()
        client.captureScene("a1b2c3")
        assertEquals(setOf("capture"), bodyOf(server.takeRequest()).keys)
    }

    @Test
    fun `scene endpoints use the documented paths`() = runTest {
        respondState()
        client.recallScene("a1b2c3")
        val recall = server.takeRequest()
        assertEquals("POST", recall.method)
        assertEquals("/api/scenes/a1b2c3/recall", recall.path)

        respondState()
        client.deleteScene("a1b2c3")
        val delete = server.takeRequest()
        assertEquals("DELETE", delete.method)
        assertEquals("/api/scenes/a1b2c3", delete.path)
    }

    @Test
    fun `a scene id is escaped into the path`() = runTest {
        respondState()
        client.deleteScene("a b/c")
        assertEquals("/api/scenes/a%20b%2Fc", server.takeRequest().path)
    }

    @Test
    fun `creating a scene reads the state out of the envelope beside the scene`() = runTest {
        server.enqueue(MockResponse().setResponseCode(201).setBody(Fixtures.sceneCreate))
        val state = client.createScene("Hearth")

        assertEquals(1, state.scenes.size)
        assertEquals("Hearth", state.scenes.first().name)
        assertEquals(state.scenes.first().id, state.activeScene)
    }

    @Test
    fun `the state envelope is unwrapped`() = runTest {
        respondState()
        val state = client.state()
        assertEquals(0.35, state.brightness, 1e-9)
        assertEquals("/api/state", server.takeRequest().path)
    }

    @Test
    fun `effects are unwrapped from their envelope`() = runTest {
        server.enqueue(MockResponse().setBody(Fixtures.effects))
        val effects = client.effects()
        assertTrue(effects.isNotEmpty())
        assertTrue(effects.all { it.name.isNotBlank() })
    }

    @Test
    fun `an error carries the controller's own displayable sentence`() = runTest {
        server.enqueue(
            MockResponse().setResponseCode(404)
                .setBody("""{"error": "not_found", "detail": "no scene with id 'ghost'"}""")
        )
        val failure = runCatching { client.recallScene("ghost") }.exceptionOrNull()

        assertTrue(failure is FcApiException)
        failure as FcApiException
        assertEquals(404, failure.status)
        assertEquals("not_found", failure.code)
        assertEquals("no scene with id 'ghost'", failure.detail)
    }

    @Test
    fun `a failure that is not the documented envelope still surfaces something`() = runTest {
        server.enqueue(MockResponse().setResponseCode(502).setBody("<html>bad gateway</html>"))
        val failure = runCatching { client.state() }.exceptionOrNull() as FcApiException

        assertEquals(502, failure.status)
        assertFalse(failure.message.isNullOrBlank())
    }

    @Test
    fun `every request declares JSON`() = runTest {
        respondState()
        client.setBrightness(0.5)
        assertTrue(
            server.takeRequest().getHeader("Content-Type").orEmpty().startsWith("application/json")
        )
    }

    @Test
    fun `a connection lost while reading the body fails the call rather than hanging`() {
        // The headers arriving does not mean the body will. A call that never
        // comes back leaves its parameter overridden locally for good, showing
        // a value the controller never accepted.
        server.enqueue(
            MockResponse()
                .setBody(Fixtures.state)
                .setHeader("content-type", "application/json")
                .setSocketPolicy(SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY)
        )

        assertThrows(IOException::class.java) {
            runBlocking { withTimeout(5_000) { client.setPower(true) } }
        }
    }
}
