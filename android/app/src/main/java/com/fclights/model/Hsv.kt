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

    /**
     * The slider positions for [rgb], recovering from [remembered] the axes RGB
     * cannot carry.
     *
     * The projection is lossy at the edges: every hue collapses to the same grey
     * at zero saturation, and hue and saturation both vanish at black. Reading
     * the sliders straight back from the colour would therefore snap the hue to
     * red the moment a drag reached either edge, taking the blue the user was
     * choosing with it. So an axis the colour cannot express falls back to the
     * one the user last set, while every axis the colour *can* express comes
     * from the colour - which is what lets a scene recall or another phone win
     * over an edit this control is no longer making.
     */
    fun axesFor(rgb: List<Int>, remembered: HsvColor): HsvColor {
        val shown = fromRgb255(rgb)
        return HsvColor(
            hue = if (shown.value > 0.0 && shown.saturation > 0.0) shown.hue else remembered.hue,
            saturation = if (shown.value > 0.0) shown.saturation else remembered.saturation,
            value = shown.value,
        )
    }
}
