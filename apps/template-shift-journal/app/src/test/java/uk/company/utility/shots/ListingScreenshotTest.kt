package uk.company.utility.shots

import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.os.Looper
import android.view.View
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.isSelectable
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.test.core.app.ApplicationProvider
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import uk.company.utility.BuildConfig
import uk.company.utility.MainActivity
import uk.company.utility.core.fmtDate
import java.io.File
import java.util.Calendar

/**
 * The Play Store screenshots, rendered from the app itself.
 *
 * Play rejects listings whose screenshots do not represent the app, and a
 * hand-drawn mock drifts from the UI the moment either side changes. This
 * drives the real [MainActivity] under Robolectric with NATIVE graphics and
 * captures the composed frame, so a screenshot cannot show a screen the app
 * does not have.
 *
 * The flavor decides what is drawn. Every BuildConfig field the generator
 * wrote is live here, and [seed] puts believable records in the same
 * SharedPreferences the app reads, so the shots show a working app rather
 * than five empty states. Nothing here is archetype-specific beyond [seed]
 * and [plan]: the navigation bar is walked by the content description Compose
 * already sets from each tab's label.
 *
 * Output goes to the directory named by -Dshots.dir, which
 * apps/generator/assets.py passes and then copies into listing graphics/.
 *
 *     ./gradlew :app:test<Flavor>DebugUnitTest --tests '*ListingScreenshotTest'
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34])
class ListingScreenshotTest {

    @get:Rule
    val compose = createAndroidComposeRule<MainActivity>()

    /**
     * Play wants three sizes, and all three are mandatory for a listing that
     * offers tablet support. The qualifiers are chosen so density × dp lands
     * exactly on the pixel sizes Play specifies — 360×3, 600×2, 800×2 — and
     * the test fails rather than uploading an off-size image if a future
     * Robolectric rounds them differently.
     */
    @Test
    @Config(qualifiers = "w360dp-h640dp-xxhdpi")
    fun phone() = shoot("phone-screenshot", 1080, 1920)

    @Test
    @Config(qualifiers = "w600dp-h960dp-xhdpi")
    fun tablet7() = shoot("tablet7-screenshot", 1200, 1920)

    @Test
    @Config(qualifiers = "w800dp-h1280dp-xhdpi")
    fun tablet10() = shoot("tablet10-screenshot", 1600, 2560)

    private fun shoot(prefix: String, width: Int, height: Int) {
        val dir = File(System.getProperty("shots.dir") ?: "build/listing-shots")
        dir.mkdirs()
        plan().forEachIndexed { i, shot ->
            shot.before?.invoke()
            if (shot.tab != 0) {
                // The bottom bar's items are the only selectable nodes in the
                // tree, in the order AppNav built them. Matching the icon's
                // content description does not work: NavigationBarItem merges
                // its icon and label into one node, and the description only
                // survives in the unmerged tree, where nothing is clickable.
                val tabs = compose.onAllNodes(isSelectable()).fetchSemanticsNodes().size
                val index = if (shot.tab < 0) tabs + shot.tab else shot.tab
                compose.onAllNodes(isSelectable())[index].performClick()
                compose.waitForIdle()
            }
            val dest = File(dir, "$prefix-${i + 1}.png")
            val bmp = capture()
            assertEquals("$prefix width", width, bmp.width)
            assertEquals("$prefix height", height, bmp.height)
            dest.outputStream().use { out -> bmp.compress(Bitmap.CompressFormat.PNG, 100, out) }
            println("  shot ${dest.name}  ${bmp.width}x${bmp.height}  ${shot.caption}")
        }
        writeManifest(dir, prefix)
    }

    /**
     * Record what was rendered, beside the PNGs.
     *
     * The generator copies the images into a listing it also writes captions
     * for, and nothing connected the two: a mileage app could be given a
     * shift app's screenshots and no step would notice. This states which
     * archetype and which flavor the pictures came from, and the policy gate
     * refuses to publish when it does not match the company being published.
     */
    private fun writeManifest(dir: File, prefix: String) {
        val shots = JSONArray()
        plan().forEachIndexed { i, shot ->
            shots.put(
                JSONObject().apply {
                    put("file", "$prefix-${i + 1}.png")
                    put("caption", shot.caption)
                },
            )
        }
        File(dir, "$prefix.json").writeText(
            JSONObject().apply {
                put("archetype", BuildConfig.ARCHETYPE_ID)
                put("application_id", BuildConfig.APPLICATION_ID)
                put("company_number", BuildConfig.COMPANY_NUMBER)
                put("shots", shots)
            }.toString(2),
        )
    }

    /**
     * The composed frame, as a bitmap.
     *
     * Not `onRoot().captureToImage()`: that goes through PixelCopy against a
     * real window, which Robolectric never redraws, and the capture times out
     * in forceRedraw after two seconds. Drawing the decor view onto our own
     * Canvas renders through the same display list the device would, and
     * NATIVE graphics mode (set in build.gradle.kts) makes that a real
     * rasterisation rather than a no-op.
     */
    private fun capture(): Bitmap {
        val view = compose.activity.window.decorView
        if (view.width == 0 || view.height == 0) {
            val dm = compose.activity.resources.displayMetrics
            view.measure(
                View.MeasureSpec.makeMeasureSpec(dm.widthPixels, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(dm.heightPixels, View.MeasureSpec.EXACTLY),
            )
            view.layout(0, 0, view.measuredWidth, view.measuredHeight)
        }
        shadowOf(Looper.getMainLooper()).idle()
        val bmp = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bmp)
        // Play's screenshot spec is a 24-bit PNG with no alpha, and the
        // listing manifest says so. An ARGB_8888 bitmap encodes an alpha
        // channel, so paint an opaque ground first and then tell the encoder
        // there is no transparency to write.
        canvas.drawColor(Color.WHITE)
        view.draw(canvas)
        bmp.setHasAlpha(false)
        return bmp
    }

    // ── what to capture ─────────────────────────────────────────────────────

    /**
     * [tab] indexes the bottom bar. 0 is where the app already opens, so it
     * is not clicked. A negative index counts back from the end, which is
     * how the two shared tabs are addressed: an archetype chooses its own
     * `sharedScreens`, and mileage and quote both drop the Calculator, so
     * Reference sits at 3 in a shift app and at 2 in the other two. Assuming
     * a fixed 5 was the first thing this test caught.
     */
    private data class Shot(
        val tab: Int,
        val caption: String,
        /** Run before the tab is selected — see [buildAnEstimate]. */
        val before: (() -> Unit)? = null,
    )

    /**
     * The screens, in listing order: the archetype's own two tabs, then
     * Reference and Contact. The Calculator is left out of the listing even
     * where an archetype has one — it is the screen the whole catalog
     * shares, and leading with it would make different products look alike.
     *
     * Keep this in step with `screens` in apps/generator/archetypes.py; the
     * policy gate checks the two agree.
     */
    private fun plan(): List<Shot> = when (BuildConfig.ARCHETYPE_ID) {
        "mileage" -> listOf(
            Shot(0, "home — journey entry and tax-year relief"),
            Shot(1, "journeys — history by month"),
            Shot(-2, "reference"),
            Shot(-1, "contact"),
        )
        "quote" -> listOf(
            Shot(0, "estimate — lines, VAT and total", ::buildAnEstimate),
            Shot(1, "saved estimates"),
            Shot(-2, "reference"),
            Shot(-1, "contact"),
        )
        else -> listOf(
            Shot(0, "home — timer and recent activity"),
            Shot(1, "session log"),
            Shot(-2, "reference"),
            Shot(-1, "contact"),
        )
    }

    /**
     * Fill in an estimate the way a user would.
     *
     * The quote builder holds its lines in composition rather than in the
     * store, so seeding SharedPreferences fills the saved list but leaves
     * the builder empty — the first shot was a blank form with "No lines
     * yet" across the header. Typing into the real fields and pressing the
     * real button is both the only way to get lines in there and the more
     * honest picture: the screenshot shows a flow the app actually supports.
     */
    private fun buildAnEstimate() {
        // By position, not by label: the quantity field is labelled with the
        // flavor's LINE_UNIT_LABEL ("Circuit", "Room", "Point"), and this
        // test source set compiles against every flavor, including the ones
        // whose BuildConfig has no such field. The four editable fields are
        // client, description, quantity and unit price, in that order.
        fun field(i: Int) = compose.onAllNodes(hasSetTextAction())[i]
        field(0).performTextInput("M. Okafor")
        listOf(
            Triple("Labour — first fix", "8", "48.00"),
            Triple("Materials — cable, 100 m", "1", "86.00"),
        ).forEach { (description, qty, unit) ->
            field(1).performTextInput(description)
            field(2).performTextReplacement(qty)
            field(3).performTextReplacement(unit)
            compose.onNodeWithText("Add line").performClick()
            compose.waitForIdle()
        }
        // No scroll here, deliberately. The builder is taller than a phone,
        // so the lines and the Net/VAT/Total breakdown sit below the fold —
        // but scrolling to them cuts the branded header off mid-glyph, and
        // performScrollTo leaves whatever it reaches clipped by the bottom
        // bar. The form with two lines already entered is the truer picture:
        // the header carries the running "2 lines - GBP 564.00", and the
        // itemised total is shown on the saved tab in the next shot.
    }

    // ── believable records, written before the activity reads them ──────────

    init {
        seed(ApplicationProvider.getApplicationContext<Application>())
    }

    private fun seed(ctx: Context) {
        val rows: Pair<String, JSONArray> = when (BuildConfig.ARCHETYPE_ID) {
            "mileage" -> "journey_entries" to journeys()
            "quote" -> "quote_documents" to quotes()
            else -> "log_entries" to sessions()
        }
        ctx.getSharedPreferences("app_data", Context.MODE_PRIVATE)
            .edit().putString(rows.first, rows.second.toString()).commit()
    }

    /**
     * Oldest first, so the home screen's newest-first list reads in order,
     * and dated against the day the shot is taken rather than a fixed date:
     * the "Today" tile counts rows whose date equals today's, and hardcoded
     * dates made every screenshot say the operator had done nothing today.
     */
    private fun sessions() = JSONArray().apply {
        listOf(
            arrayOf(1, "08:12", "11:20", "3h 08m", "Morning round"),
            arrayOf(1, "13:00", "15:57", "2h 57m", "Afternoon round"),
            arrayOf(0, "09:41", "11:55", "2h 14m", "Depot run — van 3"),
            arrayOf(0, "12:30", "15:02", "2h 32m", "Client drops, south"),
        ).forEachIndexed { i, r ->
            put(
                JSONObject().apply {
                    put("id", i + 1)
                    put("date", fmtDate(daysAgo(r[0] as Int)))
                    put("start", r[1]); put("end", r[2])
                    put("dur", r[3]); put("note", r[4])
                },
            )
        }
    }

    private fun journeys() = JSONArray().apply {
        listOf(
            intArrayOf(0, 48210, 48297) to "Client delivery — Leeds",
            intArrayOf(1, 48068, 48210) to "Depot collection",
            intArrayOf(2, 47951, 48068) to "Site visit — Hull",
            intArrayOf(30, 47702, 47951) to "Regional round",
        ).forEachIndexed { i, (odo, purpose) ->
            put(
                JSONObject().apply {
                    put("id", i + 1)
                    put("at", daysAgo(odo[0]))
                    put("from", odo[1]); put("to", odo[2]); put("purpose", purpose)
                },
            )
        }
    }

    private fun quotes() = JSONArray().apply {
        listOf(
            Triple(0, "M. Okafor", listOf(Triple("Labour — first fix", 8, 4800L))),
            Triple(2, "Brightwell Lettings", listOf(Triple("Labour — second fix", 6, 4800L))),
            Triple(5, "J. Hargreaves", listOf(Triple("Consumer unit", 1, 22200L))),
        ).forEachIndexed { i, (ago, client, lines) ->
            put(
                JSONObject().apply {
                    put("id", i + 1); put("at", daysAgo(ago)); put("client", client)
                    put(
                        "lines",
                        JSONArray().apply {
                            lines.forEach { (d, q, u) ->
                                put(JSONObject().apply { put("d", d); put("q", q); put("u", u) })
                            }
                        },
                    )
                },
            )
        }
    }

    private fun daysAgo(n: Int): Long = Calendar.getInstance()
        .apply { add(Calendar.DAY_OF_YEAR, -n) }.timeInMillis
}
