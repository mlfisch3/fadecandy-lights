package com.fclights.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.fclights.model.Blackbody
import com.fclights.model.ColorValue
import com.fclights.model.Hsv
import com.fclights.model.ParamSpec
import kotlin.math.roundToInt

/**
 * The colour control.
 *
 * This installation exists to make apartment light, so the warm-to-cool slider
 * is the control, not a secondary tab: it opens on it whenever the value is a
 * temperature, and the track is painted with the actual blackbody colours so
 * the slider shows what it will do rather than naming a number. The hue and
 * saturation pair behind it is for the times a coloured light is wanted.
 *
 * Value is deliberately absent from the colour picker: how bright the room
 * gets is master brightness, and a second dimmer hidden inside the colour
 * control would only fight it.
 */
@Composable
fun ColorControl(
    spec: ParamSpec,
    value: ColorValue,
    onChange: (ColorValue, Boolean) -> Unit,
) {
    val kelvinAvailable = spec.supportsKelvin
    val showingKelvin = value.isKelvin && kelvinAvailable

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Swatch(value)
            Spacer(Modifier.width(12.dp))
            Text(
                spec.displayLabel,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.weight(1f),
            )
            Text(
                describe(value),
                style = MaterialTheme.typography.bodyMedium,
                fontFamily = FontFamily.Monospace,
            )
        }

        if (kelvinAvailable) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = showingKelvin,
                    onClick = {
                        if (!showingKelvin) {
                            onChange(ColorValue.ofKelvin(spec.kelvinDefault ?: Blackbody.DEFAULT_KELVIN), true)
                        }
                    },
                    label = { Text("Warm - cool") },
                )
                FilterChip(
                    selected = !showingKelvin,
                    onClick = {
                        if (showingKelvin) {
                            val rgb = value.rgb
                            onChange(
                                ColorValue.ofRgb(
                                    rgb.getOrElse(0) { 255 },
                                    rgb.getOrElse(1) { 255 },
                                    rgb.getOrElse(2) { 255 },
                                ),
                                true,
                            )
                        }
                    },
                    label = { Text("Colour") },
                )
            }
        }

        if (showingKelvin) {
            KelvinSlider(spec, value.kelvin ?: Blackbody.DEFAULT_KELVIN, onChange)
        } else {
            HueSaturationSliders(value, onChange)
        }

        if (spec.description.isNotBlank()) {
            Text(
                spec.description,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun KelvinSlider(spec: ParamSpec, kelvin: Double, onChange: (ColorValue, Boolean) -> Unit) {
    val min = spec.kelvinMin
    val max = spec.kelvinMax
    val track = remember(min, max) {
        // Sampled densely enough that the interpolation between stops is
        // invisible; the locus is smooth, so 24 is plenty.
        List(24) { i ->
            val k = min + (max - min) * i / 23.0
            Blackbody.kelvinToRgb(k).let { Color(it[0], it[1], it[2]) }
        }
    }
    Column {
        GradientSlider(
            colors = track,
            value = ((kelvin - min) / (max - min)).toFloat().coerceIn(0f, 1f),
            onValueChange = { onChange(ColorValue.ofKelvin(quantiseKelvin(min + it * (max - min))), false) },
            onValueChangeFinished = { onChange(ColorValue.ofKelvin(kelvin), true) },
        )
        Row {
            Text(
                "${min.roundToInt()} K",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            Text(
                "${max.roundToInt()} K",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** 50 K steps: finer than the eye resolves here, and it keeps the readout still. */
private fun quantiseKelvin(raw: Double): Double = (raw / 50.0).roundToInt() * 50.0

@Composable
private fun HueSaturationSliders(value: ColorValue, onChange: (ColorValue, Boolean) -> Unit) {
    val (hue, saturation) = Hsv.fromRgb255(value.rgb)
    val hueTrack = remember {
        List(13) { Color.hsv(it * 30f % 360f, 1f, 1f) }
    }
    val saturationTrack = remember(hue) {
        listOf(Color.hsv(hue.toFloat(), 0f, 1f), Color.hsv(hue.toFloat(), 1f, 1f))
    }

    fun emit(h: Double, s: Double, committed: Boolean) {
        val rgb = Hsv.toRgb255(h, s)
        onChange(ColorValue.ofRgb(rgb[0], rgb[1], rgb[2]), committed)
    }

    Column {
        GradientSlider(
            colors = hueTrack,
            value = (hue / 360.0).toFloat(),
            onValueChange = { emit(it.toDouble() * 360.0, saturation, false) },
            onValueChangeFinished = { emit(hue, saturation, true) },
        )
        Spacer(Modifier.height(4.dp))
        GradientSlider(
            colors = saturationTrack,
            value = saturation.toFloat(),
            onValueChange = { emit(hue, it.toDouble(), false) },
            onValueChangeFinished = { emit(hue, saturation, true) },
        )
    }
}

/**
 * A slider whose track is painted with the colours it selects between.
 *
 * The Material track is drawn transparent and the gradient sits behind it, so
 * this stays an ordinary Slider - same gesture handling, same thumb, same
 * accessibility - with a different backdrop.
 */
@Composable
private fun GradientSlider(
    colors: List<Color>,
    value: Float,
    onValueChange: (Float) -> Unit,
    onValueChangeFinished: () -> Unit,
) {
    Box(contentAlignment = Alignment.Center) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 10.dp)
                .height(14.dp)
                .clip(RoundedCornerShape(7.dp))
        ) {
            drawRect(
                brush = Brush.linearGradient(
                    colors = colors,
                    start = Offset(0f, 0f),
                    end = Offset(size.width, 0f),
                )
            )
        }
        Slider(
            value = value.coerceIn(0f, 1f),
            onValueChange = onValueChange,
            onValueChangeFinished = onValueChangeFinished,
            valueRange = 0f..1f,
            colors = SliderDefaults.colors(
                activeTrackColor = Color.Transparent,
                inactiveTrackColor = Color.Transparent,
                activeTickColor = Color.Transparent,
                inactiveTickColor = Color.Transparent,
            ),
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun Swatch(value: ColorValue) {
    val rgb = value.rgb
    Box(
        Modifier
            .size(28.dp)
            .clip(RoundedCornerShape(6.dp))
            .background(
                Color(
                    rgb.getOrElse(0) { 0 },
                    rgb.getOrElse(1) { 0 },
                    rgb.getOrElse(2) { 0 },
                )
            )
            .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(6.dp))
    )
}

private fun describe(value: ColorValue): String =
    if (value.isKelvin) {
        "${value.kelvin!!.roundToInt()} K"
    } else {
        val rgb = value.rgb
        "#%02X%02X%02X".format(
            rgb.getOrElse(0) { 0 },
            rgb.getOrElse(1) { 0 },
            rgb.getOrElse(2) { 0 },
        )
    }
