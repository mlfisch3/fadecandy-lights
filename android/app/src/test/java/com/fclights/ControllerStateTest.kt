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
