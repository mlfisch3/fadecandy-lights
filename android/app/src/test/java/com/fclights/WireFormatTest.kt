package com.fclights

import com.fclights.model.EffectSpec
import com.fclights.model.FcJson
import com.fclights.model.Layout
import com.fclights.model.LightState
import com.fclights.model.Status
import com.fclights.model.WsMessage
import com.fclights.model.asColorOrNull
import com.fclights.model.decodeWsMessage
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Decoding what the controller actually sends. */
class WireFormatTest {

    @Test
    fun `hello carries everything the UI needs to render`() {
        val message = decodeWsMessage(Fixtures.hello)
        assertTrue(message is WsMessage.Hello)
        val hello = message as WsMessage.Hello

        assertEquals("1.0.0", hello.version)
        assertEquals("solid", hello.state.effect)
        assertNotNull(hello.layout)
        assertNotNull(hello.status)
        assertTrue("no effects in hello", hello.effects.isNotEmpty())
        // Every v1 effect the API documents, found by name rather than index.
        val names = hello.effects.map { it.name }.toSet()
        assertTrue(
            "missing effects: ${setOf("solid", "slowfade", "fire", "rainbow") - names}",
            names.containsAll(listOf("solid", "slowfade", "fire", "rainbow")),
        )
    }

    @Test
    fun `a state envelope decodes to the state`() {
        val message = decodeWsMessage(Fixtures.state) as WsMessage.StateChanged
        assertEquals(0.35, message.state.brightness, 1e-9)
        assertEquals(true, message.state.power)
        assertNull(message.state.activeScene)
    }

    @Test
    fun `an unknown message type is ignored rather than fatal`() {
        val message = decodeWsMessage("""{"type": "somethingNew", "payload": {"a": 1}}""")
        assertEquals(WsMessage.Unknown("somethingNew"), message)
    }

    @Test
    fun `a malformed frame decodes to null rather than throwing`() {
        assertNull(decodeWsMessage("not json at all"))
    }

    @Test
    fun `a message whose body does not match falls back to unknown`() {
        // A future server sending a `state` this client cannot read must not
        // take the connection down with it.
        assertEquals(WsMessage.Unknown("state"), decodeWsMessage("""{"type": "state"}"""))
    }

    @Test
    fun `fields the controller may add later are ignored`() {
        val state = FcJson.decodeFromString(
            LightState.serializer(),
            """{"power": true, "brightness": 0.5, "effect": "solid", "params": {},
                "scenes": [], "active_scene": null, "revision": 3, "zones": ["kitchen"]}""",
        )
        assertEquals(3L, state.revision)
    }

    @Test
    fun `every published effect parameter has a control this app can build`() {
        val root = FcJson.parseToJsonElement(Fixtures.effects) as JsonObject
        val effects = FcJson.decodeFromJsonElement(
            ListSerializer(EffectSpec.serializer()),
            root.getValue("effects"),
        )
        val known = setOf("float", "int", "bool", "color", "enum")
        effects.forEach { effect ->
            effect.params.forEach { param ->
                assertTrue("${effect.name}.${param.name} is a ${param.type}", param.type in known)
                assertTrue("${effect.name}.${param.name} has no label", param.displayLabel.isNotBlank())
                if (param.type == "float" || param.type == "int") {
                    // docs/api.md promises both, so a slider can always be built.
                    assertNotNull("${effect.name}.${param.name} minimum", param.minimum)
                    assertNotNull("${effect.name}.${param.name} maximum", param.maximum)
                }
                if (param.type == "enum") {
                    assertTrue("${effect.name}.${param.name} choices", !param.choices.isNullOrEmpty())
                }
                if (param.type == "color") {
                    assertNotNull("${effect.name}.${param.name} default", param.default.asColorOrNull())
                }
            }
        }
    }

    @Test
    fun `a kelvin colour keeps its temperature and carries a swatch`() {
        val root = FcJson.parseToJsonElement(Fixtures.state) as JsonObject
        val state = FcJson.decodeFromJsonElement(
            LightState.serializer(),
            root.getValue("state"),
        )
        val color = state.params.getValue("color").asColorOrNull()!!
        assertTrue(color.isKelvin)
        assertEquals(2700.0, color.kelvin!!, 1e-9)
        assertEquals(listOf(255, 173, 89), color.rgb)
    }

    @Test
    fun `layout is a list of devices, not one board`() {
        val layout = FcJson.decodeFromString(Layout.serializer(), Fixtures.layout)
        assertEquals(512, layout.pixelCount)
        assertEquals(30.3, layout.pixelsPerMetre, 1e-9)
        assertTrue(layout.devices.isNotEmpty())
        assertEquals(layout.devices.first().pixelCount, layout.devices.first().outputs.sumOf { it.count })
    }

    @Test
    fun `status reports the power governor`() {
        val status = FcJson.decodeFromString(Status.serializer(), Fixtures.status)
        assertEquals(512, status.pixelCount)
        assertEquals(24.0, status.power.limitAmps, 1e-9)
        assertTrue(status.power.deliveredAmps <= status.power.limitAmps)
    }
}
