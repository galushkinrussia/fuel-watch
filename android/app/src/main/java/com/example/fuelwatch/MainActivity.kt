package com.example.fuelwatch

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {

    private lateinit var latInput: EditText
    private lateinit var lonInput: EditText
    private lateinit var radiusInput: EditText
    private lateinit var intervalInput: EditText
    private lateinit var statusText: TextView
    private lateinit var toggleButton: Button

    private val notifPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        latInput = findViewById(R.id.latInput)
        lonInput = findViewById(R.id.lonInput)
        radiusInput = findViewById(R.id.radiusInput)
        intervalInput = findViewById(R.id.intervalInput)
        statusText = findViewById(R.id.statusText)
        toggleButton = findViewById(R.id.toggleButton)

        NotificationHelper.ensureChannel(this)
        loadPrefs()

        toggleButton.setOnClickListener {
            if (Prefs.running(this)) {
                stopMonitoring()
            } else {
                ensurePermissionAndStart()
            }
        }
        refreshButtonState()
    }

    private fun loadPrefs() {
        latInput.setText(Prefs.lat(this).toString())
        lonInput.setText(Prefs.lon(this).toString())
        radiusInput.setText(Prefs.radius(this).toString())
        intervalInput.setText(Prefs.interval(this).toString())
        val fuels = Prefs.fuels(this)
        findViewById<CheckBox>(R.id.fuel92).isChecked = "92" in fuels
        findViewById<CheckBox>(R.id.fuel95).isChecked = "95" in fuels
        findViewById<CheckBox>(R.id.fuel98).isChecked = "98" in fuels
        findViewById<CheckBox>(R.id.fuel100).isChecked = "100" in fuels
        findViewById<CheckBox>(R.id.fuelDT).isChecked = "ДТ" in fuels
    }

    private fun selectedFuels(): Set<String> {
        val s = HashSet<String>()
        if (findViewById<CheckBox>(R.id.fuel92).isChecked) s.add("92")
        if (findViewById<CheckBox>(R.id.fuel95).isChecked) s.add("95")
        if (findViewById<CheckBox>(R.id.fuel98).isChecked) s.add("98")
        if (findViewById<CheckBox>(R.id.fuel100).isChecked) s.add("100")
        if (findViewById<CheckBox>(R.id.fuelDT).isChecked) s.add("ДТ")
        return s
    }

    private fun ensurePermissionAndStart() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        startMonitoring()
    }

    private fun startMonitoring() {
        val lat = latInput.text.toString().toDoubleOrNull() ?: Prefs.DEFAULT_LAT
        val lon = lonInput.text.toString().toDoubleOrNull() ?: Prefs.DEFAULT_LON
        val radius = radiusInput.text.toString().toDoubleOrNull() ?: Prefs.DEFAULT_RADIUS
        val interval = intervalInput.text.toString().toIntOrNull() ?: 180
        Prefs.save(this, lat, lon, radius, interval, selectedFuels())

        val i = Intent(this, MonitorService::class.java)
            .setAction(MonitorService.ACTION_START)
        ContextCompat.startForegroundService(this, i)
        refreshButtonState()
        statusText.text = "Мониторинг запущен (радиус ${radius} км, интервал ${interval} с)"
    }

    private fun stopMonitoring() {
        val i = Intent(this, MonitorService::class.java)
            .setAction(MonitorService.ACTION_STOP)
        startService(i)
        Prefs.setRunning(this, false)
        refreshButtonState()
        statusText.text = "Мониторинг остановлен"
    }

    private fun refreshButtonState() {
        toggleButton.text = if (Prefs.running(this)) "Остановить" else "Старт"
    }

    override fun onResume() {
        super.onResume()
        refreshButtonState()
    }
}
