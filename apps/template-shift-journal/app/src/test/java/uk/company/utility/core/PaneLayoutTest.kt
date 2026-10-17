package uk.company.utility.core

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Which layout each window width gets.
 *
 * Worth pinning because the widths are not arbitrary and not Material's
 * defaults: a 10-inch tablet in portrait is 800dp, which Material calls
 * medium, so an expanded-only rule at 840dp would have left the device the
 * single-column layout looked worst on with the single-column layout. The
 * store screenshots are rendered at exactly these three widths, so a change
 * here changes what Play is sent.
 */
class PaneLayoutTest {

    @Test
    fun `phone widths keep the bottom bar`() {
        // 1080x1920 at xxhdpi — the phone screenshot size.
        assertEquals(PaneLayout.BOTTOM_BAR, paneLayoutFor(360))
        assertEquals(PaneLayout.BOTTOM_BAR, paneLayoutFor(599))
    }

    @Test
    fun `a 7-inch tablet gets a rail and one pane`() {
        // 1200x1920 at xhdpi — the 7-inch screenshot size.
        assertEquals(PaneLayout.RAIL, paneLayoutFor(600))
        assertEquals(PaneLayout.RAIL, paneLayoutFor(719))
    }

    @Test
    fun `a 10-inch tablet gets two panes`() {
        // 1600x2560 at xhdpi — the 10-inch screenshot size. Below Material's
        // 840dp expanded breakpoint, which is the point of the 720dp rule.
        assertEquals(PaneLayout.SIDE_BY_SIDE, paneLayoutFor(720))
        assertEquals(PaneLayout.SIDE_BY_SIDE, paneLayoutFor(800))
    }

    @Test
    fun `only the phone layout uses a bottom bar`() {
        assertEquals(false, paneLayoutFor(360).usesRail)
        assertEquals(true, paneLayoutFor(600).usesRail)
        assertEquals(true, paneLayoutFor(800).usesRail)
    }
}
