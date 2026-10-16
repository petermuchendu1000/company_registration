package uk.company.utility.core

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.vector.ImageVector

/**
 * One bottom-bar destination contributed by an archetype.
 *
 * [content] closes over state hoisted in [Archetype.rememberTabs], so a tab
 * keeps its data while the user is looking at a different tab.
 */
class FeatureTab(
    val key: String,
    val label: String,
    val filledIcon: ImageVector,
    val outlinedIcon: ImageVector,
    val badged: Boolean = false,
    val content: @Composable (PaddingValues) -> Unit,
)

/**
 * The feature set that makes one app a different product from another.
 *
 * Exactly one implementation is on the compile path for any given flavor: the
 * generator adds that archetype's source directory to the flavor's Kotlin
 * source set (apps/generator/flavor_writer.py), so [activeArchetype] resolves
 * at compile time. Nothing selects an archetype at runtime and no unused
 * archetype reaches the artifact.
 */
/**
 * A screen the core provides that more than one archetype can reuse.
 *
 * An archetype opts in rather than inheriting all of them: a quote builder
 * that already totals labour, materials and VAT has no use for a generic rate
 * calculator, and carrying one would make it look like the same app as every
 * other one in the catalog.
 */
enum class SharedScreen { CALCULATOR, REFERENCE, CONTACT }

interface Archetype {
    /** Must match the id in apps/generator/archetypes.py. */
    val id: String

    /** Shared screens this product includes, in the order they appear. */
    val sharedScreens: List<SharedScreen>
        get() = listOf(SharedScreen.CALCULATOR, SharedScreen.REFERENCE, SharedScreen.CONTACT)

    /**
     * The tabs this archetype owns, shown before the shared Reference and
     * Contact tabs. Called once per recomposition of the navigation host, so
     * implementations put their state in `remember`/`rememberSaveable` here.
     *
     * [navigate] selects another tab by its [FeatureTab.key] — for a "see all"
     * link from a summary into a list, say. Passing a key no tab owns is a
     * no-op rather than a crash.
     */
    @Composable
    fun rememberTabs(navigate: (String) -> Unit): List<FeatureTab>
}
