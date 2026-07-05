package org.midimech.app

import android.media.midi.MidiDeviceService
import android.media.midi.MidiReceiver

/** Exposes two virtual MIDI ports (declared in res/xml/midi_device_info.xml):
 *  - an OUTPUT port so an external synth app or DAW can connect and receive midimech's notes,
 *    in addition to the built-in Synth.
 *  - an INPUT port ("visualizer") so an external "notes to play" source - e.g. a web-based
 *    Synthesia-style player via Chrome's Web MIDI API, or a MIDI file player/DAW that can pick
 *    a MIDI output device - can feed Core.cb_visualizer(), mirroring desktop's "visualizer"
 *    loopback MIDI-in port. (Neothesia itself has no Android build; this just gives any
 *    Android-side MIDI source a port to connect to.)
 *
 * Android instantiates this service lazily when something binds to it (a virtual MIDI device
 * doesn't "exist" as a live object until then), so both directions are bridged through this
 * companion object rather than talking to MainActivity's `core` directly. */
class MidimechVirtualMidiService : MidiDeviceService() {
    private val visualizerParser = MidiByteStreamParser { status, d1, d2 ->
        val kind = status and 0xF0
        if (kind == 0x90 || kind == 0x80) {
            visualizerCallback?.invoke(intArrayOf(status, d1, d2))
        }
    }
    private val visualizerReceiver = object : MidiReceiver() {
        override fun onSend(msg: ByteArray, offset: Int, count: Int, timestamp: Long) {
            visualizerParser.feed(msg, offset, count)
        }
    }

    override fun onGetInputPortReceivers(): Array<MidiReceiver> = arrayOf(visualizerReceiver)

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    private fun send(data: ByteArray) {
        val receivers = outputPortReceivers
        if (receivers.isNotEmpty()) {
            receivers[0].send(data, 0, data.size)
        }
    }

    companion object {
        @Volatile private var instance: MidimechVirtualMidiService? = null
        @Volatile private var visualizerCallback: ((IntArray) -> Unit)? = null

        /** Called from Python (src/backends/android.py) whenever Core writes a MIDI message.
         * A no-op if no app has connected to our virtual port yet. */
        fun sendToConnectedApps(data: ByteArray) {
            instance?.send(data)
        }

        /** MainActivity registers this once Core exists (and clears it on teardown) to route
         * incoming "notes to play" messages to Core.cb_visualizer(). */
        fun setVisualizerCallback(callback: ((IntArray) -> Unit)?) {
            visualizerCallback = callback
        }
    }
}
