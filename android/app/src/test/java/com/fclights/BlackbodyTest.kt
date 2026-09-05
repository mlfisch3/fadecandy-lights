package com.fclights

import com.fclights.model.Blackbody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Kotlin blackbody conversion has to agree with the Pi's, because the
 * slider is drawn from this one and the strip is lit from that one. The
 * expected values below were produced by `fclights.color.kelvin_to_rgb255`.
 */
class BlackbodyTest {

    private val golden = mapOf(
        1800.0 to listOf(255, 126, 0),
        2000.0 to listOf(255, 139, 22),
        2200.0 to listOf(255, 150, 47),
        // Either side of the fit's 2222 K and 4000 K piece boundaries, where a
        // transcription slip in the coefficients would show first.
        2222.0 to listOf(255, 151, 49),
        2400.0 to listOf(255, 160, 66),
        2700.0 to listOf(255, 173, 89),
        3000.0 to listOf(255, 184, 109),
        3400.0 to listOf(255, 196, 134),
        4000.0 to listOf(255, 211, 165),
        4001.0 to listOf(255, 211, 165),
        5000.0 to listOf(255, 230, 208),
        6500.0 to listOf(255, 249, 254),
    )

    @Test
    fun `matches the controller's conversion`() {
        golden.forEach { (kelvin, expected) ->
            assertEquals("at $kelvin K", expected, Blackbody.kelvinToRgb255(kelvin))
        }
    }

    @Test
    fun `the brightest channel is always saturated`() {
        var kelvin = Blackbody.MIN_KELVIN
        while (kelvin <= Blackbody.MAX_KELVIN) {
            val rgb = Blackbody.kelvinToRgb(kelvin)
            assertEquals("at $kelvin K", 1.0f, rgb.max(), 1e-6f)
            kelvin += 25.0
        }
    }

    @Test
    fun `warmer temperatures have less blue`() {
        var previous = -1.0f
        var kelvin = Blackbody.MIN_KELVIN
        while (kelvin <= Blackbody.MAX_KELVIN) {
            val blue = Blackbody.kelvinToRgb(kelvin)[2]
            assertTrue("blue fell going up to $kelvin K", blue >= previous)
            previous = blue
            kelvin += 25.0
        }
    }

    @Test
    fun `the default is the warm domestic white the API reports`() {
        assertEquals(listOf(255, 173, 89), Blackbody.kelvinToRgb255(Blackbody.DEFAULT_KELVIN))
    }
}
