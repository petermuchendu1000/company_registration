package uk.company.utility.arch

import android.content.Context
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddRoad
import androidx.compose.material.icons.filled.Route
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.outlined.AddRoad
import androidx.compose.material.icons.outlined.Route
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import uk.company.utility.BuildConfig
import uk.company.utility.core.LocalSiblingPaneVisible
import uk.company.utility.core.pluralNoun
import uk.company.utility.core.AccentCard
import uk.company.utility.core.Archetype
import uk.company.utility.core.BreakdownRow
import uk.company.utility.core.EmptyState
import uk.company.utility.core.FeatureTab
import uk.company.utility.core.HeroHeader
import uk.company.utility.core.ScreenTitle
import uk.company.utility.core.SharedScreen
import uk.company.utility.core.SectionHeader
import uk.company.utility.core.StatPill
import uk.company.utility.core.Store
import uk.company.utility.core.fmtDate
import uk.company.utility.core.fmtMiles
import uk.company.utility.core.fmtMonth
import uk.company.utility.core.fmtPence
import java.util.Calendar

/**
 * Mileage log: an odometer, not a clock.
 *
 * A journey is a pair of odometer readings. The app derives the distance, then
 * accrues HMRC Approved Mileage Allowance Payments across the UK tax year
 * (6 April to 5 April), applying the higher rate to the first 10,000 miles and
 * the lower rate to everything after — in the order the journeys were driven,
 * which is how the allowance actually works. Nothing here measures elapsed
 * time, and nothing in the shift journal measures distance.
 */
object MileageArchetype : Archetype {
    override val id: String = "mileage"

    // No generic rate calculator: the tax-year card already does this trade's
    // money arithmetic, and carrying the shared one too would make the app
    // look like every other app in the catalog.
    override val sharedScreens: List<SharedScreen> =
        listOf(SharedScreen.REFERENCE, SharedScreen.CONTACT)

    @Composable
    override fun rememberTabs(navigate: (String) -> Unit): List<FeatureTab> {
        val ctx = androidx.compose.ui.platform.LocalContext.current
        val journeys = remember { mutableStateListOf<Journey>() }
        var loaded by remember { mutableStateOf(false) }

        LaunchedEffect(Unit) {
            if (!loaded) {
                journeys.addAll(loadJourneys(ctx))
                loaded = true
            }
        }

        return listOf(
            FeatureTab(
                key = "home",
                label = "Home",
                filledIcon = Icons.Filled.AddRoad,
                outlinedIcon = Icons.Outlined.AddRoad,
            ) { pad ->
                HomeScreen(
                    pad = pad,
                    journeys = journeys,
                    onSave = { startOdo, endOdo, purpose ->
                        journeys.add(
                            Journey(
                                id = (journeys.maxOfOrNull { it.id } ?: 0) + 1,
                                atMs = System.currentTimeMillis(),
                                startOdo = startOdo,
                                endOdo = endOdo,
                                purpose = purpose,
                            ),
                        )
                        saveJourneys(ctx, journeys)
                    },
                    onViewAll = { navigate("journeys") },
                )
            },
            FeatureTab(
                key = "journeys",
                label = "Journeys",
                filledIcon = Icons.Filled.Route,
                outlinedIcon = Icons.Outlined.Route,
            ) { pad -> JourneysScreen(pad, journeys) },
        )
    }
}

/** The archetype the build selected. One definition per archetype source set. */
fun activeArchetype(): Archetype = MileageArchetype

// ── model ───────────────────────────────────────────────────────────────────

private const val KEY_JOURNEYS = "journey_entries"

data class Journey(
    val id: Int,
    val atMs: Long,
    val startOdo: Int,
    val endOdo: Int,
    val purpose: String = "",
) {
    val miles: Int get() = (endOdo - startOdo).coerceAtLeast(0)
}

private fun saveJourneys(ctx: Context, rows: List<Journey>) {
    Store.write(
        ctx, KEY_JOURNEYS,
        rows.map { j ->
            JSONObject().apply {
                put("id", j.id)
                put("at", j.atMs)
                put("from", j.startOdo)
                put("to", j.endOdo)
                put("purpose", j.purpose)
            }
        },
    )
}

private fun loadJourneys(ctx: Context): List<Journey> =
    Store.read(ctx, KEY_JOURNEYS).map { o ->
        Journey(
            id = o.optInt("id"),
            atMs = o.optLong("at"),
            startOdo = o.optInt("from"),
            endOdo = o.optInt("to"),
            purpose = o.optString("purpose", ""),
        )
    }

// ── HMRC approved mileage ───────────────────────────────────────────────────

/**
 * The UK tax year containing [ms] starts on 6 April. Returned as the start
 * instant so journeys can be filtered with a single comparison.
 */
private fun taxYearStartMs(ms: Long): Long {
    val now = Calendar.getInstance().apply { timeInMillis = ms }
    val boundary = Calendar.getInstance().apply {
        clear()
        set(now.get(Calendar.YEAR), Calendar.APRIL, 6, 0, 0, 0)
    }
    if (ms >= boundary.timeInMillis) return boundary.timeInMillis
    boundary.add(Calendar.YEAR, -1)
    return boundary.timeInMillis
}

/** e.g. "2026/27" for the tax year beginning 6 April 2026. */
private fun taxYearLabel(startMs: Long): String {
    val y = Calendar.getInstance().apply { timeInMillis = startMs }.get(Calendar.YEAR)
    return "$y/${((y + 1) % 100).toString().padStart(2, '0')}"
}

/**
 * Relief for [miles] driven when [milesAlready] of the tax year's allowance
 * has been used. The higher rate applies only to the part that still falls
 * inside the band.
 */
private fun reliefPence(milesAlready: Int, miles: Int): Long {
    val atHigherRate = (BuildConfig.AMAP_BAND_MILES - milesAlready).coerceIn(0, miles)
    val atLowerRate = miles - atHigherRate
    return atHigherRate.toLong() * BuildConfig.AMAP_FIRST_RATE_PENCE +
        atLowerRate.toLong() * BuildConfig.AMAP_AFTER_RATE_PENCE
}

private data class TaxYear(
    val startMs: Long,
    val journeys: List<Journey>,
    val totalMiles: Int,
    val milesInBand: Int,
    val milesOverBand: Int,
    val reliefPence: Long,
) {
    val label: String get() = taxYearLabel(startMs)
}

private fun taxYearSummary(all: List<Journey>, nowMs: Long): TaxYear {
    val start = taxYearStartMs(nowMs)
    val inYear = all.filter { it.atMs >= start }.sortedBy { it.atMs }
    val total = inYear.sumOf { it.miles }
    val inBand = total.coerceAtMost(BuildConfig.AMAP_BAND_MILES)
    return TaxYear(
        startMs = start,
        journeys = inYear,
        totalMiles = total,
        milesInBand = inBand,
        milesOverBand = total - inBand,
        reliefPence = reliefPence(0, total),
    )
}

/**
 * Relief for every journey, keyed by id.
 *
 * The band resets each tax year and is consumed in the order journeys were
 * driven, so this has to be worked out per year over the whole history —
 * a journey cannot be priced on its own.
 */
private fun reliefByJourney(all: List<Journey>): Map<Int, Long> {
    val out = HashMap<Int, Long>(all.size)
    all.groupBy { taxYearStartMs(it.atMs) }.forEach { (_, rows) ->
        var running = 0
        rows.sortedBy { it.atMs }.forEach { j ->
            out[j.id] = reliefPence(running, j.miles)
            running += j.miles
        }
    }
    return out
}

// ── screens ─────────────────────────────────────────────────────────────────

@Composable
private fun HomeScreen(
    pad: PaddingValues,
    journeys: List<Journey>,
    onSave: (Int, Int, String) -> Unit,
    onViewAll: () -> Unit,
) {
    val year = remember(journeys.size) { taxYearSummary(journeys, System.currentTimeMillis()) }
    val relief = remember(journeys.size) { reliefByJourney(journeys) }
    val lastOdo = remember(journeys.size) { journeys.maxByOrNull { it.atMs }?.endOdo }

    var startOdo by rememberSaveable { mutableStateOf("") }
    var endOdo by rememberSaveable { mutableStateOf("") }
    var purpose by rememberSaveable { mutableStateOf("") }

    // Journeys arrive from storage after the first composition, so the opening
    // reading is prefilled once they land rather than in the initialiser. Only
    // while the field is untouched — never over something the user typed.
    LaunchedEffect(lastOdo) {
        if (startOdo.isBlank() && lastOdo != null) startOdo = lastOdo.toString()
    }

    val startValue = startOdo.filter { it.isDigit() }.toIntOrNull()
    val endValue = endOdo.filter { it.isDigit() }.toIntOrNull()
    val pendingMiles = if (startValue != null && endValue != null) endValue - startValue else null
    val problem: String? = when {
        startOdo.isBlank() || endOdo.isBlank() -> null
        startValue == null || endValue == null -> "Odometer readings must be whole numbers."
        pendingMiles != null && pendingMiles <= 0 -> "The closing reading must be higher than the opening one."
        else -> null
    }
    val canSave = pendingMiles != null && pendingMiles > 0

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(pad)
            .verticalScroll(rememberScrollState()),
    ) {
        HeroHeader(subtitle = BuildConfig.EXPORT_TITLE) {
            Surface(
                shape = RoundedCornerShape(24.dp),
                color = androidx.compose.ui.graphics.Color.White.copy(alpha = 0.20f),
            ) {
                Text(
                    "${fmtMiles(year.totalMiles)} miles · ${year.label}",
                    style = MaterialTheme.typography.labelLarge,
                    fontWeight = FontWeight.SemiBold,
                    color = androidx.compose.ui.graphics.Color.White,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }

        Column(
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            TaxYearCard(year)

            Card(
                Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                shape = RoundedCornerShape(20.dp),
                elevation = CardDefaults.elevatedCardElevation(defaultElevation = 2.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    Text(
                        BuildConfig.ROLE_VERB_START,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        OutlinedTextField(
                            value = startOdo,
                            onValueChange = { startOdo = it },
                            modifier = Modifier.weight(1f),
                            label = { Text("${BuildConfig.VEHICLE_LABEL} start") },
                            supportingText = { Text("Odometer") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            singleLine = true,
                            shape = RoundedCornerShape(12.dp),
                        )
                        OutlinedTextField(
                            value = endOdo,
                            onValueChange = { endOdo = it },
                            modifier = Modifier.weight(1f),
                            label = { Text("${BuildConfig.VEHICLE_LABEL} end") },
                            supportingText = { Text("Odometer") },
                            isError = problem != null,
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            singleLine = true,
                            shape = RoundedCornerShape(12.dp),
                        )
                    }
                    OutlinedTextField(
                        value = purpose,
                        onValueChange = { purpose = it },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Purpose (optional)") },
                        placeholder = { Text("Drop-off, collection, site…") },
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                    )

                    if (problem != null) {
                        Text(
                            problem,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }

                    AnimatedVisibility(
                        visible = canSave,
                        enter = expandVertically() + fadeIn(tween(250)),
                        exit = shrinkVertically() + fadeOut(tween(150)),
                    ) {
                        val miles = pendingMiles ?: 0
                        Surface(
                            shape = RoundedCornerShape(12.dp),
                            color = MaterialTheme.colorScheme.primaryContainer,
                        ) {
                            Row(
                                Modifier
                                    .fillMaxWidth()
                                    .padding(horizontal = 14.dp, vertical = 10.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                            ) {
                                Text(
                                    "${fmtMiles(miles)} miles",
                                    style = MaterialTheme.typography.bodyMedium,
                                    fontWeight = FontWeight.SemiBold,
                                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                                )
                                Text(
                                    fmtPence(reliefPence(year.totalMiles, miles)),
                                    style = MaterialTheme.typography.bodyMedium,
                                    fontWeight = FontWeight.Bold,
                                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                                )
                            }
                        }
                    }

                    Button(
                        onClick = {
                            val from = startValue ?: return@Button
                            val to = endValue ?: return@Button
                            onSave(from, to, purpose.trim())
                            startOdo = to.toString()
                            endOdo = ""
                            purpose = ""
                        },
                        enabled = canSave,
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(48.dp),
                        shape = RoundedCornerShape(14.dp),
                    ) {
                        Icon(Icons.Default.Save, contentDescription = null, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(
                            BuildConfig.ROLE_VERB_END,
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
            }

            // Dropped when the history pane is beside this one: the same
            // rows twice on one screen, and the space is better spent on the
            // entry form and the relief summary above it.
            val previewJourneys = journeys.isNotEmpty() && !LocalSiblingPaneVisible.current
            if (previewJourneys) {
                val linkToAll = journeys.size > 3
                SectionHeader(
                    "Recent journeys",
                    actionLabel = if (linkToAll) "See all →" else null,
                    onAction = if (linkToAll) onViewAll else null,
                )
                journeys.sortedByDescending { it.atMs }.take(3).forEach {
                    JourneyRow(it, relief[it.id] ?: 0L)
                }
            } else if (journeys.isEmpty()) {
                EmptyState(
                    icon = Icons.Outlined.AddRoad,
                    title = "No journeys yet",
                    body = "Enter the opening and closing odometer readings above to " +
                        "record your first ${BuildConfig.ROLE_NOUN}",
                )
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
private fun TaxYearCard(year: TaxYear) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(20.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "Tax year ${year.label}",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    fmtPence(year.reliefPence),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                )
            }

            val band = BuildConfig.AMAP_BAND_MILES
            val progress = (year.totalMiles.toFloat() / band).coerceIn(0f, 1f)
            LinearProgressIndicator(
                progress = { progress },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(8.dp),
            )
            Text(
                "${fmtMiles(year.milesInBand)} of ${fmtMiles(band)} miles at the higher rate",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            BreakdownRow(
                "First ${fmtMiles(band)} miles @ ${BuildConfig.AMAP_FIRST_RATE_PENCE}p",
                "${fmtMiles(year.milesInBand)} mi · " +
                    fmtPence(year.milesInBand.toLong() * BuildConfig.AMAP_FIRST_RATE_PENCE),
            )
            BreakdownRow(
                "Above ${fmtMiles(band)} miles @ ${BuildConfig.AMAP_AFTER_RATE_PENCE}p",
                "${fmtMiles(year.milesOverBand)} mi · " +
                    fmtPence(year.milesOverBand.toLong() * BuildConfig.AMAP_AFTER_RATE_PENCE),
            )
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            BreakdownRow("Approved mileage total", fmtPence(year.reliefPence), emphasis = true)
            Text(
                "HMRC approved rates for cars and vans. A guide for your own records, " +
                    "not a tax return.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
            )
        }
    }
}

@Composable
private fun JourneysScreen(pad: PaddingValues, journeys: List<Journey>) {
    val relief = remember(journeys.size) { reliefByJourney(journeys) }
    val ordered = remember(journeys.size) { journeys.sortedByDescending { it.atMs } }
    val totalMiles = remember(journeys.size) { journeys.sumOf { it.miles } }

    Column(
        Modifier
            .fillMaxSize()
            .padding(pad)
            .padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        ScreenTitle(BuildConfig.EXPORT_TITLE)

        if (ordered.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                EmptyState(
                    icon = Icons.Outlined.Route,
                    title = "No journeys yet",
                    body = "Log a ${BuildConfig.ROLE_NOUN} on the Home tab",
                    card = false,
                )
            }
            return@Column
        }

        Row(
            Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            StatPill(Modifier.weight(1f), ordered.size.toString(), pluralNoun(ordered.size, "journey"))
            StatPill(Modifier.weight(1f), fmtMiles(totalMiles), "miles")
        }

        // Month headers let a driver reconcile against a monthly invoice,
        // which is the shape the records are usually needed in.
        val byMonth = ordered.groupBy { fmtMonth(it.atMs) }
        LazyColumn(
            verticalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = PaddingValues(bottom = 24.dp),
        ) {
            byMonth.forEach { (month, rows) ->
                item(key = "header-$month") {
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .padding(top = 8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            month,
                            style = MaterialTheme.typography.titleSmall,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Text(
                            "${fmtMiles(rows.sumOf { it.miles })} mi",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                items(rows, key = { it.id }) { JourneyRow(it, relief[it.id] ?: 0L) }
            }
        }
    }
}

@Composable
private fun JourneyRow(journey: Journey, reliefPence: Long) {
    AccentCard {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text(
                        "${fmtMiles(journey.startOdo)} → ${fmtMiles(journey.endOdo)}",
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Medium,
                    )
                    Text(
                        fmtDate(journey.atMs),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Column(horizontalAlignment = Alignment.End) {
                    Surface(
                        shape = RoundedCornerShape(8.dp),
                        color = MaterialTheme.colorScheme.secondaryContainer,
                    ) {
                        Text(
                            "${fmtMiles(journey.miles)} mi",
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = MaterialTheme.colorScheme.onSecondaryContainer,
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        )
                    }
                    Text(
                        fmtPence(reliefPence),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (journey.purpose.isNotBlank()) {
                Text(
                    journey.purpose,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 2.dp, top = 2.dp),
                )
            }
        }
    }
}
