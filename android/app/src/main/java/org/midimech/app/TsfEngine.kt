package org.midimech.app

/** Thin JNI bridge to TinySoundFont (app/src/main/cpp/tsf_jni.cpp / tsf.h, MIT-licensed:
 * https://github.com/schellingb/TinySoundFont). Every function takes the jlong handle
 * nativeLoad() returned (0 means "not loaded" - callers must check before rendering).
 * Not thread-safe by itself; Synth.kt wraps every call, including rendering, in one lock -
 * see the note in tsf_jni.cpp for why. */
object TsfEngine {
    init {
        System.loadLibrary("tsf_jni")
    }

    external fun nativeLoad(soundfontBytes: ByteArray, sampleRate: Int): Long
    external fun nativeClose(handle: Long)
    external fun nativeNoteOn(handle: Long, channel: Int, key: Int, vel: Float)
    external fun nativeNoteOff(handle: Long, channel: Int, key: Int)
    external fun nativeProgramChange(handle: Long, channel: Int, program: Int)
    external fun nativeControlChange(handle: Long, channel: Int, controller: Int, value: Int)
    external fun nativePitchBend(handle: Long, channel: Int, value14: Int)
    external fun nativeRenderFloat(handle: Long, buffer: FloatArray, numSamples: Int)
}
