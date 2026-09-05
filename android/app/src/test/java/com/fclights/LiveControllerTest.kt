package com.fclights

import com.fclights.api.Endpoint
import com.fclights.api.FcClient
import com.fclights.api.FcSocket
import com.fclights.api.Link
import com.fclights.model.ControllerState
import com.fclights.model.Params
import com.fclights.model.WsMessage
import com.fclights.model.reduce
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.mapNotNull
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
            client.setEffect(original.effect, original.params)
            client.setBrightness(original.brightness)
            client.setPower(original.power)
        }
    }
}
