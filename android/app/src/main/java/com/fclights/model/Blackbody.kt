package com.fclights.model

import kotlin.math.pow
import kotlin.math.roundToInt

/**
 * Colour temperature to display-space RGB.
 *
 * This is a port of the Pi's `fclights.color.kelvin_to_rgb`, and has to stay a
 * faithful one: the app draws the warm-to-cool slider's gradient and its swatch
 * from this, while the strip is lit from the Python version, and the two
 * disagreeing would make the slider lie about what the room will look like.
 *
 * The pipeline is kelvin -> CIE 1931 xy on the Planckian locus (the Kim et al.
 * cubic fit) -> XYZ -> linear sRGB -> the sRGB transfer function. It stops
 * there: gamma is fcserver's job on the Pi, and applying it here would correct
 * twice.
 */
object Blackbody {

    const val MIN_KELVIN = 1800.0
    const val MAX_KELVIN = 6500.0
    const val DEFAULT_KELVIN = 2700.0

    private const val LOCUS_MIN_KELVIN = 1667.0
    private const val LOCUS_MAX_KELVIN = 25000.0

    /** CIE XYZ (D65) to linear sRGB, row-major. */
    private val XYZ_TO_LINEAR_SRGB = arrayOf(
        doubleArrayOf(3.2404542, -1.5371385, -0.4985314),
        doubleArrayOf(-0.9692660, 1.8760108, 0.0415560),
        doubleArrayOf(0.0556434, -0.2040259, 1.0572252),
    )

    private fun planckianXy(kelvin: Double): Pair<Double, Double> {
        val t = kelvin.coerceIn(LOCUS_MIN_KELVIN, LOCUS_MAX_KELVIN)
        val inv = 1000.0 / t

        val x = if (t <= 4000.0) {
            -0.2661239 * inv * inv * inv - 0.2343589 * inv * inv + 0.8776956 * inv + 0.179910
        } else {
            -3.0258469 * inv * inv * inv + 2.1070379 * inv * inv + 0.2226347 * inv + 0.240390
        }

        val y = when {
            t <= 2222.0 -> -1.1063814 * x * x * x - 1.34811020 * x * x + 2.18555832 * x - 0.20219683
            t <= 4000.0 -> -0.9549476 * x * x * x - 1.37418593 * x * x + 2.09137015 * x - 0.16748867
            else -> 3.0817580 * x * x * x - 5.87338670 * x * x + 3.75112997 * x - 0.37001483
        }

        return x to y
    }

    private fun encodeSrgb(linear: Double): Double {
        val v = linear.coerceIn(0.0, 1.0)
        return if (v <= 0.0031308) v * 12.92 else 1.055 * v.pow(1.0 / 2.4) - 0.055
    }

    /**
     * Display-space RGB, 0..1, normalised so the brightest channel is 1.0.
     *
     * That is "the whitest white the strip can make at this temperature"; how
     * bright it actually gets is master brightness and the power governor's
     * business, not this function's.
     */
    fun kelvinToRgb(kelvin: Double): FloatArray {
        val (x, y) = planckianXy(kelvin)
        require(y > 0.0) { "$kelvin K does not land on the blackbody locus" }

        // xyY with Y = 1, into XYZ.
        val xyz = doubleArrayOf(x / y, 1.0, (1.0 - x - y) / y)
        val linear = DoubleArray(3) { row ->
            val m = XYZ_TO_LINEAR_SRGB[row]
            // A blackbody at these temperatures sits slightly outside the sRGB
            // gamut at the extremes; the strip cannot make that colour and the
            // nearest in-gamut one is what the eye reads as that temperature.
            (m[0] * xyz[0] + m[1] * xyz[1] + m[2] * xyz[2]).coerceAtLeast(0.0)
        }

        val peak = linear.max()
        require(peak > 0.0) { "$kelvin K converts to no light at all" }
        for (i in linear.indices) linear[i] /= peak

        val encoded = DoubleArray(3) { encodeSrgb(linear[it]) }
        val encodedPeak = encoded.max()
        return FloatArray(3) { (encoded[it] / encodedPeak).toFloat() }
    }

    /** Colour temperature as 0..255 components, matching what the API returns. */
    fun kelvinToRgb255(kelvin: Double): List<Int> =
        kelvinToRgb(kelvin).map { (it.toDouble() * 255.0).roundToInt() }
}
