package org.midimech.app

/** Reassembles a raw MIDI byte stream (android.media.midi.MidiReceiver.onSend delivers
 * arbitrarily-sized chunks, not necessarily aligned to message boundaries) into discrete
 * (status, data1, data2) messages, skipping SysEx. There's no public
 * android.media.midi.MidiFramer class in the SDK, so this is hand-rolled - shared by
 * LaunchpadReceiver and MidimechVirtualMidiService's visualizer input, since both need the
 * exact same message-boundary handling and a bug fixed in one copy but not the other would
 * be a maintenance hazard. One instance per MIDI source: `pending` carries a message that
 * straddles two onSend() calls, so it must persist across calls for the same stream. */
class MidiByteStreamParser(private val onMessage: (status: Int, data1: Int, data2: Int) -> Unit) {
    private var pending = ByteArray(0)

    fun feed(msg: ByteArray, offset: Int, count: Int) {
        val buf = if (pending.isEmpty()) {
            msg.copyOfRange(offset, offset + count)
        } else {
            pending + msg.copyOfRange(offset, offset + count)
        }
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
                return
            }

            val d1 = if (dataLen >= 1) (buf[i + 1].toInt() and 0x7F) else 0
            val d2 = if (dataLen >= 2) (buf[i + 2].toInt() and 0x7F) else 0
            onMessage(status, d1, d2)
            i += 1 + dataLen
        }
        pending = ByteArray(0)
    }
}
