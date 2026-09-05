package com.fclights

import com.fclights.model.Blackbody
import com.fclights.model.ColorValue
import com.fclights.model.EffectSpec
import com.fclights.model.FcJson
import com.fclights.model.ParamSpec
import com.fclights.model.Params
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Building controls from a schema this app has never seen before. */
class ParamsTest {

    private val speed = ParamSpec(
        name = "speed", type = "float", default = JsonPrimitive(0.15),
        label = "Speed", minimum = 0.0, maximum = 5.0, step = 0.01, unit = "Hz",
    )
    private val period = ParamSpec(
        name = "period", type = "float", default = JsonPrimitive(900.0),
        label = "Cycle Length", minimum = 10.0, maximum = 21600.0, step = 10.0, unit = "s",
    )
    private val seed = ParamSpec(
        name = "seed", type = "int", default = JsonPrimitive(0),
        label = "Seed", minimum = 0.0, maximum = 65535.0,
    )
    private val easing = ParamSpec(
        name = "easing", type = "enum", default = JsonPrimitive("smooth"),
        label = "Easing", choices = listOf("smooth", "linear"),
    )

    @Test
    fun `a value the controller reports wins over the schema default`() {
        assertEquals(0.7, Params.float(speed, mapOf("speed" to JsonPrimitive(0.7))), 1e-9)
    }

    @Test
    fun `a parameter missing from the state falls back to its declared default`() {
        assertEquals(0.15, Params.float(speed, emptyMap()), 1e-9)
        assertEquals(0, Params.int(seed, emptyMap()))
        assertEquals("smooth", Params.choice(easing, emptyMap()))
    }

    @Test
    fun `a value of the wrong type falls back rather than crashing the screen`() {
        assertEquals(0.15, Params.float(speed, mapOf("speed" to JsonPrimitive("fast"))), 1e-9)
        assertEquals("smooth", Params.choice(easing, mapOf("easing" to JsonPrimitive(3))))
    }

    @Test
    fun `a slider value is snapped to the schema's step and clamped to its range`() {
        assertEquals(0.33, Params.quantiseFloat(speed, 0.3339), 1e-9)
        assertEquals(5.0, Params.quantiseFloat(speed, 6.0), 1e-9)
        assertEquals(0.0, Params.quantiseFloat(speed, -1.0), 1e-9)
    }

    @Test
    fun `snapping never lands outside the declared range`() {
        // A 400 from the controller at the end of a drag is the failure this
        // guards: the top of the track must round to exactly the maximum.
        listOf(speed, period).forEach { spec ->
            val top = Params.fromSliderPosition(spec, 1f)
            val bottom = Params.fromSliderPosition(spec, 0f)
            assertTrue("${spec.name} top $top", top <= spec.maximum!! + 1e-9)
            assertTrue("${spec.name} bottom $bottom", bottom >= spec.minimum!! - 1e-9)
            assertEquals("${spec.name} top", spec.maximum!!, top, 1e-6)
            assertEquals("${spec.name} bottom", spec.minimum!!, bottom, 1e-6)
        }
    }

    @Test
    fun `a range spanning orders of magnitude gets a log track`() {
        assertTrue(Params.isWideRange(period))
        assertFalse(Params.isWideRange(speed))

        // The midpoint of a log track is the geometric mean, so the short end
        // of a six-hour range stays reachable with a fingertip.
        val middle = Params.fromSliderPosition(period, 0.5f)
        assertTrue("midpoint was $middle", middle in 400.0..600.0)
    }

    @Test
    fun `slider positions round-trip through the value`() {
        listOf(speed, period, seed).forEach { spec ->
            listOf(0f, 0.25f, 0.5f, 0.75f, 1f).forEach { position ->
                val value = Params.fromSliderPosition(spec, position)
                val back = Params.toSliderPosition(spec, value)
                assertEquals("${spec.name} at $position", position, back, 0.01f)
            }
        }
    }

    @Test
    fun `an integer parameter stays an integer on the wire`() {
        val encoded = Params.encodeInt(Params.quantiseInt(seed, 12.6))
        assertEquals("13", encoded.toString())
    }

    @Test
    fun `a long cycle length reads as a duration, not a pile of seconds`() {
        assertEquals("15 min", Params.formatFloat(period, 900.0))
        assertEquals("6 h", Params.formatFloat(period, 21600.0))
        assertEquals("1 h 30 min", Params.formatFloat(period, 5400.0))
        assertEquals("30 s", Params.formatFloat(period, 30.0))
    }

    @Test
    fun `a float keeps the precision its step implies, with its unit`() {
        assertEquals("0.15 Hz", Params.formatFloat(speed, 0.15))
    }

    @Test
    fun `a colour parameter falls back to its kelvin default`() {
        val color = ParamSpec(
            name = "color", type = "color", default = JsonPrimitive("nonsense"),
            supportsKelvin = true, kelvinRange = listOf(1800.0, 6500.0), kelvinDefault = 2700.0,
        )
        val value = Params.color(color, emptyMap())
        assertTrue(value.isKelvin)
        assertEquals(2700.0, value.kelvin!!, 1e-9)
    }

    @Test
    fun `a colour encodes to the object shape the API expects`() {
        val encoded = Params.encodeColor(ColorValue.ofKelvin(2700.0)) as JsonObject
        assertEquals("kelvin", encoded.getValue("mode").let { (it as JsonPrimitive).content })
        assertTrue(encoded.containsKey("kelvin"))
        assertTrue(encoded.containsKey("rgb"))

        val rgb = Params.encodeColor(ColorValue.ofRgb(255, 170, 80)) as JsonObject
        assertEquals("rgb", (rgb.getValue("mode") as JsonPrimitive).content)
        assertFalse("an rgb colour must not claim a temperature", rgb.containsKey("kelvin"))
    }

    @Test
    fun `every published effect renders from its defaults alone`() {
        // The state a fresh effect selection produces: the controller fills in
        // the declared defaults, and each one has to drive a control.
        val root = FcJson.parseToJsonElement(Fixtures.effects) as JsonObject
        val effects = FcJson.decodeFromJsonElement(
            ListSerializer(EffectSpec.serializer()),
            root.getValue("effects"),
        )
        effects.forEach { effect ->
            val values = Params.defaults(effect)
            effect.params.forEach { spec ->
                when (spec.type) {
                    "float" -> {
                        val v = Params.float(spec, values)
                        assertTrue("${effect.name}.${spec.name} = $v", v in spec.minimum!!..spec.maximum!!)
                        assertTrue(Params.formatFloat(spec, v).isNotBlank())
                    }
                    "int" -> {
                        val v = Params.int(spec, values)
                        assertTrue("${effect.name}.${spec.name} = $v", v.toDouble() in spec.minimum!!..spec.maximum!!)
                    }
                    "enum" -> assertTrue(Params.choice(spec, values) in spec.choices!!)
                    "color" -> {
                        val c = Params.color(spec, values)
                        assertEquals(3, c.rgb.size)
                        if (c.isKelvin) {
                            assertTrue(c.kelvin!! in Blackbody.MIN_KELVIN..Blackbody.MAX_KELVIN)
                        }
                    }
                    "bool" -> Params.bool(spec, values)
                }
            }
        }
    }
}
