package com.fclights.model

import kotlin.math.abs
import kotlin.math.roundToInt

/** A colour on the picker's three axes: hue 0..360, saturation 0..1, value 0..1. */
data class HsvColor(val hue: Double, val saturation: Double, val value: Double)

/**
 * Hue/saturation/value, for the colour control's non-white half.
 *
 * Value is here because a colour parameter is not always a light: the
 * controller publishes `wipe.background` defaulting to `[0, 0, 0]` and
 * `twinkle.background` to `[6, 4, 12]`, and a picker that cannot reach a dark
 * colour cannot express them. It is how dark *this colour* is, which is a
 * different question from how bright the room gets - that is master brightness
 * and the power governor, and a near-black twinkle background at full master
 * brightness is exactly the state the effect wants.
 */
object Hsv {

    /** Hue 0..360, saturation 0..1 and value 0..1 to 0..255 components. */
    fun toRgb255(hue: Double, saturation: Double, value: Double = 1.0): List<Int> {
        val h = ((hue % 360.0) + 360.0) % 360.0
        val s = saturation.coerceIn(0.0, 1.0)
        val v = value.coerceIn(0.0, 1.0)
        val c = v * s
        val x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
        val m = v - c
        val (r, g, b) = when {
            h < 60.0 -> Triple(c, x, 0.0)
            h < 120.0 -> Triple(x, c, 0.0)
            h < 180.0 -> Triple(0.0, c, x)
            h < 240.0 -> Triple(0.0, x, c)
            h < 300.0 -> Triple(x, 0.0, c)
            else -> Triple(c, 0.0, x)
        }
        return listOf(
            ((r + m) * 255.0).roundToInt().coerceIn(0, 255),
            ((g + m) * 255.0).roundToInt().coerceIn(0, 255),
            ((b + m) * 255.0).roundToInt().coerceIn(0, 255),
        )
    }

    /** 0..255 components back to hue, saturation and value. */
    fun fromRgb255(rgb: List<Int>): HsvColor {
        val r = (rgb.getOrElse(0) { 0 }).coerceIn(0, 255) / 255.0
        val g = (rgb.getOrElse(1) { 0 }).coerceIn(0, 255) / 255.0
        val b = (rgb.getOrElse(2) { 0 }).coerceIn(0, 255) / 255.0
        val max = maxOf(r, g, b)
        val min = minOf(r, g, b)
        val delta = max - min
        if (delta <= 0.0 || max <= 0.0) return HsvColor(0.0, 0.0, max)
        val hue = when (max) {
            r -> 60.0 * (((g - b) / delta) % 6.0)
            g -> 60.0 * (((b - r) / delta) + 2.0)
            else -> 60.0 * (((r - g) / delta) + 4.0)
        }
        return HsvColor((((hue % 360.0) + 360.0) % 360.0), delta / max, max)
    }
}

/**
 * What the colour sliders are showing, and the RGB it corresponds to.
 *
 * The projection to RGB is lossy at the edges: every hue collapses to the same
 * grey at zero saturation, and hue and saturation both vanish at zero value. So
 * the control cannot re-derive its slider positions from the round-tripped
 * colour on every frame - drag saturation to zero and the hue slider would snap
 * to red, taking the user's blue with it. This holds the axes the user is
 * actually manipulating and only re-reads them when the colour changed
 * somewhere else: another phone, a scene recall, a different effect.
 */
data class HsvEdit(val hsv: HsvColor, val rgb: List<Int>) {

    /** Adopt [incoming] unless it is the colour this edit itself produced. */
    fun sync(incoming: List<Int>): HsvEdit = if (incoming == rgb) this else of(incoming)

    /** Move one axis, keeping the others exactly where the user left them. */
    fun move(
        hue: Double = hsv.hue,
        saturation: Double = hsv.saturation,
        value: Double = hsv.value,
    ): HsvEdit {
        val next = HsvColor(hue, saturation, value)
        return HsvEdit(next, Hsv.toRgb255(next.hue, next.saturation, next.value))
    }

    companion object {
        fun of(rgb: List<Int>): HsvEdit = HsvEdit(Hsv.fromRgb255(rgb), rgb)
    }
}
