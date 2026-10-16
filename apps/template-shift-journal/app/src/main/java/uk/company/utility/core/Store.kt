package uk.company.utility.core

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * On-device record storage.
 *
 * SharedPreferences holding a JSON array, nothing else. No network, no
 * account, no identifier — which is what lets the Data Safety declaration say
 * the app collects nothing. Archetypes pick their own key and their own row
 * shape; the store neither knows nor cares what is in a row.
 */
object Store {
    private const val PREFS = "app_data"

    fun read(ctx: Context, key: String): List<JSONObject> {
        val raw = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(key, null) ?: return emptyList()
        return try {
            val arr = JSONArray(raw)
            (0 until arr.length()).map { arr.getJSONObject(it) }
        } catch (_: Exception) {
            // A row written by an older version that no longer parses is
            // dropped rather than crashing the app on launch.
            emptyList()
        }
    }

    fun write(ctx: Context, key: String, rows: List<JSONObject>) {
        val arr = JSONArray()
        rows.forEach { arr.put(it) }
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(key, arr.toString()).apply()
    }
}
