package uk.company.utility.screens

import android.content.Intent
import android.net.Uri
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.OpenInBrowser
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import uk.company.utility.BuildConfig
import uk.company.utility.core.initials

/**
 * Company identity and outbound actions. Every action hands off to another
 * app via an implicit Intent (mail, maps, browser, share sheet), which is why
 * none of them needs a permission.
 */
@Composable
fun ContactScreen(pad: PaddingValues) {
    val ctx = LocalContext.current

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(pad)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.primaryContainer,
            ),
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

        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            shape = RoundedCornerShape(20.dp),
            elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
        ) {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                if (BuildConfig.CONTACT_ADDRESS.isNotBlank())
                    InfoRow(Icons.Default.LocationOn, "Address", BuildConfig.CONTACT_ADDRESS)
                if (BuildConfig.SUPPORT_EMAIL.isNotBlank())
                    InfoRow(Icons.Default.Email, "Email", BuildConfig.SUPPORT_EMAIL)
                if (BuildConfig.COMPANY_DOMAIN.isNotBlank())
                    InfoRow(Icons.Default.Language, "Website", BuildConfig.COMPANY_DOMAIN)
            }
        }

        Text(
            "Get in Touch",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (BuildConfig.SUPPORT_EMAIL.isNotBlank()) {
                ContactButton(Icons.Default.Email, "Send Email", BuildConfig.SUPPORT_EMAIL) {
                    ctx.startActivity(
                        Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:${BuildConfig.SUPPORT_EMAIL}")),
                    )
                }
            }
            if (BuildConfig.CONTACT_ADDRESS.isNotBlank()) {
                ContactButton(Icons.Default.Map, "Get Directions", "Open in Maps") {
                    ctx.startActivity(
                        Intent(
                            Intent.ACTION_VIEW,
                            Uri.parse("geo:0,0?q=${Uri.encode(BuildConfig.CONTACT_ADDRESS)}"),
                        ),
                    )
                }
            }
            if (BuildConfig.COMPANY_DOMAIN.isNotBlank()) {
                ContactButton(Icons.Default.OpenInBrowser, "Visit Website", BuildConfig.COMPANY_DOMAIN) {
                    ctx.startActivity(
                        Intent(Intent.ACTION_VIEW, Uri.parse("https://${BuildConfig.COMPANY_DOMAIN}")),
                    )
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
                ctx.startActivity(
                    Intent(Intent.ACTION_SEND).apply {
                        type = "text/plain"
                        putExtra(Intent.EXTRA_TEXT, text.trim())
                        putExtra(Intent.EXTRA_SUBJECT, BuildConfig.COMPANY_NAME)
                    },
                )
            }
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun InfoRow(icon: ImageVector, label: String, value: String) {
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
            Icon(
                icon, null, Modifier.size(18.dp),
                tint = MaterialTheme.colorScheme.onPrimaryContainer,
            )
        }
        Column(Modifier.weight(1f)) {
            Text(
                label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(value, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
        }
    }
}

@Composable
private fun ContactButton(
    icon: ImageVector,
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
                Icon(
                    icon, null, Modifier.size(22.dp),
                    tint = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Column(Modifier.weight(1f)) {
                Text(
                    label,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
            Icon(
                Icons.Default.ChevronRight, null, Modifier.size(20.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f),
            )
        }
    }
}
