package com.example.fuelwatch

import android.content.Context
import android.content.SharedPreferences

object Prefs {
    private const val NAME = "fuelwatch"
    // Нейтральный дефолт (центр города) — свои координаты вводите в приложении.
    const val DEFAULT_LAT = 48.7080
    const val DEFAULT_LON = 44.5148
    const val DEFAULT_RADIUS = 8.0

    private fun sp(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    fun lat(ctx: Context) = sp(ctx).getFloat("lat", DEFAULT_LAT.toFloat()).toDouble()
    fun lon(ctx: Context) = sp(ctx).getFloat("lon", DEFAULT_LON.toFloat()).toDouble()
    fun radius(ctx: Context) = sp(ctx).getFloat("radius", DEFAULT_RADIUS.toFloat()).toDouble()
    fun interval(ctx: Context) = sp(ctx).getInt("interval", 180)
    fun fuels(ctx: Context): Set<String> =
        sp(ctx).getString("fuels", "92,95")!!.split(',').map { it.trim() }
            .filter { it.isNotEmpty() }.toSet()
    fun running(ctx: Context) = sp(ctx).getBoolean("running", false)
    fun topic(ctx: Context): String = sp(ctx).getString("topic", "") ?: ""

    fun save(ctx: Context, lat: Double, lon: Double, radius: Double, interval: Int, fuels: Set<String>) {
        sp(ctx).edit()
            .putFloat("lat", lat.toFloat())
            .putFloat("lon", lon.toFloat())
            .putFloat("radius", radius.toFloat())
            .putInt("interval", interval)
            .putString("fuels", fuels.joinToString(","))
            .apply()
    }

    fun setRunning(ctx: Context, running: Boolean) {
        sp(ctx).edit().putBoolean("running", running).apply()
    }

    // сохранение последнего известного состояния АЗС (для детекции перехода)
    fun seen(ctx: Context): MutableMap<String, Boolean> {
        val raw = sp(ctx).getString("seen", "{}") ?: "{}"
        return try {
            val map = HashMap<String, Boolean>()
            org.json.JSONObject(raw).keys().forEach { k -> map[k] = org.json.JSONObject(raw).getBoolean(k) }
            map
        } catch (e: Exception) {
            HashMap()
        }
    }

    fun saveSeen(ctx: Context, seen: Map<String, Boolean>) {
        val o = org.json.JSONObject()
        seen.forEach { (k, v) -> o.put(k, v) }
        sp(ctx).edit().putString("seen", o.toString()).apply()
    }
}
