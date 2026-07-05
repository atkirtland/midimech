package org.midimech.app

import android.media.midi.MidiReceiver
import java.util.concurrent.ConcurrentLinkedQueue

/** Receives raw MIDI bytes from the Launchpad's output port (MidiReceiver.onSend is
 * push/callback-based, unlike launchpad_py's poll-based ButtonStateXY), parses them into
 * discrete messages, and queues [status, data1, data2] triples for Python to drain
 * non-blockingly via pollEvent() - reproducing launchpad_py's poll-loop contract exactly
 * (see src/backends/android.py). There's no public android.media.midi.MidiFramer class in
 * the SDK, so message-boundary parsing (including skipping SysEx acks) is done here.
 *
 * `onEvent` fires as soon as a real button/pressure event is queued, letting the caller
 * process it immediately instead of waiting for the next scheduled tick - the periodic tick
 * loop alone adds up to ~16ms (60Hz) of avoidable input latency on top of everything else. */
class LaunchpadReceiver(private val onEvent: (() -> Unit)? = null) : MidiReceiver() {
    private val queue = ConcurrentLinkedQueue<IntArray>()
    private var pending = ByteArray(0)

    override fun onSend(msg: ByteArray, offset: Int, count: Int, timestamp: Long) {
        val buf = if (pending.isEmpty()) {
            msg.copyOfRange(offset, offset + count)
        } else {
            pending + msg.copyOfRange(offset, offset + count)
        }
        var queuedAny = false
        var i = 0
        while (i < buf.size) {
            val status = buf[i].toInt() and 0xFF
            if (status < 0x80) {
                i++
                continue
            }

            if (status == 0xF0) {
                var end = i + 1
                while (end < buf.size && (buf[end].toInt() and 0xFF) != 0xF7) end++
                if (end >= buf.size) {
                    pending = buf.copyOfRange(i, buf.size)
                    if (queuedAny) onEvent?.invoke()
                    return
                }
                i = end + 1
                continue
            }

            val dataLen = when {
                status in 0x80..0xBF || status in 0xE0..0xEF -> 2
                status in 0xC0..0xDF -> 1
                status == 0xF1 || status == 0xF3 -> 1
                status == 0xF2 -> 2
                else -> 0
            }

            if (i + 1 + dataLen > buf.size) {
                pending = buf.copyOfRange(i, buf.size)
                if (queuedAny) onEvent?.invoke()
                return
            }

            val d1 = if (dataLen >= 1) (buf[i + 1].toInt() and 0x7F) else 0
            val d2 = if (dataLen >= 2) (buf[i + 2].toInt() and 0x7F) else 0
            val kind = status and 0xF0
            if (kind == 0x90 || kind == 0xA0 || kind == 0xB0) {
                queue.add(intArrayOf(kind, d1, d2))
                queuedAny = true
            }
            i += 1 + dataLen
        }
        pending = ByteArray(0)
        if (queuedAny) onEvent?.invoke()
    }

    /** Called from Python: non-blocking dequeue, matching launchpad_py's ReadCheck()+ReadRaw(). */
    fun pollEvent(): IntArray? = queue.poll()
}
