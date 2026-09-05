package com.fclights.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * A deliberately warm, low-contrast scheme. The app is used in a dim room to
 * adjust the light in that room, so a bright white UI would be the brightest
 * thing in it and would wreck the eye adaptation the user is trying to judge.
 */
private val DarkScheme = darkColorScheme(
    primary = Color(0xFFFFB74D),
    onPrimary = Color(0xFF3E2600),
    primaryContainer = Color(0xFF5A3800),
    onPrimaryContainer = Color(0xFFFFDDB3),
    secondary = Color(0xFFD8C3A5),
    background = Color(0xFF14120F),
    onBackground = Color(0xFFE9E1D9),
    surface = Color(0xFF14120F),
    onSurface = Color(0xFFE9E1D9),
    surfaceVariant = Color(0xFF2A2520),
    onSurfaceVariant = Color(0xFFD3C6B8),
    outline = Color(0xFF5C544A),
)

private val LightScheme = lightColorScheme(
    primary = Color(0xFF8A5100),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFFFDDB3),
    onPrimaryContainer = Color(0xFF2C1700),
    background = Color(0xFFFFFBF7),
    surface = Color(0xFFFFFBF7),
    surfaceVariant = Color(0xFFF1E3D3),
)

@Composable
fun FclightsTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkScheme else LightScheme,
        content = content,
    )
}
