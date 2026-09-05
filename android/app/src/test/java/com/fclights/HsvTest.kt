package com.fclights

import com.fclights.model.Hsv
import com.fclights.model.HsvColor
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
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
    fun `value darkens the colour without moving it`() {
        assertEquals(listOf(0, 0, 0), Hsv.toRgb255(210.0, 1.0, 0.0))
        assertEquals(listOf(128, 0, 0), Hsv.toRgb255(0.0, 1.0, 0.5))
        // The controller's twinkle background, near-black and still blue.
        val (hue, saturation, value) = Hsv.fromRgb255(listOf(6, 4, 12))
        assertEquals(listOf(6, 4, 12), Hsv.toRgb255(hue, saturation, value))
    }

    @Test
    fun `hue and saturation survive a round trip`() {
        listOf(0.0, 35.0, 120.0, 200.0, 300.0, 359.0).forEach { hue ->
            listOf(0.25, 0.5, 1.0).forEach { saturation ->
                val hsv = Hsv.fromRgb255(Hsv.toRgb255(hue, saturation))
                assertEquals("hue $hue", hue, hsv.hue, 1.5)
                assertEquals("saturation $saturation", saturation, hsv.saturation, 0.01)
                assertEquals("value", 1.0, hsv.value, 1e-9)
            }
        }
    }

    @Test
    fun `a dark colour comes back as the colour it was`() {
        // At a low value the 8-bit grid is coarse enough that the angle moves a
        // little, so the property that matters is the colour, not the numbers.
        listOf(0.0, 35.0, 120.0, 200.0, 300.0, 359.0).forEach { hue ->
            listOf(0.25, 0.5, 1.0).forEach { saturation ->
                listOf(0.05, 0.1, 0.6, 1.0).forEach { value ->
                    val rgb = Hsv.toRgb255(hue, saturation, value)
                    val hsv = Hsv.fromRgb255(rgb)
                    assertEquals(
                        "$hue/$saturation/$value",
                        rgb,
                        Hsv.toRgb255(hsv.hue, hsv.saturation, hsv.value),
                    )
                }
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
        assertEquals(HsvColor(0.0, 0.0, 0.0), Hsv.fromRgb255(listOf(0, 0, 0)))
    }
}

/**
 * The colour control's slider positions across the gestures a user makes.
 *
 * Each test walks what the control does with one finger: [Hsv.axesFor] gives
 * the positions drawn and sent, a drag replaces the remembered axes, and the
 * colour goes out to the controller and comes straight back. What must survive
 * is the axes the user is not touching; what must not survive is an edit the
 * user has stopped making once the colour changes somewhere else.
 */
class ColourAxesTest {

    private fun emitted(axes: HsvColor) = Hsv.toRgb255(axes.hue, axes.saturation, axes.value)

    @Test
    fun `hue survives a drag through zero saturation`() {
        val blue = Hsv.toRgb255(200.0, 1.0)
        var remembered = Hsv.axesFor(blue, Hsv.fromRgb255(blue))

        remembered = remembered.copy(saturation = 0.0)
        val white = emitted(remembered)
        assertEquals(listOf(255, 255, 255), white)

        // The controller echoes back the white it was sent.
        val shown = Hsv.axesFor(white, remembered)
        assertEquals(200.0, shown.hue, 0.5)
        assertEquals(0.0, shown.saturation, 1e-9)

        assertEquals(blue, emitted(shown.copy(saturation = 1.0)))
    }

    @Test
    fun `hue and saturation survive a drag through zero shade`() {
        val green = Hsv.toRgb255(120.0, 0.6)
        var remembered = Hsv.axesFor(green, Hsv.fromRgb255(green))

        remembered = remembered.copy(value = 0.0)
        val black = emitted(remembered)
        assertEquals(listOf(0, 0, 0), black)

        val shown = Hsv.axesFor(black, remembered)
        assertEquals(120.0, shown.hue, 0.5)
        assertEquals(0.6, shown.saturation, 0.01)

        assertEquals(green, emitted(shown.copy(value = 1.0)))
    }

    @Test
    fun `a colour changed elsewhere wins over the axes a drag left behind`() {
        // The user dragged to blue; a scene recall then sets the parameter red.
        val remembered = HsvColor(200.0, 1.0, 1.0)
        val shown = Hsv.axesFor(Hsv.toRgb255(0.0, 1.0), remembered)

        assertEquals(0.0, shown.hue, 0.5)
        assertEquals(1.0, shown.saturation, 0.01)
        assertEquals(1.0, shown.value, 1e-9)
    }

    @Test
    fun `an external black is shown and sent as black, not as the colour it replaced`() {
        // Round-2's failure: the sliders showed the new colour and a release
        // committed the old one. What is drawn and what is sent are now one
        // value, so a release that moves nothing cannot resurrect the red.
        val remembered = HsvColor(0.0, 1.0, 1.0)
        val shown = Hsv.axesFor(listOf(0, 0, 0), remembered)

        assertEquals(0.0, shown.value, 1e-9)
        assertEquals(listOf(0, 0, 0), emitted(shown))
    }

    @Test
    fun `what is drawn is the colour on the wire, whatever the axes remember`() {
        listOf(
            listOf(0, 0, 0),
            listOf(6, 4, 12),
            listOf(255, 255, 255),
            listOf(128, 128, 128),
            listOf(255, 170, 80),
        ).forEach { rgb ->
            listOf(HsvColor(0.0, 0.0, 0.0), HsvColor(200.0, 1.0, 1.0), HsvColor(35.0, 0.4, 0.2))
                .forEach { remembered ->
                    assertEquals("$rgb from $remembered", rgb, emitted(Hsv.axesFor(rgb, remembered)))
                }
        }
    }

    @Test
    fun `a remembered hue is only ever a resume point for an axis the colour lost`() {
        val remembered = HsvColor(200.0, 1.0, 1.0)
        val grey = Hsv.axesFor(listOf(128, 128, 128), remembered)
        assertEquals(200.0, grey.hue, 0.5)
        assertTrue("a grey has no saturation to resume", grey.saturation < 1e-9)
    }
}
