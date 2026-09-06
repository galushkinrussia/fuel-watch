package com.example.fuelwatch

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class Station(
    val osmId: String,
    val brand: String,
    val addr: String,
    val distanceKm: Double,
    val status: String,
    val detail: String,
    val fuelsNow: String,
    val lastAt: String,
)

object FuelApi {
    private const val BASE = "https://gdebenz.ru/api/nearby"
    private const val UA = "Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 FuelWatch/1.0"

    fun fetch(lat: Double, lon: Double, radiusKm: Double): Pair<List<Station>, String?> {
        val url = "$BASE?lat=$lat&lon=$lon&radius_km=$radiusKm&full=1"
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            setRequestProperty("User-Agent", UA)
            setRequestProperty("Accept", "application/json")
            setRequestProperty("Referer", "https://gdebenz.ru/")
            connectTimeout = 15000
            readTimeout = 15000
        }
        val code = conn.responseCode
        val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
            ?.bufferedReader()?.use { it.readText() } ?: ""
        conn.disconnect()
        if (code !in 200..299) return emptyList<Station>() to null

        val root = JSONObject(body)
        val updated = root.optString("updated").ifBlank { null }
        val arr = root.optJSONArray("stations") ?: return emptyList<Station>() to updated
        val list = ArrayList<Station>(arr.length())
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            list.add(
                Station(
                    osmId = o.optString("osm_id"),
                    brand = o.optString("brand").ifBlank { o.optString("name") }.ifBlank { "АЗС" },
                    addr = o.optString("addr"),
                    distanceKm = o.optDouble("distance_km"),
                    status = o.optString("status"),
                    detail = o.optString("detail"),
                    fuelsNow = o.optString("fuels_now"),
                    lastAt = o.optString("last_at"),
                )
            )
        }
        return list to updated
    }
}

private val AVAILABLE = setOf("yes", "queue")

fun normalizeFuel(f: String): String {
    val t = f.trim().lowercase()
    return when (t) {
        "dt", "дт", "diesel", "дизель" -> "ДТ"
        "92", "95", "98", "100" -> t
        else -> f.trim().uppercase()
    }
}

fun stationFuels(s: Station): Set<String> {
    val fuels = HashSet<String>()
    s.fuelsNow.replace(',', ' ').split(Regex("\\s+")).forEach {
        if (it.isNotBlank()) fuels.add(normalizeFuel(it))
    }
    val head = s.detail.split(Regex("Очередь|Лимит|·"))[0]
    Regex("(?:^|[\\s,])(92|95|98|100|ДТ|дт|Дт)(?=[\\s,]|$)").findAll(head).forEach {
        fuels.add(normalizeFuel(it.groupValues[1]))
    }
    return fuels
}

fun stationAvailable(s: Station, wanted: Set<String>): Boolean {
    if (s.status !in AVAILABLE) return false
    if (wanted.isEmpty()) return true
    return (stationFuels(s) intersect wanted).isNotEmpty()
}
