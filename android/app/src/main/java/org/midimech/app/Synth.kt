package org.midimech.app

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.net.Uri
import android.os.Build
import kotlin.concurrent.thread

/** Real soundfont synthesis (TinySoundFont via JNI - see TsfEngine.kt) instead of a
 * hand-rolled oscillator, so instruments are switchable General MIDI programs rather than
 * a fixed tone, and the user can load their own .sf2 (see setSoundfontUri). Bundles
 * GeneralUser GS as the default (assets/soundfont.sf2, permissively licensed - see
 * assets/licenses/). Fed by Core's raw MIDI bytes via onMidi(), called from the Python side
 * through the Chaquopy bridge; public API (start/stop/onMidi) is unchanged from the old
 * hand-rolled synth, so src/backends/android.py needed zero changes for this rewrite. */
class Synth(private val context: Context) {
    private val lock = Object()
    private var handle: Long = 0L
    private var sampleRate: Int = 44100

    @Volatile private var running = false
    private var track: AudioTrack? = null
    private var thread: Thread? = null

    private val prefs = context.getSharedPreferences("midimech", Context.MODE_PRIVATE)

    fun start() {
        if (running) return
        running = true

        val am = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
        // Matching the device's actual native output sample rate (and sizing the buffer to
        // a multiple of its reported frames-per-buffer) is what actually gets AudioTrack
        // onto the low-latency output path - PERFORMANCE_MODE_LOW_LATENCY alone silently
        // falls back to the normal (higher-latency) mixer if the format doesn't match.
        sampleRate = am?.getProperty(AudioManager.PROPERTY_OUTPUT_SAMPLE_RATE)?.toIntOrNull() ?: 44100
        val nativeFramesPerBuffer = am?.getProperty(AudioManager.PROPERTY_OUTPUT_FRAMES_PER_BUFFER)?.toIntOrNull() ?: 256

        // AudioTrack.getMinBufferSize() is sized for the *default* (non-low-latency) mixer
        // and can be far larger than the low-latency path actually needs - measured 25 blocks
        // (~100ms!) on a real device here, which was the dominant remaining latency source.
        // Only logged for comparison now, deliberately NOT used to size the real buffer below.
        val minBuf = AudioTrack.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_FLOAT
        )
        val framesPerBlock = nativeFramesPerBuffer.coerceAtLeast(64)
        val bytesPerFrame = 4 // 32-bit float mono
        val blockBytes = framesPerBlock * bytesPerFrame
        // Triple-buffered at the native block size - deliberately not derived from minBuf (see
        // above). 2 (double-buffered, the Oboe/AAudio default) crackled on a real test device,
        // so this trades a little extra latency (~4ms more at typical 192-frame/48kHz blocks)
        // for headroom against scheduling jitter. Drop back to 2 if a device handles it fine.
        val blocksNeeded = 3
        val bufSize = blocksNeeded * blockBytes

        val builder = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(bufSize)
            .setTransferMode(AudioTrack.MODE_STREAM)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder.setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
        }
        track = builder.build()

        synchronized(lock) {
            handle = TsfEngine.nativeLoad(loadSoundfontBytes(), sampleRate)
            if (handle == 0L) {
                android.util.Log.e("MIDIMECH", "TsfEngine.nativeLoad failed - no soundfont loaded")
            }
        }

        track?.play()

        // Whether PERFORMANCE_MODE_LOW_LATENCY was actually granted (vs silently falling back
        // to the normal, higher-latency mixer) determines how much more latency headroom is
        // realistically left to chase - log it so this is diagnosable without guessing.
        val actualPerfMode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) track?.performanceMode else null
        android.util.Log.i(
            "MIDIMECH",
            "audio: sampleRate=$sampleRate nativeFramesPerBuffer=$nativeFramesPerBuffer " +
                "framesPerBlock=$framesPerBlock minBufBytes=$minBuf (legacy-mixer estimate, not used) " +
                "blocksNeeded=$blocksNeeded requestedBufBytes=$bufSize " +
                "actualBufferSizeInFrames=${track?.bufferSizeInFrames} " +
                // real AudioTrack constant values (verified against the SDK): NONE=0, LOW_LATENCY=1, POWER_SAVING=2
                "actualPerformanceMode=$actualPerfMode (1=LOW_LATENCY, 0=NONE, null=API<26)"
        )

        thread = thread(start = true, isDaemon = true, name = "midimech-synth") { renderLoop(framesPerBlock) }
    }

    fun stop() {
        running = false
        thread?.join(500)
        track?.stop()
        track?.release()
        track = null
        synchronized(lock) {
            if (handle != 0L) {
                TsfEngine.nativeClose(handle)
                handle = 0L
            }
        }
    }

    /** The currently-persisted custom soundfont, if any (null means the bundled default) -
     * so the UI can show the right label on a fresh launch, not just after a picker call. */
    fun customSoundfontUri(): Uri? = prefs.getString(KEY_SOUNDFONT_URI, null)?.let { Uri.parse(it) }

    /** Points the engine at a user-picked .sf2 (persisted across restarts), or back at the
     * bundled default if `uri` is null. Safe to call while playing. */
    fun setSoundfontUri(uri: Uri?) {
        synchronized(lock) {
            prefs.edit().apply {
                if (uri != null) putString(KEY_SOUNDFONT_URI, uri.toString()) else remove(KEY_SOUNDFONT_URI)
            }.apply()

            val newHandle = TsfEngine.nativeLoad(loadSoundfontBytes(), sampleRate)
            if (newHandle == 0L) {
                android.util.Log.e("MIDIMECH", "failed to load soundfont from $uri, keeping previous one")
                return
            }
            if (handle != 0L) {
                TsfEngine.nativeClose(handle)
            }
            handle = newHandle
        }
    }

    private fun loadSoundfontBytes(): ByteArray {
        val customUri = prefs.getString(KEY_SOUNDFONT_URI, null)
        if (customUri != null) {
            try {
                context.contentResolver.openInputStream(Uri.parse(customUri))?.use { return it.readBytes() }
            } catch (e: Exception) {
                android.util.Log.e("MIDIMECH", "failed to read custom soundfont, falling back to default", e)
            }
        }
        return context.assets.open("soundfont.sf2").use { it.readBytes() }
    }

    private fun renderLoop(frames: Int) {
        val buffer = FloatArray(frames)
        while (running) {
            synchronized(lock) {
                if (handle != 0L) {
                    TsfEngine.nativeRenderFloat(handle, buffer, frames)
                } else {
                    buffer.fill(0f) // no soundfont loaded (yet, or load failed) - stay silent, not garbage/stuck audio
                }
            }
            track?.write(buffer, 0, frames, AudioTrack.WRITE_BLOCKING)
        }
    }

    /** Entry point called from Python (src/backends/android.py's AndroidFanOutMidiOut).
     * Generic raw-MIDI-byte dispatch, so unlike the old hand-rolled synth this also handles
     * program change (instrument switching), CC, and pitch bend, not just note on/off. */
    fun onMidi(data: ByteArray) {
        if (data.isEmpty()) return
        val status = data[0].toInt() and 0xFF
        val channel = status and 0x0F
        synchronized(lock) {
            if (handle == 0L) return@synchronized
            when (status and 0xF0) {
                0x90 -> {
                    val key = data[1].toInt() and 0x7F
                    val vel = data[2].toInt() and 0x7F
                    if (vel == 0) TsfEngine.nativeNoteOff(handle, channel, key)
                    else TsfEngine.nativeNoteOn(handle, channel, key, vel / 127f)
                }
                0x80 -> TsfEngine.nativeNoteOff(handle, channel, data[1].toInt() and 0x7F)
                0xB0 -> TsfEngine.nativeControlChange(
                    handle, channel, data[1].toInt() and 0x7F, data[2].toInt() and 0x7F
                )
                0xC0 -> TsfEngine.nativeProgramChange(handle, channel, data[1].toInt() and 0x7F)
                0xE0 -> {
                    val value14 = (data[1].toInt() and 0x7F) or ((data[2].toInt() and 0x7F) shl 7)
                    TsfEngine.nativePitchBend(handle, channel, value14)
                }
            }
        }
    }

    companion object {
        private const val KEY_SOUNDFONT_URI = "custom_soundfont_uri"
    }
}
