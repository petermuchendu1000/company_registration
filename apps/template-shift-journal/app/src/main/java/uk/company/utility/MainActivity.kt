package uk.company.utility

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.Crossfade
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Calculate
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.outlined.Calculate
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import uk.company.utility.arch.activeArchetype
import uk.company.utility.core.FeatureTab
import uk.company.utility.core.SharedScreen
import uk.company.utility.screens.CalculatorScreen
import uk.company.utility.screens.ContactScreen
import uk.company.utility.screens.ReferenceScreen
import uk.company.utility.ui.theme.AppTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            AppTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    AppNav()
                }
            }
        }
    }
}

/**
 * Navigation host.
 *
 * The archetype owns the leading tabs — the ones that make this app a mileage
 * log rather than a shift journal — and the trailing three are shared by every
 * app. This file never names a specific archetype: `activeArchetype()` is
 * resolved at compile time from whichever archetype source directory the
 * generator put on this flavor's source set.
 */
@Composable
private fun AppNav() {
    val archetype = activeArchetype()
    var selectedKey by rememberSaveable { mutableStateOf("") }
    val featureTabs = archetype.rememberTabs { key -> selectedKey = key }

    val sharedTabs = archetype.sharedScreens.map { screen ->
        when (screen) {
            SharedScreen.CALCULATOR -> FeatureTab(
                key = "calculator",
                label = "Calc",
                filledIcon = Icons.Filled.Calculate,
                outlinedIcon = Icons.Outlined.Calculate,
            ) { pad -> CalculatorScreen(pad) }

            SharedScreen.REFERENCE -> FeatureTab(
                key = "reference",
                // Five tabs share the bar; "Reference" is ellipsised at phone
                // width and reads "Referenc".
                label = "Info",
                filledIcon = Icons.Filled.Info,
                outlinedIcon = Icons.Outlined.Info,
            ) { pad -> ReferenceScreen(pad) }

            SharedScreen.CONTACT -> FeatureTab(
                key = "contact",
                label = "Contact",
                filledIcon = Icons.Filled.Person,
                outlinedIcon = Icons.Outlined.Person,
            ) { pad -> ContactScreen(pad) }
        }
    }

    val tabs = featureTabs + sharedTabs
    // An unset key on first launch, or a key saved by a previous version whose
    // tab no longer exists, both fall back to the first tab rather than
    // leaving the user on a blank screen.
    val current = tabs.firstOrNull { it.key == selectedKey } ?: tabs.first()

    Scaffold(
        bottomBar = {
            NavigationBar(tonalElevation = 4.dp) {
                tabs.forEach { tab ->
                    val selected = tab.key == current.key
                    NavigationBarItem(
                        selected = selected,
                        onClick = { selectedKey = tab.key },
                        icon = {
                            val icon = if (selected) tab.filledIcon else tab.outlinedIcon
                            if (tab.badged) {
                                BadgedBox(badge = { Badge() }) {
                                    Icon(icon, contentDescription = tab.label)
                                }
                            } else {
                                Icon(icon, contentDescription = tab.label)
                            }
                        },
                        label = { Text(tab.label, maxLines = 1) },
                    )
                }
            }
        },
    ) { pad ->
        Crossfade(targetState = current.key, label = "tab_nav") { key ->
            (tabs.firstOrNull { it.key == key } ?: tabs.first()).content(pad)
        }
    }
}
