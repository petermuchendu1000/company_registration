package uk.company.utility.arch

import android.content.Context
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject
import uk.company.utility.BuildConfig
import uk.company.utility.core.pluralNoun
import uk.company.utility.core.AccentCard
import uk.company.utility.core.Archetype
import uk.company.utility.core.EmptyState
import uk.company.utility.core.FeatureTab
import uk.company.utility.core.HeroHeader
import uk.company.utility.core.ScreenTitle
import uk.company.utility.core.SectionHeader
import uk.company.utility.core.StatCard
import uk.company.utility.core.StatPill
import uk.company.utility.core.Store
import uk.company.utility.core.fmtDate
import uk.company.utility.core.fmtDur
import uk.company.utility.core.fmtElapsed
import uk.company.utility.core.fmtTime

/**
 * Shift journal: a running clock.
 *
 * The user starts a session, the app times it, and ending it writes a row with
 * the start time, end time and elapsed duration. What is recorded is a span of
 * time, which is what attendance-based trades need — staffing, care, property
 * site visits, depot shifts.
 */
object ShiftArchetype : Archetype {
    override val id: String = "shift"

    @Composable
    override fun rememberTabs(navigate: (String) -> Unit): List<FeatureTab> {
        val ctx = LocalContext.current
        val log = remember { mutableStateListOf<Session>() }
        var active by rememberSaveable { mutableStateOf(false) }
        var startTime by rememberSaveable { mutableStateOf("") }
        var startMs by rememberSaveable { mutableLongStateOf(0L) }
        var note by rememberSaveable { mutableStateOf("") }

        LaunchedEffect(Unit) {
            val saved = loadSessions(ctx)
            if (saved.isNotEmpty() && log.isEmpty()) log.addAll(saved)
        }

        return listOf(
            FeatureTab(
                key = "home",
                label = "Home",
                filledIcon = Icons.Filled.Home,
                outlinedIcon = Icons.Outlined.Home,
                badged = active,
            ) { pad ->
                HomeScreen(
                    pad = pad,
                    log = log,
                    active = active,
                    startTime = startTime,
                    startMs = startMs,
                    note = note,
                    onNoteChange = { note = it },
                    onStart = {
                        active = true
                        startTime = fmtTime(System.currentTimeMillis())
                        startMs = System.currentTimeMillis()
                        note = ""
                    },
                    onEnd = {
                        val now = System.currentTimeMillis()
                        log.add(
                            Session(
                                id = log.size + 1,
                                date = fmtDate(now),
                                startTime = startTime,
                                endTime = fmtTime(now),
                                duration = fmtDur(now - startMs),
                                note = note.trim(),
                            ),
                        )
                        saveSessions(ctx, log)
                        active = false
                        startTime = ""
                        startMs = 0L
                        note = ""
                    },
                    onViewLog = { navigate("log") },
                )
            },
            FeatureTab(
                key = "log",
                label = BuildConfig.EXPORT_TITLE,
                filledIcon = Icons.Filled.Schedule,
                outlinedIcon = Icons.Outlined.Schedule,
            ) { pad -> LogScreen(pad, log) },
        )
    }
}

/** The archetype the build selected. One definition per archetype source set. */
fun activeArchetype(): Archetype = ShiftArchetype

// ── model ───────────────────────────────────────────────────────────────────

private const val KEY_LOG = "log_entries"

data class Session(
    val id: Int,
    val date: String,
    val startTime: String,
    val endTime: String,
    val duration: String,
    val note: String = "",
)

private fun saveSessions(ctx: Context, entries: List<Session>) {
    Store.write(
        ctx, KEY_LOG,
        entries.map { e ->
            JSONObject().apply {
                put("id", e.id)
                put("date", e.date)
                put("start", e.startTime)
                put("end", e.endTime)
                put("dur", e.duration)
                put("note", e.note)
            }
        },
    )
}

private fun loadSessions(ctx: Context): List<Session> =
    Store.read(ctx, KEY_LOG).map { o ->
        Session(
            id = o.optInt("id"),
            date = o.optString("date"),
            startTime = o.optString("start"),
            endTime = o.optString("end"),
            duration = o.optString("dur"),
            note = o.optString("note", ""),
        )
    }

// ── screens ─────────────────────────────────────────────────────────────────

@Composable
private fun HomeScreen(
    pad: PaddingValues,
    log: List<Session>,
    active: Boolean,
    startTime: String,
    startMs: Long,
    note: String,
    onNoteChange: (String) -> Unit,
    onStart: () -> Unit,
    onEnd: () -> Unit,
    onViewLog: () -> Unit,
) {
    val today = fmtDate(System.currentTimeMillis())
    val todayCount = log.count { it.date == today }

    var elapsedSec by remember { mutableLongStateOf(0L) }
    LaunchedEffect(active, startMs) {
        if (!active) {
            elapsedSec = 0L
            return@LaunchedEffect
        }
        while (true) {
            elapsedSec = (System.currentTimeMillis() - startMs) / 1000L
            delay(1_000L)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(pad)
            .verticalScroll(rememberScrollState()),
    ) {
        HeroHeader(
            subtitle = BuildConfig.EXPORT_TITLE,
            trailing = if (active) {
                { SessionStatusChip(elapsedSec) }
            } else {
                null
            },
        )

        Column(
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                StatCard(
                    Modifier.weight(1f), "Today", todayCount.toString(),
                    MaterialTheme.colorScheme.primaryContainer,
                    MaterialTheme.colorScheme.onPrimaryContainer,
                )
                StatCard(
                    Modifier.weight(1f), "Total", log.size.toString(),
                    MaterialTheme.colorScheme.secondaryContainer,
                    MaterialTheme.colorScheme.onSecondaryContainer,
                )
            }

            SessionCard(active, startTime, note, onNoteChange, onStart, onEnd)

            if (log.isNotEmpty()) {
                SectionHeader(
                    "Recent Activity",
                    actionLabel = if (log.size > 3) "See all →" else null,
                    onAction = if (log.size > 3) onViewLog else null,
                )
                log.reversed().take(3).forEach { SessionRow(it) }
            } else {
                EmptyState(
                    icon = Icons.Outlined.Schedule,
                    title = "Nothing logged yet",
                    body = "Tap \"${BuildConfig.ROLE_VERB_START}\" above to record " +
                        "your first ${BuildConfig.ROLE_NOUN}",
                )
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
private fun SessionStatusChip(elapsedSec: Long) {
    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val dotScale by infiniteTransition.animateFloat(
        initialValue = 0.8f,
        targetValue = 1.3f,
        animationSpec = infiniteRepeatable(
            animation = tween(700, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "dot_scale",
    )
    Surface(shape = RoundedCornerShape(24.dp), color = Color.White.copy(alpha = 0.20f)) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Box(
                Modifier
                    .scale(dotScale)
                    .size(8.dp)
                    .clip(CircleShape)
                    .background(Color(0xFF4ADE80)),
            )
            Text(
                "Active · ${fmtElapsed(elapsedSec)}",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
                color = Color.White,
            )
        }
    }
}

@Composable
private fun SessionCard(
    active: Boolean,
    startTime: String,
    note: String,
    onNoteChange: (String) -> Unit,
    onStart: () -> Unit,
    onEnd: () -> Unit,
) {
    val cardColor = if (active) MaterialTheme.colorScheme.primaryContainer
    else MaterialTheme.colorScheme.surfaceVariant
    val onCardColor = if (active) MaterialTheme.colorScheme.onPrimaryContainer
    else MaterialTheme.colorScheme.onSurfaceVariant

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = cardColor),
        shape = RoundedCornerShape(20.dp),
        elevation = CardDefaults.elevatedCardElevation(defaultElevation = if (active) 4.dp else 1.dp),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Box(
                    Modifier
                        .size(10.dp)
                        .clip(CircleShape)
                        .background(
                            if (active) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.outline.copy(alpha = 0.5f),
                        ),
                )
                Text(
                    if (active) {
                        "${BuildConfig.ROLE_NOUN.replaceFirstChar { it.titlecase() }} started at $startTime"
                    } else {
                        "No active ${BuildConfig.ROLE_NOUN}"
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = if (active) FontWeight.Medium else FontWeight.Normal,
                    color = onCardColor,
                )
            }

            if (active) {
                OutlinedTextField(
                    value = note, onValueChange = onNoteChange,
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("Note (optional)") },
                    placeholder = { Text("Add a note…") },
                    minLines = 2, maxLines = 4,
                    shape = RoundedCornerShape(12.dp),
                )
            }

            val btnColors = if (active) {
                ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error,
                    contentColor = MaterialTheme.colorScheme.onError,
                )
            } else {
                ButtonDefaults.buttonColors()
            }

            Button(
                onClick = if (active) onEnd else onStart,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp),
                colors = btnColors,
                shape = RoundedCornerShape(14.dp),
            ) {
                Icon(
                    if (active) Icons.Default.Stop else Icons.Default.PlayArrow,
                    contentDescription = null,
                    modifier = Modifier.size(20.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    if (active) BuildConfig.ROLE_VERB_END else BuildConfig.ROLE_VERB_START,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
            }
        }
    }
}

@Composable
private fun LogScreen(pad: PaddingValues, log: List<Session>) {
    val uniqueDays = remember(log.size) { log.map { it.date }.toSet().size }

    Column(
        Modifier
            .fillMaxSize()
            .padding(pad)
            .padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        ScreenTitle(BuildConfig.EXPORT_TITLE)

        if (log.isNotEmpty()) {
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(bottom = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                StatPill(Modifier.weight(1f), log.size.toString(), pluralNoun(log.size, "entry", "entries"))
                StatPill(Modifier.weight(1f), uniqueDays.toString(), pluralNoun(uniqueDays, "day"))
            }
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = PaddingValues(bottom = 16.dp),
            ) {
                items(log.reversed()) { SessionRow(it) }
            }
        } else {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                EmptyState(
                    icon = Icons.Outlined.Schedule,
                    title = "No entries yet",
                    body = "Start a ${BuildConfig.ROLE_NOUN} on the Home tab",
                    card = false,
                )
            }
        }
    }
}

@Composable
private fun SessionRow(e: Session) {
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
                Row(
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
                        Text(
                            "#${e.id}",
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onPrimaryContainer,
                            modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
                        )
                    }
                    Column {
                        Text(
                            "${e.startTime} – ${e.endTime}",
                            style = MaterialTheme.typography.bodyMedium,
                            fontWeight = FontWeight.Medium,
                        )
                        Text(
                            e.date,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.secondaryContainer,
                ) {
                    Text(
                        e.duration,
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                }
            }
            if (e.note.isNotBlank()) {
                Text(
                    e.note,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 4.dp, top = 2.dp),
                )
            }
        }
    }
}
