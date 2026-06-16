package uk.company.utility

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.Calculate
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject
import uk.company.utility.ui.theme.AppTheme
import java.text.NumberFormat
import java.text.SimpleDateFormat
import java.util.*

// ── Persistence ───────────────────────────────────────────────────────────────

private const val PREFS = "app_data"
private const val KEY_LOG = "log_entries"

data class LogEntry(
    val id: Int,
    val date: String,
    val startTime: String,
    val endTime: String,
    val duration: String,
    val note: String = "",
)

private fun saveLog(ctx: Context, entries: List<LogEntry>) {
    val arr = JSONArray()
    entries.forEach { e ->
        arr.put(JSONObject().apply {
            put("id", e.id); put("date", e.date)
            put("start", e.startTime); put("end", e.endTime)
            put("dur", e.duration); put("note", e.note)
        })
    }
    ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .edit().putString(KEY_LOG, arr.toString()).apply()
}

private fun loadLog(ctx: Context): List<LogEntry> {
    val raw = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(KEY_LOG, null) ?: return emptyList()
    return try {
        val arr = JSONArray(raw)
        (0 until arr.length()).map { i ->
            arr.getJSONObject(i).let { o ->
                LogEntry(o.getInt("id"), o.getString("date"),
                    o.getString("start"), o.getString("end"),
                    o.getString("dur"), o.optString("note", ""))
            }
        }
    } catch (_: Exception) { emptyList() }
}

// ── Entry point ───────────────────────────────────────────────────────────────

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            AppTheme {
                Surface(modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background) {
                    AppNav(applicationContext)
                }
            }
        }
    }
}

// ── Navigation ────────────────────────────────────────────────────────────────

private enum class Tab { Home, Log, Calculator, Reference, Contact }

private data class NavItem(
    val tab: Tab,
    val filledIcon: androidx.compose.ui.graphics.vector.ImageVector,
    val outlinedIcon: androidx.compose.ui.graphics.vector.ImageVector,
    val label: String,
)

@Composable
private fun AppNav(ctx: Context) {
    var tab by rememberSaveable { mutableStateOf(Tab.Home) }
    val log = remember { mutableStateListOf<LogEntry>() }
    var active by rememberSaveable { mutableStateOf(false) }
    var startTime by rememberSaveable { mutableStateOf("") }
    var startMs by rememberSaveable { mutableStateOf(0L) }
    var note by rememberSaveable { mutableStateOf("") }

    LaunchedEffect(Unit) {
        val saved = loadLog(ctx)
        if (saved.isNotEmpty() && log.isEmpty()) log.addAll(saved)
    }

    val navItems = listOf(
        NavItem(Tab.Home,       Icons.Filled.Home,     Icons.Outlined.Home,     "Home"),
        NavItem(Tab.Log,        Icons.Filled.Schedule, Icons.Outlined.Schedule, BuildConfig.EXPORT_TITLE),
        NavItem(Tab.Calculator, Icons.Filled.Calculate,Icons.Outlined.Calculate,"Calc"),
        NavItem(Tab.Reference,  Icons.Filled.Info,     Icons.Outlined.Info,     "Reference"),
        NavItem(Tab.Contact,    Icons.Filled.Person,   Icons.Outlined.Person,   "Contact"),
    )

    Scaffold(
        bottomBar = {
            NavigationBar(tonalElevation = 4.dp) {
                navItems.forEach { item ->
                    val selected = tab == item.tab
                    NavigationBarItem(
                        selected = selected,
                        onClick  = { tab = item.tab },
                        icon = {
                            val icon = if (selected) item.filledIcon else item.outlinedIcon
                            if (item.tab == Tab.Home && active) {
                                BadgedBox(badge = { Badge() }) {
                                    Icon(icon, contentDescription = item.label)
                                }
                            } else {
                                Icon(icon, contentDescription = item.label)
                            }
                        },
                        label = { Text(item.label, maxLines = 1) },
                    )
                }
            }
        },
    ) { pad ->
        Crossfade(targetState = tab, label = "tab_nav") { current ->
            when (current) {
                Tab.Home -> HomeScreen(
                    pad, log, active, startTime, startMs, note,
                    onNoteChange = { note = it },
                    onStart = {
                        active = true
                        startTime = fmtTime(System.currentTimeMillis())
                        startMs = System.currentTimeMillis()
                        note = ""
                    },
                    onEnd = {
                        val now = System.currentTimeMillis()
                        val e = LogEntry(log.size + 1, fmtDate(now), startTime,
                            fmtTime(now), fmtDur(now - startMs), note.trim())
                        log.add(e); saveLog(ctx, log)
                        active = false; startTime = ""; startMs = 0L; note = ""
                    },
                    onViewLog = { tab = Tab.Log },
                )
                Tab.Log        -> LogScreen(pad, log)
                Tab.Calculator -> CalculatorScreen(pad)
                Tab.Reference  -> ReferenceScreen(pad)
                Tab.Contact    -> ContactScreen(pad)
            }
        }
    }
}

// ── Screen 1: Home ────────────────────────────────────────────────────────────

@Composable
private fun HomeScreen(
    pad: PaddingValues,
    log: List<LogEntry>,
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

    var elapsedSec by remember { mutableStateOf(0L) }
    LaunchedEffect(active, startMs) {
        if (!active) { elapsedSec = 0L; return@LaunchedEffect }
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
        // Gradient hero banner
        val primary = MaterialTheme.colorScheme.primary
        val secondary = MaterialTheme.colorScheme.secondary
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(bottomStart = 28.dp, bottomEnd = 28.dp))
                .background(Brush.linearGradient(listOf(primary, secondary)))
                .padding(horizontal = 20.dp, vertical = 24.dp),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    BuildConfig.COMPANY_NAME,
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                    color = Color.White,
                    maxLines = 2,
                )
                Text(
                    BuildConfig.EXPORT_TITLE,
                    style = MaterialTheme.typography.bodyMedium,
                    color = Color.White.copy(alpha = 0.80f),
                )
                if (active) {
                    Spacer(Modifier.height(12.dp))
                    SessionStatusChip(elapsedSec)
                }
            }
        }

        Column(
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Row(modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatCard(Modifier.weight(1f), "Today", todayCount.toString(),
                    MaterialTheme.colorScheme.primaryContainer,
                    MaterialTheme.colorScheme.onPrimaryContainer)
                StatCard(Modifier.weight(1f), "Total", log.size.toString(),
                    MaterialTheme.colorScheme.secondaryContainer,
                    MaterialTheme.colorScheme.onSecondaryContainer)
            }

            SessionCard(active, startTime, note, onNoteChange, onStart, onEnd)

            if (log.isNotEmpty()) {
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Recent Activity",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold)
                    if (log.size > 3) {
                        TextButton(onClick = onViewLog,
                            contentPadding = PaddingValues(horizontal = 8.dp)) {
                            Text("See all →",
                                style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
                log.reversed().take(3).forEach { EntryRow(it) }
            } else {
                EmptyHomeState()
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

@Composable
private fun SessionStatusChip(elapsedSec: Long) {
    val infiniteTransition = rememberInfiniteTransition(label = "pulse")
    val dotScale by infiniteTransition.animateFloat(
        initialValue = 0.8f, targetValue = 1.3f,
        animationSpec = infiniteRepeatable(
            animation = tween(700, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "dot_scale",
    )
    Surface(
        shape = RoundedCornerShape(24.dp),
        color = Color.White.copy(alpha = 0.20f),
    ) {
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
private fun EmptyHomeState() {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        shape = RoundedCornerShape(20.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp, vertical = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                Icons.Outlined.Schedule, null,
                Modifier.size(44.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.35f),
            )
            Text(
                "Nothing logged yet",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "Tap \"${BuildConfig.ROLE_VERB_START}\" above to record your first ${BuildConfig.ROLE_NOUN}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.65f),
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun StatCard(
    mod: Modifier, label: String, value: String,
    bg: Color, fg: Color,
) {
    Card(
        mod,
        colors = CardDefaults.cardColors(containerColor = bg),
        shape = RoundedCornerShape(16.dp),
        elevation = CardDefaults.elevatedCardElevation(defaultElevation = 2.dp),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(vertical = 16.dp, horizontal = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(value,
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold, color = fg)
            Text(label,
                style = MaterialTheme.typography.labelSmall,
                color = fg.copy(alpha = 0.75f))
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
                            else MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)
                        ),
                )
                Text(
                    if (active) "${BuildConfig.ROLE_NOUN.replaceFirstChar { it.titlecase() }} started at $startTime"
                    else "No active ${BuildConfig.ROLE_NOUN}",
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

            val btnColors = if (active)
                ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.error,
                    contentColor = MaterialTheme.colorScheme.onError,
                )
            else ButtonDefaults.buttonColors()

            Button(
                onClick = if (active) onEnd else onStart,
                modifier = Modifier.fillMaxWidth().height(48.dp),
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

// ── Screen 2: Log ─────────────────────────────────────────────────────────────

@Composable
private fun LogScreen(pad: PaddingValues, log: List<LogEntry>) {
    val uniqueDays = remember(log.size) { log.map { it.date }.toSet().size }

    Column(
        Modifier
            .fillMaxSize()
            .padding(pad)
            .padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        Text(
            BuildConfig.EXPORT_TITLE,
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.Bold,
            modifier = Modifier.padding(bottom = 12.dp),
        )

        if (log.isNotEmpty()) {
            // Stats summary row
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(bottom = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                LogStatPill(Modifier.weight(1f), log.size.toString(), "entries")
                LogStatPill(Modifier.weight(1f), uniqueDays.toString(), "days")
            }
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(8.dp),
                contentPadding = PaddingValues(bottom = 16.dp),
            ) {
                items(log.reversed()) { EntryRow(it) }
            }
        } else {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Icon(
                        Icons.Outlined.Schedule, null,
                        Modifier.size(56.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.3f),
                    )
                    Text(
                        "No entries yet",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        "Start a ${BuildConfig.ROLE_NOUN} on the Home tab",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                        textAlign = TextAlign.Center,
                    )
                }
            }
        }
    }
}

@Composable
private fun LogStatPill(mod: Modifier, value: String, label: String) {
    Surface(
        mod,
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
        tonalElevation = 2.dp,
    ) {
        Row(
            Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(value,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary)
            Text(label,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun EntryRow(e: LogEntry) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(14.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
            Box(
                Modifier
                    .width(4.dp)
                    .fillMaxHeight()
                    .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.6f)),
            )
            Column(
                Modifier
                    .weight(1f)
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
                        Surface(
                            shape = CircleShape,
                            color = MaterialTheme.colorScheme.primaryContainer,
                        ) {
                            Text(
                                "#${e.id}",
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                                modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
                            )
                        }
                        Column {
                            Text("${e.startTime} – ${e.endTime}",
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.Medium)
                            Text(e.date,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant)
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
}

// ── Screen 3: Calculator ──────────────────────────────────────────────────────

@Composable
private fun CalculatorScreen(pad: PaddingValues) {
    var inputA by rememberSaveable { mutableStateOf("") }
    var inputB by rememberSaveable { mutableStateOf("") }
    val formula = BuildConfig.CALC_FORMULA
    val singleInput = formula == "VAT_ADD" || formula == "STAMP_DUTY"

    val aNum = inputA.replace(",", "").toDoubleOrNull()
    val bNum = inputB.replace(",", "").toDoubleOrNull()
    val liveResult: String? = when {
        aNum == null || aNum <= 0.0 -> null
        !singleInput && (bNum == null || bNum == 0.0) -> null
        else -> calculate(formula, inputA, inputB)
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(pad)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column {
            Text(
                BuildConfig.CALC_TITLE,
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                BuildConfig.COMPANY_NAME,
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = RoundedCornerShape(20.dp),
            elevation = CardDefaults.elevatedCardElevation(defaultElevation = 2.dp),
        ) {
            Column(
                Modifier.padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                OutlinedTextField(
                    value = inputA, onValueChange = { inputA = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text(BuildConfig.CALC_LABEL_A) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    shape = RoundedCornerShape(12.dp),
                    leadingIcon = {
                        Icon(Icons.Default.Calculate, null,
                            Modifier.size(20.dp),
                            tint = MaterialTheme.colorScheme.primary)
                    },
                )
                if (!singleInput) {
                    OutlinedTextField(
                        value = inputB, onValueChange = { inputB = it },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text(BuildConfig.CALC_LABEL_B) },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                    )
                }
                if (!singleInput) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        Text(
                            "Result updates automatically as you type",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                        )
                    }
                }
            }
        }

        AnimatedVisibility(
            visible = liveResult != null,
            enter = expandVertically() + fadeIn(tween(300)),
            exit = shrinkVertically() + fadeOut(tween(200)),
        ) {
            Card(
                Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer),
                shape = RoundedCornerShape(20.dp),
                elevation = CardDefaults.elevatedCardElevation(defaultElevation = 3.dp),
            ) {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        BuildConfig.CALC_RESULT_LABEL,
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.7f),
                    )
                    Text(
                        liveResult ?: "",
                        style = MaterialTheme.typography.headlineMedium,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                        textAlign = TextAlign.Center,
                    )
                }
            }
        }

        if (formula == "VAT_ADD" && aNum != null && aNum > 0.0) {
            AnimatedVisibility(
                visible = true,
                enter = expandVertically() + fadeIn(tween(400)),
            ) {
                VatBreakdownCard(aNum)
            }
        }

        if (formula == "STAMP_DUTY") {
            SdltBreakdownCard()
        }

        Spacer(Modifier.height(8.dp))
    }
}

private fun calculate(formula: String, rawA: String, rawB: String): String {
    val a = rawA.replace(",", "").toDoubleOrNull() ?: return "Enter a valid number"
    val b = rawB.replace(",", "").toDoubleOrNull() ?: 0.0
    val fmt = NumberFormat.getCurrencyInstance(Locale.UK)
    return when (formula) {
        "MULTIPLY"   -> { if (b == 0.0) return "Enter both values"; fmt.format(a * b) }
        "DIVIDE"     -> { if (b == 0.0) return "Enter divisor"; fmt.format(a / b) }
        "PERCENT"    -> { if (b == 0.0) return "Enter percentage"; fmt.format(a * b / 100.0) }
        "VAT_ADD"    -> fmt.format(a * 1.20)
        "STAMP_DUTY" -> fmt.format(stampDuty(a))
        else         -> { if (b == 0.0) return "Enter both values"; fmt.format(a * b) }
    }
}

private fun stampDuty(v: Double): Double {
    var tax = 0.0
    if (v > 250_000.0)   tax += (minOf(v, 925_000.0)   - 250_000.0)   * 0.05
    if (v > 925_000.0)   tax += (minOf(v, 1_500_000.0) - 925_000.0)   * 0.10
    if (v > 1_500_000.0) tax += (v                     - 1_500_000.0) * 0.12
    return tax
}

@Composable
private fun VatBreakdownCard(net: Double) {
    val fmt = NumberFormat.getCurrencyInstance(Locale.UK)
    val vat = net * 0.20
    val gross = net * 1.20
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(16.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("VAT Breakdown",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary)
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            VatRow("Net amount", fmt.format(net), normal = true)
            VatRow("VAT (20%)", fmt.format(vat), normal = true)
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            VatRow("Gross total", fmt.format(gross), normal = false)
        }
    }
}

@Composable
private fun VatRow(label: String, value: String, normal: Boolean) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label,
            style = MaterialTheme.typography.bodySmall,
            fontWeight = if (normal) FontWeight.Normal else FontWeight.SemiBold,
            color = if (normal) MaterialTheme.colorScheme.onSurfaceVariant
                    else MaterialTheme.colorScheme.onSurface)
        Text(value,
            style = MaterialTheme.typography.bodySmall,
            fontWeight = if (normal) FontWeight.Medium else FontWeight.Bold,
            color = if (normal) MaterialTheme.colorScheme.onSurface
                    else MaterialTheme.colorScheme.primary)
    }
}

@Composable
private fun SdltBreakdownCard() {
    val bands = listOf(
        "Up to £250,000" to "0%",
        "£250,001 – £925,000" to "5%",
        "£925,001 – £1,500,000" to "10%",
        "Over £1,500,000" to "12%",
    )
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(16.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("SDLT Rate Bands",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary)
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            bands.forEach { (range, rate) ->
                Row(Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(range, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(rate, style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface)
                }
            }
        }
    }
}

// ── Screen 4: Reference ───────────────────────────────────────────────────────

@Composable
private fun ReferenceScreen(pad: PaddingValues) {
    val items = remember {
        try {
            val arr = JSONArray(BuildConfig.INFO_ITEMS_JSON)
            (0 until arr.length()).map { i ->
                arr.getJSONObject(i).let { it.getString("k") to it.getString("v") }
            }
        } catch (_: Exception) { emptyList() }
    }

    Column(
        Modifier
            .fillMaxSize()
            .padding(pad)
            .padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        Column(modifier = Modifier.padding(bottom = 20.dp)) {
            Text(
                BuildConfig.INFO_TITLE,
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                BuildConfig.COMPANY_NAME,
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        LazyColumn(
            verticalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = PaddingValues(bottom = 24.dp),
        ) {
            items(items) { (k, v) -> ReferenceRow(k, v) }
        }
    }
}

@Composable
private fun ReferenceRow(k: String, v: String) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(12.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
            Box(
                Modifier
                    .width(4.dp)
                    .fillMaxHeight()
                    .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.7f)),
            )
            Row(
                Modifier
                    .weight(1f)
                    .padding(horizontal = 16.dp, vertical = 14.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    k,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(16.dp))
                Text(
                    v,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                    textAlign = TextAlign.End,
                )
            }
        }
    }
}

// ── Screen 5: Contact ─────────────────────────────────────────────────────────

@Composable
private fun ContactScreen(pad: PaddingValues) {
    val ctx = LocalContext.current

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(pad)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        // Company avatar + identity header
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.primaryContainer),
            shape = RoundedCornerShape(20.dp),
            elevation = CardDefaults.elevatedCardElevation(defaultElevation = 2.dp),
        ) {
            Row(
                Modifier.padding(20.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Box(
                    Modifier
                        .size(56.dp)
                        .clip(CircleShape)
                        .background(MaterialTheme.colorScheme.primary),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        initials(BuildConfig.COMPANY_NAME),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onPrimary,
                    )
                }
                Column {
                    Text(
                        BuildConfig.COMPANY_NAME,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                        maxLines = 2,
                    )
                    Text(
                        "Reg. ${BuildConfig.COMPANY_NUMBER}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onPrimaryContainer.copy(alpha = 0.7f),
                    )
                }
            }
        }

        // Company info card
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = RoundedCornerShape(20.dp),
            elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
        ) {
            Column(
                Modifier.padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                if (BuildConfig.CONTACT_ADDRESS.isNotBlank())
                    InfoRow(Icons.Default.LocationOn, "Address", BuildConfig.CONTACT_ADDRESS)
                if (BuildConfig.SUPPORT_EMAIL.isNotBlank())
                    InfoRow(Icons.Default.Email, "Email", BuildConfig.SUPPORT_EMAIL)
                if (BuildConfig.COMPANY_DOMAIN.isNotBlank())
                    InfoRow(Icons.Default.Language, "Website", BuildConfig.COMPANY_DOMAIN)
            }
        }

        // Action buttons
        Text("Get in Touch",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurfaceVariant)

        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (BuildConfig.SUPPORT_EMAIL.isNotBlank()) {
                ContactButton(Icons.Default.Email, "Send Email",
                    BuildConfig.SUPPORT_EMAIL) {
                    ctx.startActivity(Intent(Intent.ACTION_SENDTO,
                        Uri.parse("mailto:${BuildConfig.SUPPORT_EMAIL}")))
                }
            }
            if (BuildConfig.CONTACT_ADDRESS.isNotBlank()) {
                ContactButton(Icons.Default.Map, "Get Directions", "Open in Maps") {
                    ctx.startActivity(Intent(Intent.ACTION_VIEW,
                        Uri.parse("geo:0,0?q=${Uri.encode(BuildConfig.CONTACT_ADDRESS)}")))
                }
            }
            if (BuildConfig.COMPANY_DOMAIN.isNotBlank()) {
                ContactButton(Icons.Default.OpenInBrowser, "Visit Website",
                    BuildConfig.COMPANY_DOMAIN) {
                    ctx.startActivity(Intent(Intent.ACTION_VIEW,
                        Uri.parse("https://${BuildConfig.COMPANY_DOMAIN}")))
                }
            }
            ContactButton(Icons.Default.Share, BuildConfig.ACTION_LABEL, "Share our details") {
                val text = buildString {
                    appendLine(BuildConfig.COMPANY_NAME)
                    appendLine("Reg: ${BuildConfig.COMPANY_NUMBER}")
                    if (BuildConfig.COMPANY_DOMAIN.isNotBlank())
                        appendLine("Web: https://${BuildConfig.COMPANY_DOMAIN}")
                    if (BuildConfig.SUPPORT_EMAIL.isNotBlank())
                        appendLine("Email: ${BuildConfig.SUPPORT_EMAIL}")
                }
                ctx.startActivity(Intent(Intent.ACTION_SEND).apply {
                    type = "text/plain"
                    putExtra(Intent.EXTRA_TEXT, text.trim())
                    putExtra(Intent.EXTRA_SUBJECT, BuildConfig.COMPANY_NAME)
                })
            }
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun InfoRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    value: String,
) {
    Row(
        verticalAlignment = Alignment.Top,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(
            Modifier
                .size(36.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(MaterialTheme.colorScheme.primaryContainer),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, null, Modifier.size(18.dp),
                tint = MaterialTheme.colorScheme.onPrimaryContainer)
        }
        Column(Modifier.weight(1f)) {
            Text(label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium)
        }
    }
}

@Composable
private fun ContactButton(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    description: String,
    onClick: () -> Unit,
) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(16.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Box(
                Modifier
                    .size(44.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primaryContainer),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, null, Modifier.size(22.dp),
                    tint = MaterialTheme.colorScheme.onPrimaryContainer)
            }
            Column(Modifier.weight(1f)) {
                Text(label,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold)
                Text(description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1)
            }
            Icon(Icons.Default.ChevronRight, null, Modifier.size(20.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f))
        }
    }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

private fun fmtTime(ms: Long)  = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(ms))
private fun fmtDate(ms: Long)  = SimpleDateFormat("d MMM yyyy", Locale.getDefault()).format(Date(ms))
private fun fmtDur(ms: Long): String {
    val m = (ms / 60_000).coerceAtLeast(1)
    val h = m / 60; val rem = m % 60
    return when { h > 0 && rem > 0 -> "${h}h ${rem}m"; h > 0 -> "${h}h"; else -> "${rem}m" }
}

private fun fmtElapsed(sec: Long): String {
    val h = sec / 3600; val m = (sec % 3600) / 60; val s = sec % 60
    return if (h > 0) String.format("%d:%02d:%02d", h, m, s)
    else String.format("%d:%02d", m, s)
}

private fun initials(name: String): String {
    val stopWords = setOf("LTD", "LIMITED", "LLP", "PLC", "THE", "AND", "OF")
    val words = name.split(Regex("\\s+"))
        .filter { it.length > 1 && it.uppercase() !in stopWords }
    return when {
        words.size >= 2 -> "${words[0][0]}${words[1][0]}"
        words.size == 1 -> words[0].take(2)
        else            -> name.take(2)
    }.uppercase()
}
