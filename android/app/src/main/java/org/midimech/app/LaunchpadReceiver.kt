package org.midimech.app

import android.media.midi.MidiReceiver
import java.util.concurrent.ConcurrentLinkedQueue

/** Receives raw MIDI bytes from the Launchpad's output port (MidiReceiver.onSend is
 * push/callback-based, unlike launchpad_py's poll-based ButtonStateXY), and queues
 * [status, data1, data2] triples for Python to drain non-blockingly via pollEvent() -
 * reproducing launchpad_py's poll-loop contract exactly (see src/backends/android.py).
 * Message-boundary parsing is shared with MidimechVirtualMidiService's visualizer input via
 * MidiByteStreamParser.
 *
 * `onEvent` fires as soon as a real button/pressure event is queued, letting the caller
 * process it immediately instead of waiting for the next scheduled tick - the periodic tick
 * loop alone adds up to ~16ms (60Hz) of avoidable input latency on top of everything else. */
class LaunchpadReceiver(private val onEvent: (() -> Unit)? = null) : MidiReceiver() {
    private val queue = ConcurrentLinkedQueue<IntArray>()
    private var queuedThisCall = false

    private val parser = MidiByteStreamParser { status, d1, d2 ->
        val kind = status and 0xF0
        if (kind == 0x90 || kind == 0xA0 || kind == 0xB0) {
            queue.add(intArrayOf(kind, d1, d2))
            queuedThisCall = true
        }
    }

    override fun onSend(msg: ByteArray, offset: Int, count: Int, timestamp: Long) {
        queuedThisCall = false
        parser.feed(msg, offset, count)
        if (queuedThisCall) onEvent?.invoke()
    }

    /** Called from Python: non-blocking dequeue, matching launchpad_py's ReadCheck()+ReadRaw(). */
    fun pollEvent(): IntArray? = queue.poll()
}
