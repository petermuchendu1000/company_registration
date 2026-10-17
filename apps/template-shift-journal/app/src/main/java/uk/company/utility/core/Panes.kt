package uk.company.utility.core

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.platform.LocalConfiguration

/**
 * How much room there is, and what to do with it.
 *
 * These apps shipped one layout: a single scrolling column under a bottom
 * bar, which is right on a phone and wrong on anything larger. On a 10-inch
 * tablet it stretched a 360dp design across 800dp — one narrow column of
 * cards, a button the full width of the screen, and most of the display
 * empty below the fold. The store screenshots showed exactly that, because
 * they are captured from the real UI.
 *
 * The breakpoints are Material's own, with one addition. Material splits at
 * 600dp (compact to medium) and 840dp (medium to expanded); a 10-inch tablet
 * in portrait is 800dp, which is medium, so the expanded threshold would
 * never fire on the device the layout was worst on. [SIDE_BY_SIDE] starts at
 * 720dp instead: with an 80dp rail that leaves two 320dp panes, each about
 * the width the single-column design was drawn for.
 */
enum class PaneLayout {
    /** Phone. Bottom navigation bar, one screen at a time. */
    BOTTOM_BAR,

    /** Small tablet. Navigation moves to a rail; still one screen. */
    RAIL,

    /**
     * Large tablet. Rail, and the archetype's two feature screens sit beside
     * each other — the timer next to the log, the journey form next to the
     * history, the estimate builder next to the saved estimates. They already
     * share hoisted state, so an entry made on the left appears on the right
     * without anything being wired up for it.
     */
    SIDE_BY_SIDE,
    ;

    val usesRail: Boolean get() = this != BOTTOM_BAR
}

private const val MEDIUM_WIDTH_DP = 600
private const val SIDE_BY_SIDE_WIDTH_DP = 720

/** The thresholds, as a plain function so they can be tested without a window. */
fun paneLayoutFor(widthDp: Int): PaneLayout = when {
    widthDp >= SIDE_BY_SIDE_WIDTH_DP -> PaneLayout.SIDE_BY_SIDE
    widthDp >= MEDIUM_WIDTH_DP -> PaneLayout.RAIL
    else -> PaneLayout.BOTTOM_BAR
}

@Composable
@ReadOnlyComposable
fun paneLayoutForWindow(): PaneLayout =
    paneLayoutFor(LocalConfiguration.current.screenWidthDp)

/**
 * True when this screen's sibling feature screen is already on display.
 *
 * Archetypes read it to drop links that would go nowhere: a "See all →" next
 * to a summary is useful on a phone and nonsense on a tablet where the full
 * list is in the next pane. Defaults to false, so a screen composed outside
 * the navigation host behaves as it does on a phone.
 */
val LocalSiblingPaneVisible = compositionLocalOf { false }
