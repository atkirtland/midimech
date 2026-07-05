package org.midimech.app

import android.media.midi.MidiDeviceService
import android.media.midi.MidiReceiver

/** Exposes a virtual MIDI output port (declared in res/xml/midi_device_info.xml) so an
 * external synth app or DAW can connect and receive midimech's notes, in addition to the
 * built-in Synth. We declare no input ports, so onGetInputPortReceivers() is empty. */
class MidimechVirtualMidiService : MidiDeviceService() {
    override fun onGetInputPortReceivers(): Array<MidiReceiver> = emptyArray()

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

        /** Called from Python (src/backends/android.py) whenever Core writes a MIDI message.
         * A no-op if no app has connected to our virtual port yet. */
        fun sendToConnectedApps(data: ByteArray) {
            instance?.send(data)
        }
    }
}
