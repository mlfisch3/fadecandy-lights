package com.fclights

import com.fclights.model.ControllerState
import com.fclights.model.EffectSpec
import com.fclights.model.LightState
import com.fclights.model.PowerStatus
import com.fclights.model.Status
import com.fclights.model.WsMessage
import com.fclights.model.applyState
import com.fclights.model.decodeWsMessage
import com.fclights.model.reduce
import com.fclights.model.retirePending
import com.fclights.model.retirePendingBrightness
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The reduction that keeps two phones showing the same thing.
 *
 * The rule is the one docs/api.md states: a state carrying a revision lower
 * than one already applied is discarded, and nothing else about ordering is
 * assumed.
 */
class ControllerStateTest {

    private fun state(revision: Long, brightness: Double = 0.5, effect: String = "solid") =
        LightState(power = true, brightness = brightness, effect = effect, revision = revision)

    @Test
    fun `hello replaces everything`() {
        val hello = decodeWsMessage(Fixtures.hello) as WsMessage.Hello
        val result = reduce(ControllerState(), hello)

        assertNotNull(result.state)
        assertNotNull(result.layout)
        assertTrue(result.effects.isNotEmpty())
        assertEquals("1.0.0", result.version)
    }

    @Test
    fun `a newer state is applied`() {
        val start = ControllerState(state = state(4))
        val result = reduce(start, WsMessage.StateChanged(state(5, brightness = 0.9)))
        assertEquals(0.9, result.state!!.brightness, 1e-9)
    }

    @Test
    fun `a stale state is discarded`() {
        val start = ControllerState(state = state(9, brightness = 0.9))
        val result = reduce(start, WsMessage.StateChanged(state(8, brightness = 0.1)))
        assertEquals(0.9, result.state!!.brightness, 1e-9)
    }

    @Test
    fun `the same revision is applied, because equal revisions describe the same state`() {
        val start = ControllerState(state = state(7, brightness = 0.9))
        val result = applyState(start, state(7, brightness = 0.9))
        assertEquals(0.9, result.state!!.brightness, 1e-9)
    }

    @Test
    fun `a reconnect resyncs even if the controller restarted and counted down`() {
        // The service restores its last state on boot, so a hello is the truth
        // whatever revision it carries.
        val start = ControllerState(state = state(900))
        val hello = WsMessage.Hello(
            version = "1.0.0",
            state = state(1, brightness = 0.2),
            layout = null,
            effects = listOf(EffectSpec(name = "solid")),
            status = null,
        )
        val result = reduce(start, hello)
        assertEquals(0.2, result.state!!.brightness, 1e-9)
    }

    @Test
    fun `a hello with no effects keeps the ones already known`() {
        val start = ControllerState(effects = listOf(EffectSpec(name = "solid")))
        val hello = WsMessage.Hello("1.0.0", state(1), null, emptyList(), null)
        assertEquals(1, reduce(start, hello).effects.size)
    }

    @Test
    fun `telemetry updates the power readout without touching the state`() {
        val start = ControllerState(state = state(4))
        val status = Status(pixelCount = 512, power = PowerStatus(clamped = true, scale = 0.76))
        val result = reduce(start, WsMessage.Telemetry(status))

        assertSame(start.state, result.state)
        assertEquals(true, result.status!!.power.clamped)
    }

    @Test
    fun `a message this build does not understand changes nothing`() {
        val start = ControllerState(state = state(4))
        assertSame(start, reduce(start, WsMessage.Unknown("zones")))
        assertSame(start, reduce(start, WsMessage.Pong))
    }

    @Test
    fun `the active effect is looked up by name in what the controller published`() {
        val start = ControllerState(
            state = state(1, effect = "fire"),
            effects = listOf(EffectSpec(name = "solid"), EffectSpec(name = "fire", displayName = "Fire")),
        )
        assertEquals("Fire", start.activeEffect!!.title)
    }

    @Test
    fun `an effect the controller runs but did not publish is simply absent`() {
        val start = ControllerState(
            state = state(1, effect = "aurora"),
            effects = listOf(EffectSpec(name = "solid")),
        )
        assertEquals(null, start.activeEffect)
    }
}

/**
 * Which local overrides a finished send is entitled to drop.
 *
 * A control's value is held locally while it is being dragged, so a state push
 * that is behind the finger does not yank it back. The send that confirms a
 * value earns the right to drop it - and nothing more: a finger that moved on
 * while the request was in flight has already put a newer value under that key.
 */
class RetirePendingTest {

    private fun params(vararg pairs: Pair<String, Double>) =
        pairs.associate { (name, value) -> name to JsonPrimitive(value) }

    @Test
    fun `a confirmed value is dropped`() {
        assertEquals(
            emptyMap<String, Any>(),
            retirePending(params("color" to 1.0), params("color" to 1.0)),
        )
    }

    @Test
    fun `a value the finger moved on to outlives the reply to the older send`() {
        // Release at A, then re-grab and drag towards B before A's reply lands.
        val newer = params("color" to 2.0)
        assertEquals(newer, retirePending(newer, params("color" to 1.0)))
    }

    @Test
    fun `a failed send does not discard a drag that has moved on`() {
        // Same subtraction runs on the error path, so the same rule has to hold.
        val newer = params("speed" to 0.4)
        assertEquals(newer, retirePending(newer, params("speed" to 0.2)))
    }

    @Test
    fun `only the keys that were sent are considered`() {
        val pending = params("color" to 1.0, "speed" to 0.2)
        assertEquals(params("speed" to 0.2), retirePending(pending, params("color" to 1.0)))
    }

    @Test
    fun `brightness follows the same rule`() {
        assertEquals(null, retirePendingBrightness(0.4, 0.4))
        assertEquals(0.7, retirePendingBrightness(0.7, 0.4))
        assertEquals(null, retirePendingBrightness(null, 0.4))
    }
}
