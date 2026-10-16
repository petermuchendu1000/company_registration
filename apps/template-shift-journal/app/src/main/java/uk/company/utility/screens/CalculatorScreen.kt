package uk.company.utility.screens

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Calculate
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import uk.company.utility.BuildConfig
import uk.company.utility.core.BreakdownRow
import uk.company.utility.core.ScreenTitle
import java.text.NumberFormat
import java.util.Locale

/**
 * Trade calculator. Shared by every archetype: the formula and the labels come
 * from the company's SIC code (apps/generator/sic_map.py), not from the
 * archetype, because a courier and a care provider can both need a rate
 * multiplier while recording completely different things.
 */
@Composable
fun CalculatorScreen(pad: PaddingValues) {
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
        ScreenTitle(BuildConfig.CALC_TITLE, BuildConfig.COMPANY_NAME)

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
                        Icon(
                            Icons.Default.Calculate, null,
                            Modifier.size(20.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
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
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
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
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                ),
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
            AnimatedVisibility(visible = true, enter = expandVertically() + fadeIn(tween(400))) {
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
        "MULTIPLY" -> { if (b == 0.0) return "Enter both values"; fmt.format(a * b) }
        "DIVIDE" -> { if (b == 0.0) return "Enter divisor"; fmt.format(a / b) }
        "PERCENT" -> { if (b == 0.0) return "Enter percentage"; fmt.format(a * b / 100.0) }
        "VAT_ADD" -> fmt.format(a * 1.20)
        "STAMP_DUTY" -> fmt.format(stampDuty(a))
        else -> { if (b == 0.0) return "Enter both values"; fmt.format(a * b) }
    }
}

private fun stampDuty(v: Double): Double {
    var tax = 0.0
    if (v > 250_000.0) tax += (minOf(v, 925_000.0) - 250_000.0) * 0.05
    if (v > 925_000.0) tax += (minOf(v, 1_500_000.0) - 925_000.0) * 0.10
    if (v > 1_500_000.0) tax += (v - 1_500_000.0) * 0.12
    return tax
}

@Composable
private fun VatBreakdownCard(net: Double) {
    val fmt = NumberFormat.getCurrencyInstance(Locale.UK)
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape = RoundedCornerShape(16.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                "VAT Breakdown",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary,
            )
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            BreakdownRow("Net amount", fmt.format(net))
            BreakdownRow("VAT (20%)", fmt.format(net * 0.20))
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            BreakdownRow("Gross total", fmt.format(net * 1.20), emphasis = true)
        }
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
            Text(
                "SDLT Rate Bands",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary,
            )
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            bands.forEach { (range, rate) -> BreakdownRow(range, rate) }
        }
    }
}
