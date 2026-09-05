package com.fclights.model

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import java.util.Locale
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.roundToInt
import kotlin.math.roundToLong

/**
 * Reading and writing effect parameter values against a published schema.
 *
 * The app never knows what an effect's parameters are; it is handed a list of
 * [ParamSpec] by the controller and has to build controls from that. These
 * helpers are the whole of the translation between a control's value and the
 * JSON on the wire, which is why they are here and not in the Compose code.
 */
object Params {

    private const val KELVIN_STEP = 50.0

    /**
     * The value to show for [spec], preferring what the controller reports and
     * falling back to the schema's declared default.
     *
     * A state's `params` is documented as complete for the active effect, so
     * the fallback matters only for an effect that is being switched to, or a
     * parameter added on the Pi between two responses.
     */
    fun value(spec: ParamSpec, values: Map<String, JsonElement>): JsonElement =
        values[spec.name] ?: spec.default

    fun defaults(effect: EffectSpec): Map<String, JsonElement> =
        effect.params.associate { it.name to it.default }

    fun float(spec: ParamSpec, values: Map<String, JsonElement>): Double =
        value(spec, values).asDoubleOrNull()
            ?: spec.default.asDoubleOrNull()
            ?: (spec.minimum ?: 0.0)

    fun int(spec: ParamSpec, values: Map<String, JsonElement>): Int =
        value(spec, values).asIntOrNull()
            ?: spec.default.asIntOrNull()
            ?: (spec.minimum?.roundToInt() ?: 0)

    fun bool(spec: ParamSpec, values: Map<String, JsonElement>): Boolean =
        value(spec, values).asBooleanOrNull() ?: spec.default.asBooleanOrNull() ?: false

    fun choice(spec: ParamSpec, values: Map<String, JsonElement>): String =
        value(spec, values).asStringOrNull()
            ?: spec.default.asStringOrNull()
            ?: spec.choices?.firstOrNull()
            ?: ""

    fun color(spec: ParamSpec, values: Map<String, JsonElement>): ColorValue =
        value(spec, values).asColorOrNull()
            ?: spec.default.asColorOrNull()
            ?: ColorValue.ofKelvin(spec.kelvinDefault ?: Blackbody.DEFAULT_KELVIN)

    /**
     * Quantise a slider position to the schema's range and step.
     *
     * The controller rejects a value outside the declared range with a 400, so
     * clamping here is not politeness - it is what stops a rounding error at
     * the end of a drag from failing the call.
     */
    fun quantiseFloat(spec: ParamSpec, raw: Double): Double {
        val min = spec.minimum ?: 0.0
        val max = spec.maximum ?: 1.0
        val step = spec.step
        val clamped = raw.coerceIn(minOf(min, max), maxOf(min, max))
        if (step == null || step <= 0.0) return clamped
        val snapped = min + ((clamped - min) / step).roundToLong() * step
        // Snapping can land a hair outside the range at the top end.
        return snapped.coerceIn(minOf(min, max), maxOf(min, max))
    }

    /**
     * Quantise a colour temperature to the schema's published range.
     *
     * 50 K steps are finer than the eye resolves here and keep the readout
     * still, but the grid is anchored at zero, so its nearest step can fall
     * outside a range whose bounds are not multiples of 50 - and the
     * controller answers 400 for a temperature below its minimum. The range is
     * whatever `kelvin_range` says, never assumed.
     */
    fun quantiseKelvin(spec: ParamSpec, raw: Double): Double {
        val min = spec.kelvinMin
        val max = spec.kelvinMax
        val snapped = (raw / KELVIN_STEP).roundToLong() * KELVIN_STEP
        return snapped.coerceIn(minOf(min, max), maxOf(min, max))
    }

    fun quantiseInt(spec: ParamSpec, raw: Double): Int {
        val min = spec.minimum?.roundToInt() ?: 0
        val max = spec.maximum?.roundToInt() ?: Int.MAX_VALUE
        return raw.roundToInt().coerceIn(minOf(min, max), maxOf(min, max))
    }

    /** Encode a control's value back onto the wire, in the type the schema declares. */
    fun encodeFloat(value: Double): JsonElement = JsonPrimitive(value)
    fun encodeInt(value: Int): JsonElement = JsonPrimitive(value)
    fun encodeBool(value: Boolean): JsonElement = JsonPrimitive(value)
    fun encodeChoice(value: String): JsonElement = JsonPrimitive(value)
    fun encodeColor(value: ColorValue): JsonElement =
        FcJson.encodeToJsonElement(ColorValue.serializer(), value)

    /**
     * Whether a float parameter should be driven by a logarithmic slider.
     *
     * `slowfade.period` runs from 10 seconds to six hours. On a linear track a
     * fingertip covers minutes, and the short end of the range - the part
     * anyone tuning a fade actually visits first - is unreachable. A range
     * spanning two orders of magnitude gets a log track instead, which gives
     * every decade the same amount of travel.
     */
    fun isWideRange(spec: ParamSpec): Boolean {
        val min = spec.minimum ?: return false
        val max = spec.maximum ?: return false
        return min > 0.0 && max / min >= 100.0
    }

    /** A parameter value as a 0..1 slider position, log-scaled where that helps. */
    fun toSliderPosition(spec: ParamSpec, value: Double): Float {
        val min = spec.minimum ?: 0.0
        val max = spec.maximum ?: 1.0
        if (max <= min) return 0f
        val clamped = value.coerceIn(min, max)
        return if (isWideRange(spec)) {
            (ln(clamped / min) / ln(max / min)).toFloat()
        } else {
            ((clamped - min) / (max - min)).toFloat()
        }
    }

    /** The inverse of [toSliderPosition], quantised to the schema's step. */
    fun fromSliderPosition(spec: ParamSpec, position: Float): Double {
        val min = spec.minimum ?: 0.0
        val max = spec.maximum ?: 1.0
        if (max <= min) return min
        val p = position.toDouble().coerceIn(0.0, 1.0)
        val raw = if (isWideRange(spec)) min * exp(p * ln(max / min)) else min + p * (max - min)
        return quantiseFloat(spec, raw)
    }

    /**
     * A short, display-ready rendering of a numeric value, with its unit.
     * Uses the schema's step to decide how many decimals are meaningful.
     */
    fun formatFloat(spec: ParamSpec, value: Double): String {
        if (spec.unit == "s" && value >= 90.0) return formatDuration(value)
        val step = spec.step ?: 0.01
        val decimals = when {
            step >= 1.0 -> 0
            step >= 0.1 -> 1
            step >= 0.01 -> 2
            else -> 3
        }
        val text = String.format(Locale.US, "%.${decimals}f", value)
        return if (spec.unit.isBlank()) text else "$text ${spec.unit}"
    }

    /**
     * Seconds as something readable at a glance. A cycle length of 21600 means
     * nothing; "6 h" means something.
     */
    fun formatDuration(seconds: Double): String {
        val total = seconds.roundToLong()
        return when {
            total < 90L -> "$total s"
            total < 3600L -> {
                val minutes = total / 60L
                val rest = total % 60L
                if (rest == 0L) "$minutes min" else "$minutes min $rest s"
            }
            else -> {
                val hours = total / 3600L
                val minutes = (total % 3600L) / 60L
                if (minutes == 0L) "$hours h" else "$hours h $minutes min"
            }
        }
    }
}
