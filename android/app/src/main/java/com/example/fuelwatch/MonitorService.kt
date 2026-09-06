package com.example.fuelwatch

import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log

class MonitorService : Service() {

    companion object {
        const val ACTION_START = "com.example.fuelwatch.START"
        const val ACTION_STOP = "com.example.fuelwatch.STOP"
        private const val TAG = "FuelWatch"
    }

    @Volatile private var running = false
    private var worker: Thread? = null
    private var seen: MutableMap<String, Boolean> = HashMap()
    private var baselineDone = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        NotificationHelper.ensureChannel(this)
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
            else -> start()
        }
        return START_STICKY
    }

    private fun start() {
        if (running) return
        running = true
        seen = Prefs.seen(this)
        Prefs.setRunning(this, true)

        val notif = NotificationHelper.serviceNotification(this, "Опрашиваю АЗС…")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NotificationHelper.SERVICE_NOTIF_ID, notif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            )
        } else {
            startForeground(NotificationHelper.SERVICE_NOTIF_ID, notif)
        }

        worker = Thread { loop() }.also { it.start() }
    }

    private fun loop() {
        while (running) {
            try {
                poll()
            } catch (e: Exception) {
                Log.w(TAG, "poll failed", e)
            }
            try {
                val intervalMs = Prefs.interval(this) * 1000L
                Thread.sleep(intervalMs)
            } catch (e: InterruptedException) {
                break
            }
        }
    }

    private fun poll() {
        val ctx = applicationContext
        val wanted = Prefs.fuels(ctx)
        val (stations, updated) = FuelApi.fetch(
            Prefs.lat(ctx), Prefs.lon(ctx), Prefs.radius(ctx)
        )

        if (stations.isEmpty()) return

        var withFuel = 0
        for (s in stations) {
            val avail = stationAvailable(s, wanted)
            val prev = seen[s.osmId]
            if (avail && prev == false && baselineDone) {
                val fuels = stationFuels(s).sorted().joinToString(", ").ifBlank { "?" }
                val label = "${s.brand} · ${s.addr.ifBlank { "без адреса" }}"
                val detail = s.detail.ifBlank { "" }
                NotificationHelper.alert(
                    ctx, "⛽ Бензин появился",
                    "$label\n$fuels${if (detail.isNotBlank()) "\n$detail" else ""}"
                )
            }
            if (avail) withFuel++
            seen[s.osmId] = avail
        }
        baselineDone = true
        Prefs.saveSeen(ctx, seen)

        val nm = getSystemService(NOTIFICATION_SERVICE) as android.app.NotificationManager
        val summary = "Обновлено: ${updated ?: "—"} · с топливом: $withFuel из ${stations.size}"
        nm.notify(
            NotificationHelper.SERVICE_NOTIF_ID,
            NotificationHelper.serviceNotification(this, summary)
        )
    }

    override fun onDestroy() {
        running = false
        Prefs.setRunning(this, false)
        worker?.interrupt()
        worker = null
        super.onDestroy()
    }
}
