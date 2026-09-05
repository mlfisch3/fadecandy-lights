package com.fclights.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardOptions
import com.fclights.api.Endpoint

/**
 * The way in.
 *
 * mDNS is offered, but the typed address is the first-class path and is what
 * this sheet opens on: multicast is dropped by plenty of home routers and
 * suppressed by Android's battery saver, and an app that can only be reached
 * through discovery is an app that sometimes cannot be reached at all.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConnectSheet(
    ui: UiState,
    onConnect: (Endpoint) -> Unit,
    onRescan: () -> Unit,
    onDismiss: () -> Unit,
) {
    var text by remember { mutableStateOf(ui.endpoint?.toString() ?: "") }
    val parsed = Endpoint.parse(text)

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp)
                .padding(bottom = 24.dp)
                .navigationBarsPadding(),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Controller address", style = MaterialTheme.typography.titleMedium)
            Text(
                "The Pi's IP address or hostname. Port ${Endpoint.DEFAULT_PORT} is assumed unless you add one.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                singleLine = true,
                label = { Text("Address") },
                placeholder = { Text("192.168.1.164") },
                isError = text.isNotBlank() && parsed == null,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction = ImeAction.Go,
                ),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = { parsed?.let(onConnect) },
                enabled = parsed != null,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Connect") }

            HorizontalDivider()

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Found on the network",
                    style = MaterialTheme.typography.titleSmall,
                    modifier = Modifier.weight(1f),
                )
                if (ui.discovering) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                } else {
                    TextButton(onClick = onRescan) { Text("Search") }
                }
            }

            if (ui.discovered.isEmpty()) {
                Text(
                    if (ui.discovering) {
                        "Looking for _fclights._tcp..."
                    } else {
                        "Nothing found. Plenty of home networks drop multicast, so type the address instead."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                ui.discovered.forEach { found ->
                    Column(
                        Modifier
                            .fillMaxWidth()
                            .clickable { onConnect(found.endpoint) }
                            .padding(vertical = 8.dp)
                    ) {
                        Text(found.name, style = MaterialTheme.typography.bodyMedium)
                        Text(
                            listOfNotNull(
                                found.endpoint.toString(),
                                found.pixels?.let { "$it pixels" },
                                found.version?.let { "v$it" },
                            ).joinToString(" - "),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}
