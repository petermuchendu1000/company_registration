package uk.company.utility.arch

import android.content.Context
import android.content.Intent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
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
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.FolderOpen
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import uk.company.utility.BuildConfig
import uk.company.utility.core.plural
import uk.company.utility.core.AccentCard
import uk.company.utility.core.Archetype
import uk.company.utility.core.BreakdownRow
import uk.company.utility.core.EmptyState
import uk.company.utility.core.FeatureTab
import uk.company.utility.core.HeroHeader
import uk.company.utility.core.ScreenTitle
import uk.company.utility.core.SharedScreen
import uk.company.utility.core.StatPill
import uk.company.utility.core.Store
import uk.company.utility.core.fmtDate
import uk.company.utility.core.fmtPence

/**
 * Quote builder: a priced document, not a log.
 *
 * The user assembles line items — a description, a quantity and a unit price —
 * and the app totals them, adds VAT and keeps the finished quote against a
 * client name. What is recorded is work not yet done, which is the opposite
 * of the shift and mileage archetypes; they record work already done.
 *
 * No generic rate calculator tab: this screen already does the arithmetic the
 * trade needs, and bolting the shared one on would make the app look like
 * every other app in the catalog.
 */
object QuoteArchetype : Archetype {
    override val id: String = "quote"

    override val sharedScreens: List<SharedScreen> =
        listOf(SharedScreen.REFERENCE, SharedScreen.CONTACT)

    @Composable
    override fun rememberTabs(navigate: (String) -> Unit): List<FeatureTab> {
        val ctx = LocalContext.current
        val saved = remember { mutableStateListOf<Quote>() }
        var loaded by remember { mutableStateOf(false) }

        LaunchedEffect(Unit) {
            if (!loaded) {
                saved.addAll(loadQuotes(ctx))
                loaded = true
            }
        }

        return listOf(
            FeatureTab(
                key = "builder",
                label = BuildConfig.ROLE_NOUN.replaceFirstChar { it.titlecase() },
                filledIcon = Icons.Filled.Description,
                outlinedIcon = Icons.Outlined.Description,
            ) { pad ->
                BuilderScreen(
                    pad = pad,
                    onSave = { client, lines ->
                        saved.add(
                            Quote(
                                id = (saved.maxOfOrNull { it.id } ?: 0) + 1,
                                atMs = System.currentTimeMillis(),
                                client = client,
                                lines = lines,
                            ),
                        )
                        saveQuotes(ctx, saved)
                        navigate("saved")
                    },
                )
            },
            FeatureTab(
                key = "saved",
                label = BuildConfig.EXPORT_TITLE,
                filledIcon = Icons.Filled.FolderOpen,
                outlinedIcon = Icons.Outlined.FolderOpen,
            ) { pad -> SavedScreen(pad, saved) },
        )
    }
}

/** The archetype the build selected. One definition per archetype source set. */
fun activeArchetype(): Archetype = QuoteArchetype

// ── model ───────────────────────────────────────────────────────────────────

private const val KEY_QUOTES = "quote_documents"

data class Line(val description: String, val qty: Int, val unitPence: Long) {
    val totalPence: Long get() = qty * unitPence
}

data class Quote(
    val id: Int,
    val atMs: Long,
    val client: String,
    val lines: List<Line>,
) {
    val netPence: Long get() = lines.sumOf { it.totalPence }
    val vatPence: Long get() = netPence * BuildConfig.VAT_PERCENT / 100
    val grossPence: Long get() = netPence + vatPence
}

private fun saveQuotes(ctx: Context, rows: List<Quote>) {
    Store.write(
        ctx, KEY_QUOTES,
        rows.map { q ->
            JSONObject().apply {
                put("id", q.id)
                put("at", q.atMs)
                put("client", q.client)
                put(
                    "lines",
                    JSONArray().apply {
                        q.lines.forEach { l ->
                            put(
                                JSONObject().apply {
                                    put("d", l.description)
                                    put("q", l.qty)
                                    put("u", l.unitPence)
                                },
                            )
                        }
                    },
                )
            }
        },
    )
}

private fun loadQuotes(ctx: Context): List<Quote> =
    Store.read(ctx, KEY_QUOTES).map { o ->
        val arr = o.optJSONArray("lines") ?: JSONArray()
        Quote(
            id = o.optInt("id"),
            atMs = o.optLong("at"),
            client = o.optString("client"),
            lines = (0 until arr.length()).map { i ->
                arr.getJSONObject(i).let {
                    Line(it.optString("d"), it.optInt("q", 1), it.optLong("u"))
                }
            },
        )
    }

/** Pence from a "12.50"-style field. Returns null when it is not a price. */
private fun parsePence(raw: String): Long? {
    val cleaned = raw.replace(",", "").replace("£", "").trim()
    if (cleaned.isEmpty()) return null
    val value = cleaned.toDoubleOrNull() ?: return null
    if (value < 0) return null
    return Math.round(value * 100)
}

// ── screens ─────────────────────────────────────────────────────────────────

@Composable
private fun BuilderScreen(
    pad: PaddingValues,
    onSave: (client: String, lines: List<Line>) -> Unit,
) {
    val ctx = LocalContext.current
    var client by rememberSaveable { mutableStateOf("") }
    val lines = remember { mutableStateListOf<Line>() }

    var description by rememberSaveable { mutableStateOf("") }
    var qty by rememberSaveable { mutableStateOf("1") }
    var unitPrice by rememberSaveable { mutableStateOf("") }

    val qtyValue = qty.filter { it.isDigit() }.toIntOrNull()
    val unitPenceValue = parsePence(unitPrice)
    val canAddLine = description.isNotBlank() && qtyValue != null && qtyValue > 0 &&
        unitPenceValue != null && unitPenceValue > 0

    val net = lines.sumOf { it.totalPence }
    val vat = net * BuildConfig.VAT_PERCENT / 100

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
                    if (lines.isEmpty()) "No lines yet"
                    else "${plural(lines.size, "line")} · ${fmtPence(net + vat)}",
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
            OutlinedTextField(
                value = client,
                onValueChange = { client = it },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Client or site") },
                placeholder = { Text("Who is this ${BuildConfig.ROLE_NOUN} for?") },
                singleLine = true,
                shape = RoundedCornerShape(12.dp),
            )

            Card(
                Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                shape = RoundedCornerShape(20.dp),
                elevation = CardDefaults.elevatedCardElevation(defaultElevation = 2.dp),
            ) {
                Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    Text(
                        "Add a line",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    OutlinedTextField(
                        value = description,
                        onValueChange = { description = it },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Description") },
                        placeholder = { Text("Labour, materials, call-out…") },
                        singleLine = true,
                        shape = RoundedCornerShape(12.dp),
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        OutlinedTextField(
                            value = qty,
                            onValueChange = { qty = it },
                            modifier = Modifier.weight(1f),
                            label = { Text(BuildConfig.LINE_UNIT_LABEL) },
                            supportingText = { Text("Quantity") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            singleLine = true,
                            shape = RoundedCornerShape(12.dp),
                        )
                        OutlinedTextField(
                            value = unitPrice,
                            onValueChange = { unitPrice = it },
                            modifier = Modifier.weight(1f),
                            label = { Text("Unit price") },
                            supportingText = { Text("£ excl. VAT") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                            singleLine = true,
                            shape = RoundedCornerShape(12.dp),
                        )
                    }
                    OutlinedButton(
                        onClick = {
                            val q = qtyValue ?: return@OutlinedButton
                            val u = unitPenceValue ?: return@OutlinedButton
                            lines.add(Line(description.trim(), q, u))
                            description = ""
                            qty = "1"
                            unitPrice = ""
                        },
                        enabled = canAddLine,
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(46.dp),
                        shape = RoundedCornerShape(14.dp),
                    ) {
                        Icon(Icons.Default.Add, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Add line", fontWeight = FontWeight.SemiBold)
                    }
                }
            }

            AnimatedVisibility(
                visible = lines.isNotEmpty(),
                enter = expandVertically() + fadeIn(tween(250)),
            ) {
                Card(
                    Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    shape = RoundedCornerShape(20.dp),
                    elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
                ) {
                    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        lines.forEachIndexed { index, line ->
                            Row(
                                Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        line.description,
                                        style = MaterialTheme.typography.bodyMedium,
                                        fontWeight = FontWeight.Medium,
                                    )
                                    Text(
                                        "${line.qty} × ${fmtPence(line.unitPence)}",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                Text(
                                    fmtPence(line.totalPence),
                                    style = MaterialTheme.typography.bodyMedium,
                                    fontWeight = FontWeight.SemiBold,
                                )
                                IconButton(onClick = { lines.removeAt(index) }) {
                                    Icon(
                                        Icons.Default.Close,
                                        contentDescription = "Remove ${line.description}",
                                        modifier = Modifier.size(18.dp),
                                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                        }
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        BreakdownRow("Net", fmtPence(net))
                        BreakdownRow("VAT (${BuildConfig.VAT_PERCENT}%)", fmtPence(vat))
                        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                        BreakdownRow("Total", fmtPence(net + vat), emphasis = true)
                    }
                }
            }

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    onClick = {
                        onSave(client.trim().ifBlank { "Unnamed" }, lines.toList())
                        lines.clear()
                        client = ""
                    },
                    enabled = lines.isNotEmpty(),
                    modifier = Modifier
                        .weight(1f)
                        .height(48.dp),
                    shape = RoundedCornerShape(14.dp),
                ) {
                    Icon(Icons.Default.Save, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text(BuildConfig.ROLE_VERB_END, fontWeight = FontWeight.SemiBold)
                }
                OutlinedButton(
                    onClick = {
                        val text = quoteAsText(client.trim().ifBlank { "Unnamed" }, lines)
                        ctx.startActivity(
                            Intent(Intent.ACTION_SEND).apply {
                                type = "text/plain"
                                putExtra(Intent.EXTRA_TEXT, text)
                                putExtra(
                                    Intent.EXTRA_SUBJECT,
                                    "${BuildConfig.COMPANY_NAME} — ${BuildConfig.ROLE_NOUN}",
                                )
                            },
                        )
                    },
                    enabled = lines.isNotEmpty(),
                    modifier = Modifier
                        .weight(1f)
                        .height(48.dp),
                    shape = RoundedCornerShape(14.dp),
                ) {
                    Icon(Icons.Default.Share, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Send", fontWeight = FontWeight.SemiBold)
                }
            }

            if (lines.isEmpty()) {
                EmptyState(
                    icon = Icons.Outlined.Description,
                    title = "Nothing priced yet",
                    body = "Add labour and materials above to build this " +
                        "${BuildConfig.ROLE_NOUN}; the totals and VAT work themselves out.",
                )
            }
            Spacer(Modifier.height(8.dp))
        }
    }
}

/** The quote as plain text, for the share sheet. No formatting a phone cannot send. */
private fun quoteAsText(client: String, lines: List<Line>): String {
    val net = lines.sumOf { it.totalPence }
    val vat = net * BuildConfig.VAT_PERCENT / 100
    return buildString {
        appendLine(BuildConfig.COMPANY_NAME)
        appendLine("${BuildConfig.ROLE_NOUN.replaceFirstChar { it.titlecase() }} for $client")
        appendLine()
        lines.forEach { line ->
            appendLine("${line.description}  ${line.qty} × ${fmtPence(line.unitPence)}  = ${fmtPence(line.totalPence)}")
        }
        appendLine()
        appendLine("Net: ${fmtPence(net)}")
        appendLine("VAT (${BuildConfig.VAT_PERCENT}%): ${fmtPence(vat)}")
        appendLine("Total: ${fmtPence(net + vat)}")
        if (BuildConfig.COMPANY_NUMBER.isNotBlank()) {
            appendLine()
            appendLine("Reg: ${BuildConfig.COMPANY_NUMBER}")
        }
    }.trim()
}

@Composable
private fun SavedScreen(pad: PaddingValues, quotes: List<Quote>) {
    val ordered = remember(quotes.size) { quotes.sortedByDescending { it.atMs } }
    val pipeline = remember(quotes.size) { quotes.sumOf { it.grossPence } }

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
                    icon = Icons.Outlined.FolderOpen,
                    title = "No saved ${BuildConfig.EXPORT_TITLE.lowercase()}",
                    body = "Build a ${BuildConfig.ROLE_NOUN} and save it to keep it here",
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
            StatPill(Modifier.weight(1f), ordered.size.toString(), "saved")
            StatPill(Modifier.weight(1f), fmtPence(pipeline), "total")
        }

        LazyColumn(
            verticalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = PaddingValues(bottom = 24.dp),
        ) {
            items(ordered, key = { it.id }) { QuoteRow(it) }
        }
    }
}

@Composable
private fun QuoteRow(quote: Quote) {
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
                        quote.client,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Medium,
                    )
                    Text(
                        "${fmtDate(quote.atMs)} · ${plural(quote.lines.size, "line")}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Surface(
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.secondaryContainer,
                ) {
                    Text(
                        fmtPence(quote.grossPence),
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                }
            }
        }
    }
}
