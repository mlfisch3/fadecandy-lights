package com.fclights

import com.fclights.model.Hsv
import org.junit.Assert.assertEquals
import org.junit.Test

class HsvTest {

    @Test
    fun `primaries land where they should`() {
        assertEquals(listOf(255, 0, 0), Hsv.toRgb255(0.0, 1.0))
        assertEquals(listOf(0, 255, 0), Hsv.toRgb255(120.0, 1.0))
        assertEquals(listOf(0, 0, 255), Hsv.toRgb255(240.0, 1.0))
    }

    @Test
    fun `zero saturation is white, whatever the hue`() {
        assertEquals(listOf(255, 255, 255), Hsv.toRgb255(210.0, 0.0))
    }

    @Test
    fun `hue and saturation survive a round trip`() {
        listOf(0.0, 35.0, 120.0, 200.0, 300.0, 359.0).forEach { hue ->
            listOf(0.25, 0.5, 1.0).forEach { saturation ->
                val (h, s) = Hsv.fromRgb255(Hsv.toRgb255(hue, saturation))
                assertEquals("hue $hue", hue, h, 1.5)
                assertEquals("saturation $saturation", saturation, s, 0.01)
            }
        }
    }

    @Test
    fun `hue wraps rather than clamping`() {
        assertEquals(Hsv.toRgb255(10.0, 1.0), Hsv.toRgb255(370.0, 1.0))
        assertEquals(Hsv.toRgb255(350.0, 1.0), Hsv.toRgb255(-10.0, 1.0))
    }

    @Test
    fun `black reads as an unsaturated hue rather than dividing by zero`() {
        assertEquals(0.0 to 0.0, Hsv.fromRgb255(listOf(0, 0, 0)))
    }
}
