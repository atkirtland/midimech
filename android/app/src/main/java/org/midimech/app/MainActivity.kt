package org.midimech.app

import android.content.Intent
import android.media.midi.MidiDevice
import android.media.midi.MidiDeviceInfo
import android.media.midi.MidiInputPort
import android.media.midi.MidiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class MainActivity : AppCompatActivity() {
    private lateinit var statusText: TextView
    private lateinit var soundfontText: TextView
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var synth: Synth
    private var midiManager: MidiManager? = null

    private var core: PyObject? = null
    private var launchpadDevice: MidiDevice? = null
    private var connectedDeviceId: Int? = null
    private var tickRunnable: Runnable? = null

    // Must be registered before onCreate's super.onCreate() returns (Activity Result API
    // requirement), so this runs as a property initializer, not inside onCreate.
    private val soundfontPicker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@registerForActivityResult
        contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        synth.setSoundfontUri(uri)
        soundfontText.text = "Soundfont: ${uri.lastPathSegment ?: uri}"
        Toast.makeText(this, "Soundfont loaded", Toast.LENGTH_SHORT).show()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        statusText = findViewById(R.id.statusText)
        soundfontText = findViewById(R.id.soundfontText)
        wireControlButtons()
        wireSoundfontButtons()

        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        synth = Synth(this)
        synth.start()
        soundfontText.text = "Soundfont: ${synth.customSoundfontUri()?.lastPathSegment ?: "default"}"
        statusText.text = "Waiting for Launchpad..."

        val mm = getSystemService(MIDI_SERVICE) as? MidiManager
        if (mm == null) {
            statusText.text = "No MIDI support on this device"
            return
        }
        midiManager = mm

        mm.registerDeviceCallback(object : MidiManager.DeviceCallback() {
            override fun onDeviceAdded(device: MidiDeviceInfo) {
                tryOpenIfLaunchpad(mm, device)
            }

            override fun onDeviceRemoved(device: MidiDeviceInfo) {
                if (device.id == connectedDeviceId) {
                    teardownLaunchpad()
                }
            }
        }, handler)

        for (device in mm.devices) {
            tryOpenIfLaunchpad(mm, device)
        }
    }

    private fun wireControlButtons() {
        findViewById<Button>(R.id.btnOctaveDown).setOnClickListener { shiftOctave(-1) }
        findViewById<Button>(R.id.btnOctaveUp).setOnClickListener { shiftOctave(1) }
        findViewById<Button>(R.id.btnTransposeDown).setOnClickListener { shiftTonic(-1) }
        findViewById<Button>(R.id.btnTransposeUp).setOnClickListener { shiftTonic(1) }
        findViewById<Button>(R.id.btnScaleDown).setOnClickListener { core?.callAttr("prev_scale") }
        findViewById<Button>(R.id.btnScaleUp).setOnClickListener { core?.callAttr("next_scale") }
        findViewById<Button>(R.id.btnModeDown).setOnClickListener { core?.callAttr("prev_mode") }
        findViewById<Button>(R.id.btnModeUp).setOnClickListener { core?.callAttr("next_mode") }
    }

    private fun wireSoundfontButtons() {
        findViewById<Button>(R.id.btnLoadSoundfont).setOnClickListener {
            soundfontPicker.launch(arrayOf("*/*")) // .sf2 has no registered MIME type
        }
        findViewById<Button>(R.id.btnDefaultSoundfont).setOnClickListener {
            synth.setSoundfontUri(null)
            soundfontText.text = "Soundfont: default"
            Toast.makeText(this, "Default soundfont restored", Toast.LENGTH_SHORT).show()
        }
    }

    /** Mirrors src/frontends/pygame_frontend.py's OCT+/OCT- button handling exactly. */
    private fun shiftOctave(delta: Int) {
        core?.let {
            it.put("octave", it.get("octave")!!.toInt() + delta)
            it.callAttr("clear_marks", false)
        }
    }

    /** Mirrors pygame_frontend.py's TR+/TR- handling: set_tonic(tonic +/- 1). */
    private fun shiftTonic(delta: Int) {
        core?.let {
            it.callAttr("set_tonic", it.get("tonic")!!.toInt() + delta)
        }
    }

    private fun tryOpenIfLaunchpad(midiManager: MidiManager, info: MidiDeviceInfo) {
        if (launchpadDevice != null) return
        val props = info.properties
        val name = props.getString(MidiDeviceInfo.PROPERTY_NAME) ?: ""
        val product = props.getString(MidiDeviceInfo.PROPERTY_PRODUCT) ?: ""
        if (!name.contains("Launchpad", ignoreCase = true) &&
            !product.contains("Launchpad", ignoreCase = true)
        ) {
            return
        }

        connectedDeviceId = info.id
        midiManager.openDevice(info, { device ->
            if (device == null) {
                runOnUiThread { statusText.text = "Failed to open Launchpad" }
                connectedDeviceId = null
                return@openDevice
            }
            launchpadDevice = device
            openPortsWithRetry(device)
        }, handler)
    }

    /** Returns every port number of the given type, logging each one's name for diagnosis.
     * We open ALL of them (see openPortsWithRetry) rather than guessing a single "the right
     * one" index - full explanation of why (Launchpad X's DAW-port-vs-MIDI-port ambiguity,
     * and why Android gives us no way to tell them apart) is in the module docstring at the
     * top of src/backends/android.py - keep that comment as the one source of truth rather
     * than re-explaining it here too. */
    private fun allPortNumbers(info: MidiDeviceInfo, type: Int): List<Int> {
        val ports = info.ports.filter { it.type == type }
        for (p in ports) {
            android.util.Log.i(
                "MIDIMECH",
                "port #${p.portNumber} type=${if (type == MidiDeviceInfo.PortInfo.TYPE_INPUT) "IN" else "OUT"} name='${p.name}'"
            )
        }
        return ports.map { it.portNumber }
    }

    /** MIDI port enumeration can lag briefly right after a device is opened (more so on a
     * fresh hot-plug), so getting no ports on the first attempt isn't necessarily fatal -
     * retry a few times before giving up. */
    private fun openPortsWithRetry(device: MidiDevice, attempt: Int = 0) {
        if (launchpadDevice !== device) return // superseded by a teardown/reconnect

        val info = device.info
        val inputPorts = allPortNumbers(info, MidiDeviceInfo.PortInfo.TYPE_INPUT)
            .mapNotNull { device.openInputPort(it) }
        val outputPorts = allPortNumbers(info, MidiDeviceInfo.PortInfo.TYPE_OUTPUT)
            .mapNotNull { device.openOutputPort(it) }

        if (inputPorts.isNotEmpty() && outputPorts.isNotEmpty()) {
            // React the moment a button event arrives instead of waiting for the next
            // scheduled tick (up to ~16ms away at 60Hz) - onSend fires on a Binder thread,
            // so hop back onto the main thread before touching `core`, same as everywhere else.
            val receiver = LaunchpadReceiver(onEvent = {
                handler.post { core?.callAttr("poll_launchpads") }
            })
            for (out in outputPorts) {
                out.connect(receiver)
            }
            startCore(inputPorts, receiver)
            return
        }
        inputPorts.forEach { it.close() }
        outputPorts.forEach { it.close() }

        if (attempt >= 5) {
            runOnUiThread { statusText.text = "Launchpad has no usable ports" }
            return
        }
        handler.postDelayed({ openPortsWithRetry(device, attempt + 1) }, 200)
    }

    private fun startCore(inputPorts: List<MidiInputPort>, receiver: LaunchpadReceiver) {
        val py = Python.getInstance()
        val androidBackend = py.getModule("src.backends.android")
        val io = androidBackend.callAttr(
            "build_io_context", inputPorts.toTypedArray(), receiver, synth, MidimechVirtualMidiService.Companion
        )

        val settingsLoader = py.getModule("src.settings_loader")
        val settingsAndScales = settingsLoader.callAttr("load_settings").asList()
        val options = settingsAndScales[0]
        val scaleDb = settingsAndScales[1]

        val coreModule = py.getModule("src.core")
        core = coreModule.callAttr("Core", options, scaleDb, io)

        runOnUiThread { statusText.text = "Launchpad connected" }
        startTickLoop()
    }

    private fun startTickLoop() {
        val intervalMs = 1000L / 60
        val runnable = object : Runnable {
            override fun run() {
                try {
                    core?.let {
                        it.callAttr("poll_launchpads")
                        it.callAttr("logic", intervalMs / 1000.0)
                    }
                } catch (e: Exception) {
                    // A mid-flight disconnect can throw here before onDeviceRemoved fires;
                    // that callback (or onDestroy) does the real cleanup, so we still just skip
                    // this tick rather than crash - but log it, so a real bug (like the
                    // out-of-MIDI-range note crash this once silently swallowed) is visible
                    // in Logcat instead of just disappearing as "no sound, no error".
                    android.util.Log.e("MIDIMECH", "tick failed", e)
                }
                handler.postDelayed(this, intervalMs)
            }
        }
        tickRunnable = runnable
        handler.post(runnable)
    }

    /** Called when Android reports the connected Launchpad was unplugged, and on activity
     * destroy - releases the MIDI device/ports promptly so the OS doesn't consider them a
     * dangling active claim, and resets state so a replug can cleanly reconnect. */
    private fun teardownLaunchpad() {
        tickRunnable?.let { handler.removeCallbacks(it) }
        tickRunnable = null
        try {
            core?.callAttr("deinit")
        } catch (e: Exception) {
        }
        core = null
        try {
            launchpadDevice?.close()
        } catch (e: Exception) {
        }
        launchpadDevice = null
        connectedDeviceId = null
        runOnUiThread { statusText.text = "Waiting for Launchpad..." }
    }

    override fun onDestroy() {
        teardownLaunchpad()
        synth.stop()
        super.onDestroy()
    }
}
