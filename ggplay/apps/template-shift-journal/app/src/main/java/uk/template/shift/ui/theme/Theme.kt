package uk.template.shift.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.colorResource
import uk.template.shift.R

@Composable
fun ShiftJournalTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    // Colors come from per-flavor res/values/colors.xml overrides.
    val primary = colorResource(R.color.brand_primary)
    val onPrimary = colorResource(R.color.brand_on_primary)
    val accent = colorResource(R.color.brand_accent)
    val background = colorResource(R.color.brand_background)
    val surface = colorResource(R.color.brand_surface)
    val onBackground = colorResource(R.color.brand_on_background)

    val scheme = if (darkTheme) {
        darkColorScheme(
            primary = accent,
            onPrimary = primary,
            secondary = accent,
            onSecondary = primary,
            background = Color(0xFF1C1B1F),
            onBackground = Color(0xFFE6E1E5),
            surface = Color(0xFF2A2A2E),
            onSurface = Color(0xFFE6E1E5)
        )
    } else {
        lightColorScheme(
            primary = primary,
            onPrimary = onPrimary,
            primaryContainer = accent,
            secondary = accent,
            onSecondary = onPrimary,
            background = background,
            onBackground = onBackground,
            surface = surface,
            onSurface = onBackground
        )
    }

    MaterialTheme(colorScheme = scheme, typography = Typography, content = content)
}
