package com.fclights

import com.fclights.model.Hsv
import com.fclights.model.HsvColor
import com.fclights.model.HsvEdit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
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
 * The colour control's slider positions across a drag.
 *
 * Each of these is a gesture the user can make with one finger: the colour goes
 * out to the controller and comes straight back, and what must survive is the
 * axes the user is not touching.
 */
class HsvEditTest {

    @Test
    fun `hue survives a drag through zero saturation`() {
        var edit = HsvEdit.of(Hsv.toRgb255(200.0, 1.0))
        edit = edit.move(saturation = 0.0)
        assertEquals(listOf(255, 255, 255), edit.rgb)

        // The controller echoes back the white it was sent.
        edit = edit.sync(edit.rgb)
        assertEquals(200.0, edit.hsv.hue, 0.5)

        edit = edit.move(saturation = 1.0)
        assertEquals(Hsv.toRgb255(200.0, 1.0), edit.rgb)
    }

    @Test
    fun `hue and saturation survive a drag through zero shade`() {
        var edit = HsvEdit.of(Hsv.toRgb255(120.0, 0.6))
        edit = edit.move(value = 0.0)
        assertEquals(listOf(0, 0, 0), edit.rgb)

        edit = edit.sync(edit.rgb)
        assertEquals(120.0, edit.hsv.hue, 0.5)
        assertEquals(0.6, edit.hsv.saturation, 0.01)

        edit = edit.move(value = 1.0)
        assertEquals(Hsv.toRgb255(120.0, 0.6), edit.rgb)
    }

    @Test
    fun `a colour changed elsewhere is adopted`() {
        val edit = HsvEdit.of(Hsv.toRgb255(200.0, 1.0)).move(saturation = 0.0)
        val elsewhere = edit.sync(listOf(255, 0, 0))
        assertEquals(0.0, elsewhere.hsv.hue, 0.5)
        assertEquals(1.0, elsewhere.hsv.saturation, 0.01)
    }

    @Test
    fun `the edit is left alone when the colour is the one it just produced`() {
        val edit = HsvEdit.of(listOf(6, 4, 12)).move(hue = 250.0)
        assertSame(edit, edit.sync(edit.rgb))
    }
}
