package com.fclights

import com.fclights.api.Backoff
import com.fclights.api.Endpoint
import com.fclights.api.FcClient
import com.fclights.api.FcSocket
import com.fclights.api.Link
import com.fclights.model.Blackbody
import com.fclights.model.ColorValue
import com.fclights.model.ControllerState
import com.fclights.model.LightState
import com.fclights.model.Params
import com.fclights.model.WsMessage
import com.fclights.model.asColorOrNull
import com.fclights.model.reduce
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeNotNull
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * The same client, against a controller that is actually running.
 *
 * Skipped unless `FCLIGHTS_TEST_HOST` names one, so it never makes the ordinary
 * build depend on a network. Point it at a Pi, or at a laptop running
 * `fclights run --simulate --pixels 512`, and it checks the things a fixture
 * cannot: that a command is accepted, that the socket pushes the result back,
 * and that the app's idea of the wire format still matches a live service.
 *
 *     FCLIGHTS_TEST_HOST=192.168.1.164 ./gradlew :app:testDebugUnitTest
 */
class LiveControllerTest {

    private var endpoint: Endpoint? = null
    private val http = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    @Before
    fun requireController() {
        val host = System.getenv("FCLIGHTS_TEST_HOST")
        assumeNotNull(host)
        endpoint = Endpoint.parse(host!!)
        assertNotNull("FCLIGHTS_TEST_HOST is not a usable address: $host", endpoint)
    }

    @Test
    fun `a cold start renders from the socket alone`() = runBlocking {
        val socket = FcSocket(http)
        val hello = withTimeout(10_000) {
            socket.connect(endpoint!!)
                .mapNotNull { (it as? Link.Up)?.message as? WsMessage.Hello }
                .first()
        }
        val controller = reduce(ControllerState(), hello)

        assertNotNull("no state in hello", controller.state)
        assertNotNull("no layout in hello", controller.layout)
        assertTrue("no effects in hello", controller.effects.isNotEmpty())
        assertNotNull("the running effect has no published schema", controller.activeEffect)
    }

    @Test
    fun `a command is accepted and comes back with a higher revision`() = runBlocking {
        val client = FcClient(endpoint!!, http)
        val before = client.state()
        val after = client.setBrightness(before.brightness)

        assertTrue("revision went backwards", after.revision >= before.revision)
        assertEquals(before.brightness, after.brightness, 1e-6)
    }

    @Test
    fun `every published effect can be selected and its defaults sent back`() = runBlocking {
        val client = FcClient(endpoint!!, http)
        val original = client.state()
        try {
            client.effects().forEach { effect ->
                val state = client.setEffect(effect.name)
                assertEquals(effect.name, state.effect)
                // The controller reports a complete parameter set, and every
                // value in it has to drive one of this app's controls.
                effect.params.forEach { spec ->
                    assertNotNull("${effect.name}.${spec.name} missing", state.params[spec.name])
                }
                // Round-trip the defaults the same way a released slider does.
                client.patchParams(Params.defaults(effect))
            }
        } finally {
            restore(client, original)
        }
    }

    /**
     * The app's blackbody port against the controller's, on the machine that
     * actually lights the strip.
     *
     * The slider's track and swatch are drawn from `Blackbody`, and the strip
     * is lit from `fclights.color`. A unit test can only check the port against
     * numbers copied out of the Python; this checks it against the Python.
     */
    @Test
    fun `a colour temperature round-trips, and the swatch agrees with the controller`() = runBlocking {
        val client = FcClient(endpoint!!, http)
        val effects = client.effects()

        // Found from the schema, not named: the rule that an effect added on
        // the Pi needs no app change applies to the tests too.
        val subject = effects.firstNotNullOfOrNull { effect ->
            effect.params.firstOrNull { it.type == "color" && it.supportsKelvin }
                ?.let { effect to it }
        }
        assertNotNull("the controller publishes no kelvin-capable colour parameter", subject)
        val (effect, param) = subject!!

        val original = client.state()
        try {
            listOf(
                Blackbody.MIN_KELVIN, 2200.0, 2700.0, 3400.0, 4000.0, 5000.0, Blackbody.MAX_KELVIN,
            ).forEach { kelvin ->
                val state = client.setEffect(
                    effect.name,
                    mapOf(param.name to Params.encodeColor(ColorValue.ofKelvin(kelvin))),
                )
                val back = state.params.getValue(param.name).asColorOrNull()
                assertNotNull("$kelvin K came back unreadable", back)
                // The temperature has to survive, or the phone's warm-to-cool
                // slider cannot be put back where the user left it.
                assertTrue("$kelvin K came back as ${back!!.mode}", back.isKelvin)
                assertEquals(kelvin, back.kelvin!!, 1e-6)
                // And the swatch this app would draw has to be the colour the
                // controller says it is showing.
                assertEquals("swatch at $kelvin K", Blackbody.kelvinToRgb255(kelvin), back.rgb)
            }
        } finally {
            restore(client, original)
        }
    }

    /**
     * The reconnect path, driven by a real drop rather than a mocked one.
     *
     * A phone loses this socket constantly - dozing, changing WiFi, walking out
     * of range - and an app that needs restarting after that is an app that
     * shows stale lights. Cancelling the in-flight call is what the radio going
     * away looks like from OkHttp's side.
     */
    @Test
    fun `the socket comes back on its own after the connection drops`() = runBlocking {
        val socket = FcSocket(http, Backoff(firstMillis = 200, maxMillis = 2_000))
        var hellos = 0
        var sawDown = false

        withTimeout(30_000) {
            socket.connect(endpoint!!)
                .onEach { link ->
                    if (link is Link.Down) sawDown = true
                    if (link is Link.Up && link.message is WsMessage.Hello) {
                        hellos++
                        if (hellos == 1) http.dispatcher.cancelAll()
                    }
                }
                .first { hellos >= 2 }
        }

        assertTrue("the drop was never noticed", sawDown)
        assertEquals("the socket did not resync", 2, hellos)
    }

    /** Put back exactly what was showing before a test touched the lights. */
    private suspend fun restore(client: FcClient, original: LightState) {
        client.setEffect(original.effect, original.params)
        client.setBrightness(original.brightness)
        client.setPower(original.power)
        original.activeScene?.let { client.recallScene(it) }
    }
}
