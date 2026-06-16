package uk.company.utility.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.colorResource
import uk.company.utility.R

@Composable
fun AppTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    // Colors from per-flavor res/values/colors.xml overlays.
    val primary     = colorResource(R.color.brand_primary)
    val onPrimary   = colorResource(R.color.brand_on_primary)
    val accent      = colorResource(R.color.brand_accent)
    val background  = colorResource(R.color.brand_background)
    val surface     = colorResource(R.color.brand_surface)
    val onBg        = colorResource(R.color.brand_on_background)

    // Derived tokens — keep contrast ratios safe
    val primaryContainer    = accent.copy(alpha = 0.18f).compositeOver(surface)
    val onPrimaryContainer  = primary
    val secondaryContainer  = accent.copy(alpha = 0.10f).compositeOver(surface)
    val onSecondaryContainer = primary
    val surfaceVariant      = Color(0xFFEEEEF0)
    val onSurfaceVariant    = Color(0xFF5A5A66)
    val outline             = Color(0xFFB0B0BA)

    val scheme = if (darkTheme) {
        darkColorScheme(
            primary                = accent,
            onPrimary              = Color(0xFF1A1A2E),
            primaryContainer       = primary.copy(alpha = 0.25f).compositeOver(Color(0xFF1C1B1F)),
            onPrimaryContainer     = accent,
            secondary              = accent,
            onSecondary            = Color(0xFF1A1A2E),
            secondaryContainer     = primary.copy(alpha = 0.15f).compositeOver(Color(0xFF1C1B1F)),
            onSecondaryContainer   = accent,
            background             = Color(0xFF1C1B1F),
            onBackground           = Color(0xFFE6E1E5),
            surface                = Color(0xFF2A2A2E),
            onSurface              = Color(0xFFE6E1E5),
            surfaceVariant         = Color(0xFF3A3A40),
            onSurfaceVariant       = Color(0xFFB8B4C0),
            outline                = Color(0xFF6A6A7A),
        )
    } else {
        lightColorScheme(
            primary                = primary,
            onPrimary              = onPrimary,
            primaryContainer       = primaryContainer,
            onPrimaryContainer     = onPrimaryContainer,
            secondary              = accent,
            onSecondary            = onPrimary,
            secondaryContainer     = secondaryContainer,
            onSecondaryContainer   = onSecondaryContainer,
            background             = background,
            onBackground           = onBg,
            surface                = surface,
            onSurface              = onBg,
            surfaceVariant         = surfaceVariant,
            onSurfaceVariant       = onSurfaceVariant,
            outline                = outline,
        )
    }

    MaterialTheme(colorScheme = scheme, typography = Typography, content = content)
}

private fun Color.compositeOver(background: Color): Color {
    val a = this.alpha
    return Color(
        red   = this.red   * a + background.red   * (1 - a),
        green = this.green * a + background.green * (1 - a),
        blue  = this.blue  * a + background.blue  * (1 - a),
        alpha = 1f,
    )
}
