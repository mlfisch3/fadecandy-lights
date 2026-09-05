package com.fclights.model

import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Hue/saturation, for the colour control's non-white half.
 *
 * The picker offers hue and saturation but not value, because on a light
 * fitting "how bright" is already master brightness and the power governor's
 * job; a third slider that also dims would just be a second, worse brightness
 * control. So a colour is always chosen at full value and dimmed globally.
 */
object Hsv {

    /** Hue 0..360 and saturation 0..1 to 0..255 components, at full value. */
    fun toRgb255(hue: Double, saturation: Double): List<Int> {
        val h = ((hue % 360.0) + 360.0) % 360.0
        val s = saturation.coerceIn(0.0, 1.0)
        val c = s
        val x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
        val m = 1.0 - c
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

    /** 0..255 components back to hue 0..360 and saturation 0..1. */
    fun fromRgb255(rgb: List<Int>): Pair<Double, Double> {
        val r = (rgb.getOrElse(0) { 0 }).coerceIn(0, 255) / 255.0
        val g = (rgb.getOrElse(1) { 0 }).coerceIn(0, 255) / 255.0
        val b = (rgb.getOrElse(2) { 0 }).coerceIn(0, 255) / 255.0
        val max = maxOf(r, g, b)
        val min = minOf(r, g, b)
        val delta = max - min
        if (delta <= 0.0 || max <= 0.0) return 0.0 to 0.0
        val hue = when (max) {
            r -> 60.0 * (((g - b) / delta) % 6.0)
            g -> 60.0 * (((b - r) / delta) + 2.0)
            else -> 60.0 * (((r - g) / delta) + 4.0)
        }
        return (((hue % 360.0) + 360.0) % 360.0) to (delta / max)
    }
}
