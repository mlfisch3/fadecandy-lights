package com.fclights.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.fclights.model.Scene
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LightsScreen(model: AppViewModel) {
    val ui by model.ui.collectAsStateWithLifecycle()
    var showConnect by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(ui.error) {
        ui.error?.let {
            snackbar.showSnackbar(it)
            model.dismissError()
        }
    }

    // With no address yet there is nothing to show but the way in.
    LaunchedEffect(ui.endpoint) {
        if (ui.endpoint == null) showConnect = true
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("fclights")
                        Text(
                            connectionLine(ui),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { showConnect = true }) {
                        Icon(Icons.Filled.Settings, contentDescription = "Controller address")
                    }
                },
            )
        },
    ) { padding ->
        val state = ui.state
        if (state == null) {
            WaitingForController(ui, Modifier.fillMaxSize().padding(padding)) { showConnect = true }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(padding),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                item { MasterCard(ui, model) }
                item { EffectCard(ui, model) }
                item { ScenesCard(ui, model) }
                item { FooterNotes(ui) }
            }
        }
    }

    if (showConnect) {
        ConnectSheet(
            ui = ui,
            onConnect = { model.connect(it); showConnect = false },
            onRescan = { model.startDiscovery() },
            onDismiss = { showConnect = false },
        )
    }
}

private fun connectionLine(ui: UiState): String {
    val where = ui.endpoint?.toString() ?: "no controller"
    return when (ui.connection) {
        Connection.Connected -> where
        Connection.Connecting -> "connecting to $where"
        Connection.Disconnected ->
            if (ui.endpoint == null) "tap to set an address"
            else "reconnecting to $where"
    }
}

@Composable
private fun WaitingForController(ui: UiState, modifier: Modifier, onOpen: () -> Unit) {
    Box(modifier, contentAlignment = Alignment.Center) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (ui.endpoint != null) {
                CircularProgressIndicator()
                Text("Connecting to ${ui.endpoint}", style = MaterialTheme.typography.bodyMedium)
                if (ui.connectionDetail.isNotBlank()) {
                    Text(
                        ui.connectionDetail,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                Text("No controller set", style = MaterialTheme.typography.titleMedium)
            }
            TextButton(onClick = onOpen) { Text("Change address") }
        }
    }
}

@Composable
private fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(elevation = CardDefaults.cardElevation(defaultElevation = 0.dp)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
private fun MasterCard(ui: UiState, model: AppViewModel) {
    val state = ui.state ?: return
    SectionCard("Lights") {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (state.power) "On" else "Off",
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.weight(1f),
            )
            Switch(checked = state.power, onCheckedChange = { model.setPower(it) })
        }

        Column {
            Row {
                Text("Brightness", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                Text(
                    "${(ui.brightness * 100).roundToInt()}%",
                    style = MaterialTheme.typography.bodyMedium,
                    fontFamily = FontFamily.Monospace,
                )
            }
            Slider(
                value = ui.brightness.toFloat(),
                onValueChange = { model.setBrightness(it.toDouble()) },
                onValueChangeFinished = { model.commitBrightness() },
                valueRange = 0f..1f,
            )
        }

        val power = ui.controller.status?.power
        if (power?.clamped == true) {
            Text(
                "Limited by the power budget: asking for %.1f A, delivering %.1f A of %.0f A."
                    .format(power.requestedAmps, power.deliveredAmps, power.limitAmps),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun EffectCard(ui: UiState, model: AppViewModel) {
    val state = ui.state ?: return
    SectionCard("Effect") {
        EffectPicker(
            effects = ui.effects,
            selected = state.effect,
            onSelect = { model.selectEffect(it) },
        )

        val spec = ui.activeEffect
        if (spec == null) {
            Text(
                "The controller is running \"${state.effect}\", which it did not publish a schema for.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            if (spec.description.isNotBlank()) {
                Text(
                    spec.description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            spec.params.forEach { param ->
                ParamControl(
                    spec = param,
                    values = ui.paramValues,
                    onChange = { value -> model.setParam(param.name, value) },
                    onColorChange = { value -> model.setColorParam(param.name, value) },
                    onCommit = { model.commitParam() },
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ScenesCard(ui: UiState, model: AppViewModel) {
    val state = ui.state ?: return
    var naming by remember { mutableStateOf(false) }

    SectionCard("Scenes") {
        if (state.scenes.isEmpty()) {
            Text(
                "No scenes yet. Set the lights the way you want them, then save.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                state.scenes.forEach { scene ->
                    SceneChip(
                        scene = scene,
                        selected = scene.id == state.activeScene,
                        onRecall = { model.recallScene(scene.id) },
                        onDelete = { model.deleteScene(scene.id) },
                    )
                }
            }
        }

        TextButton(onClick = { naming = true }) {
            Icon(Icons.Filled.Add, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("Save what is showing")
        }
    }

    if (naming) {
        SceneNameDialog(
            onConfirm = { model.saveScene(it); naming = false },
            onDismiss = { naming = false },
        )
    }
}

@Composable
private fun SceneChip(scene: Scene, selected: Boolean, onRecall: () -> Unit, onDelete: () -> Unit) {
    FilterChip(
        selected = selected,
        onClick = onRecall,
        label = { Text(scene.name) },
        trailingIcon = {
            IconButton(onClick = onDelete, modifier = Modifier.width(28.dp)) {
                Icon(
                    Icons.Filled.Delete,
                    contentDescription = "Delete ${scene.name}",
                    modifier = Modifier.height(16.dp),
                )
            }
        },
    )
}

@Composable
private fun SceneNameDialog(onConfirm: (String) -> Unit, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Save scene") },
        text = {
            OutlinedTextField(
                value = name,
                onValueChange = { name = it.take(120) },
                singleLine = true,
                label = { Text("Name") },
            )
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(name.trim()) },
                enabled = name.isNotBlank(),
            ) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@Composable
private fun FooterNotes(ui: UiState) {
    val status = ui.controller.status
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (status != null) {
            Text(
                "%d pixels - %.0f fps - %.1f A of %.0f A".format(
                    status.pixelCount,
                    status.engine.measuredFps,
                    status.power.deliveredAmps,
                    status.power.limitAmps,
                ),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (!status.connected) {
                Text(
                    "The render loop is running but fcserver is not reachable, so the strip is not being updated.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
        Text(
            "These are RGB pixels synthesising white from three narrow emitters. " +
                "Whites are tunable and pleasant, but skin tones and food render poorly under them.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
