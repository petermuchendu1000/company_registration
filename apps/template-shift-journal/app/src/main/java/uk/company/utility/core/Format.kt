package uk.company.utility.core

import java.text.NumberFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

fun fmtTime(ms: Long): String = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(ms))

fun fmtDate(ms: Long): String = SimpleDateFormat("d MMM yyyy", Locale.getDefault()).format(Date(ms))

fun fmtMonth(ms: Long): String = SimpleDateFormat("MMMM yyyy", Locale.getDefault()).format(Date(ms))

fun fmtDur(ms: Long): String {
    val m = (ms / 60_000).coerceAtLeast(1)
    val h = m / 60
    val rem = m % 60
    return when {
        h > 0 && rem > 0 -> "${h}h ${rem}m"
        h > 0 -> "${h}h"
        else -> "${rem}m"
    }
}

fun fmtElapsed(sec: Long): String {
    val h = sec / 3600
    val m = (sec % 3600) / 60
    val s = sec % 60
    return if (h > 0) String.format(Locale.UK, "%d:%02d:%02d", h, m, s)
    else String.format(Locale.UK, "%d:%02d", m, s)
}

/** Pounds from a pence amount, e.g. 4500 -> "£45.00". */
fun fmtPence(pence: Long): String =
    NumberFormat.getCurrencyInstance(Locale.UK).format(pence / 100.0)

fun fmtMiles(miles: Int): String =
    NumberFormat.getIntegerInstance(Locale.UK).format(miles)

fun initials(name: String): String {
    val stopWords = setOf("LTD", "LIMITED", "LLP", "PLC", "THE", "AND", "OF")
    val words = name.split(Regex("\\s+"))
        .filter { it.length > 1 && it.uppercase() !in stopWords }
    return when {
        words.size >= 2 -> "${words[0][0]}${words[1][0]}"
        words.size == 1 -> words[0].take(2)
        else -> name.take(2)
    }.uppercase()
}

/** The right noun for [n]: "line" or "lines", "entry" or "entries". */
fun pluralNoun(n: Int, singular: String, pluralForm: String = singular + "s"): String =
    if (n == 1) singular else pluralForm

/**
 * "1 line", "3 lines". Every count the UI shows sits next to a noun, and
 * every one of them read "1 lines" until a store screenshot of a one-line
 * estimate made it obvious.
 */
fun plural(n: Int, singular: String, pluralForm: String = singular + "s"): String =
    "$n " + pluralNoun(n, singular, pluralForm)
