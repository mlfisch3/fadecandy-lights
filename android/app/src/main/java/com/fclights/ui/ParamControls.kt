package com.fclights.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuAnchorType
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.fclights.model.ColorValue
import com.fclights.model.EffectSpec
import com.fclights.model.ParamSpec
import com.fclights.model.Params
import kotlinx.serialization.json.JsonElement

/**
 * Controls built from the schema the controller publishes.
 *
 * Nothing here knows the name of a single effect or parameter. An effect added
 * on the Pi shows up with working controls after nothing more than a reconnect,
 * which is the whole point of `GET /api/effects` being the contract rather than
 * the table in docs/api.md.
 */
@Composable
fun ParamControl(
    spec: ParamSpec,
    values: Map<String, JsonElement>,
    onChange: (JsonElement) -> Unit,
    onColorChange: (ColorValue) -> Unit,
    onCommit: () -> Unit,
) {
    when (spec.type) {
        "float" -> FloatParam(spec, values, onChange, onCommit)
        "int" -> IntParam(spec, values, onChange, onCommit)
        "bool" -> BoolParam(spec, values, onChange, onCommit)
        "enum" -> EnumParam(spec, values, onChange, onCommit)
        "color" -> ColorParam(spec, values, onColorChange, onCommit)
        // A type this build has never heard of. Say so rather than silently
        // dropping a control the user is looking for.
        else -> UnsupportedParam(spec)
    }
}

@Composable
private fun ParamHeader(spec: ParamSpec, valueText: String?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            spec.displayLabel,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f),
        )
        if (valueText != null) {
            Text(valueText, style = MaterialTheme.typography.bodyMedium, fontFamily = FontFamily.Monospace)
        }
    }
}

@Composable
private fun ParamDescription(spec: ParamSpec) {
    if (spec.description.isNotBlank()) {
        Text(
            spec.description,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun FloatParam(
    spec: ParamSpec,
    values: Map<String, JsonElement>,
    onChange: (JsonElement) -> Unit,
    onCommit: () -> Unit,
) {
    val value = Params.float(spec, values)
    Column {
        ParamHeader(spec, Params.formatFloat(spec, value))
        Slider(
            value = Params.toSliderPosition(spec, value),
            onValueChange = { onChange(Params.encodeFloat(Params.fromSliderPosition(spec, it))) },
            onValueChangeFinished = onCommit,
            valueRange = 0f..1f,
        )
        ParamDescription(spec)
    }
}

@Composable
private fun IntParam(
    spec: ParamSpec,
    values: Map<String, JsonElement>,
    onChange: (JsonElement) -> Unit,
    onCommit: () -> Unit,
) {
    val value = Params.int(spec, values)
    val min = spec.minimum ?: 0.0
    val max = spec.maximum ?: 100.0
    Column {
        ParamHeader(spec, value.toString())
        Slider(
            value = value.toFloat(),
            onValueChange = { onChange(Params.encodeInt(Params.quantiseInt(spec, it.toDouble()))) },
            onValueChangeFinished = onCommit,
            valueRange = min.toFloat()..max.toFloat(),
        )
        ParamDescription(spec)
    }
}

@Composable
private fun BoolParam(
    spec: ParamSpec,
    values: Map<String, JsonElement>,
    onChange: (JsonElement) -> Unit,
    onCommit: () -> Unit,
) {
    val value = Params.bool(spec, values)
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                spec.displayLabel,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.weight(1f),
            )
            Switch(
                checked = value,
                onCheckedChange = {
                    onChange(Params.encodeBool(it))
                    onCommit()
                },
            )
        }
        ParamDescription(spec)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun EnumParam(
    spec: ParamSpec,
    values: Map<String, JsonElement>,
    onChange: (JsonElement) -> Unit,
    onCommit: () -> Unit,
) {
    val value = Params.choice(spec, values)
    Column {
        ParamHeader(spec, null)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            spec.choices.orEmpty().forEach { choice ->
                FilterChip(
                    selected = choice == value,
                    onClick = {
                        onChange(Params.encodeChoice(choice))
                        onCommit()
                    },
                    label = { Text(choice) },
                )
            }
        }
        ParamDescription(spec)
    }
}

@Composable
private fun ColorParam(
    spec: ParamSpec,
    values: Map<String, JsonElement>,
    onChange: (ColorValue) -> Unit,
    onCommit: () -> Unit,
) {
    ColorControl(
        spec = spec,
        value = Params.color(spec, values),
        onChange = onChange,
        onCommit = onCommit,
    )
}

@Composable
private fun UnsupportedParam(spec: ParamSpec) {
    Column {
        ParamHeader(spec, null)
        Text(
            "This build has no control for a \"${spec.type}\" parameter. Update the app.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.error,
        )
    }
}

/** Picks the running effect from whatever the controller published. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EffectPicker(effects: List<EffectSpec>, selected: String, onSelect: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    val label = effects.firstOrNull { it.name == selected }?.title ?: selected

    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = it },
    ) {
        OutlinedTextField(
            value = label,
            onValueChange = {},
            readOnly = true,
            label = { Text("Effect") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier
                .menuAnchor(ExposedDropdownMenuAnchorType.PrimaryNotEditable)
                .fillMaxWidth(),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            effects.forEach { effect ->
                DropdownMenuItem(
                    text = { Text(effect.title) },
                    onClick = {
                        expanded = false
                        if (effect.name != selected) onSelect(effect.name)
                    },
                )
            }
        }
    }
}
