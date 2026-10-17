package uk.company.utility.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import uk.company.utility.BuildConfig
import uk.company.utility.core.AccentCard
import uk.company.utility.core.ScreenTitle

/**
 * Trade figures for the company's sector, baked in at build time from
 * apps/generator/sic_map.py. Offline by design — no fetch, no cache, no
 * network permission.
 */
@Composable
fun ReferenceScreen(pad: PaddingValues) {
    val items = remember {
        try {
            val arr = JSONArray(BuildConfig.INFO_ITEMS_JSON)
            (0 until arr.length()).map { i ->
                arr.getJSONObject(i).let { it.getString("k") to it.getString("v") }
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .padding(pad)
            .padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        ScreenTitle(BuildConfig.INFO_TITLE, BuildConfig.COMPANY_NAME)

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
    AccentCard {
        Row(
            Modifier
                .fillMaxWidth()
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
                // Weighted, or a long value takes the width it wants and
                // leaves the key a sliver to break inside a word in:
                // "Companies House Filing" rendered as "Companie / s House /
                // Filing" next to "Annual confirmation statement". fill=false
                // so a short value ("20%") still sits against the right edge
                // instead of padding out to half the row.
                modifier = Modifier.weight(1f, fill = false),
            )
        }
    }
}
