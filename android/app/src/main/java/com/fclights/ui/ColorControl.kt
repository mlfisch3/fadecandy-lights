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
import androidx.compose.runtime.mutableStateOf
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
import com.fclights.model.HsvEdit
import com.fclights.model.ParamSpec
import com.fclights.model.Params
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
 * That half also carries a shade axis, because a colour parameter is not
 * always a light: `wipe.background` defaults to black and `twinkle.background`
 * to a near-black blue, and the app builds its controls from the published
 * schema, so it has to be able to reach them. Shade is a property of the
 * colour - how dark this particular colour is - and not a second master
 * brightness, which scales the whole installation. The kelvin half has no such
 * axis on purpose: the controller re-derives a temperature with
 * `kelvin_to_rgb`, normalising the brightest channel, so a dimmed kelvin
 * colour cannot be expressed in the API at all and the slider would do
 * nothing.
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
            ColourSliders(value, onChange)
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
            onValueChange = {
                onChange(ColorValue.ofKelvin(Params.quantiseKelvin(spec, min + it * (max - min))), false)
            },
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

/**
 * Hue, saturation and shade.
 *
 * The sliders read from an [HsvEdit] rather than from the colour itself: the
 * round trip through RGB loses the hue of any grey and both axes of black, so
 * a control that re-derived its positions would throw away the colour the user
 * was in the middle of choosing the moment they dragged either axis to zero.
 */
@Composable
private fun ColourSliders(value: ColorValue, onChange: (ColorValue, Boolean) -> Unit) {
    val editState = remember { mutableStateOf(HsvEdit.of(value.rgb)) }
    val edit = editState.value.sync(value.rgb)
    val hsv = edit.hsv

    fun emit(next: HsvEdit, committed: Boolean) {
        editState.value = next
        onChange(ColorValue.ofRgb(next.rgb[0], next.rgb[1], next.rgb[2]), committed)
    }

    val hueTrack = remember {
        List(13) { Color.hsv(it * 30f % 360f, 1f, 1f) }
    }
    val saturationTrack = remember(hsv.hue) {
        listOf(Color.hsv(hsv.hue.toFloat(), 0f, 1f), Color.hsv(hsv.hue.toFloat(), 1f, 1f))
    }
    val shadeTrack = remember(hsv.hue, hsv.saturation) {
        listOf(Color.Black, Color.hsv(hsv.hue.toFloat(), hsv.saturation.toFloat(), 1f))
    }

    Column {
        TrackLabel("Hue")
        GradientSlider(
            colors = hueTrack,
            value = (hsv.hue / 360.0).toFloat(),
            onValueChange = { emit(edit.move(hue = it.toDouble() * 360.0), false) },
            onValueChangeFinished = { emit(editState.value, true) },
        )
        Spacer(Modifier.height(4.dp))
        TrackLabel("Saturation")
        GradientSlider(
            colors = saturationTrack,
            value = hsv.saturation.toFloat(),
            onValueChange = { emit(edit.move(saturation = it.toDouble()), false) },
            onValueChangeFinished = { emit(editState.value, true) },
        )
        Spacer(Modifier.height(4.dp))
        TrackLabel("Shade - how dark this colour is")
        GradientSlider(
            colors = shadeTrack,
            value = hsv.value.toFloat(),
            onValueChange = { emit(edit.move(value = it.toDouble()), false) },
            onValueChangeFinished = { emit(editState.value, true) },
        )
    }
}

@Composable
private fun TrackLabel(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 10.dp),
    )
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
