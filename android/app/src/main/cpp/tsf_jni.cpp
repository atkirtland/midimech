// Thin JNI bridge to TinySoundFont (tsf.h, vendored unmodified, MIT-licensed:
// https://github.com/schellingb/TinySoundFont). All real synthesis work happens in tsf.h;
// this file just exposes the handful of tsf_* calls TsfEngine.kt needs, keyed by an opaque
// jlong handle (a tsf* cast to jlong) since JNI has no notion of a C struct pointer.
//
// Thread safety: tsf.h's own docs say note events and rendering can run on different
// threads only if voices/channels are pre-allocated (see nativeLoad's warm-up loop below);
// getting that exactly right without real-hardware testing is risky, so Synth.kt instead
// wraps every native call (note events AND rendering) in the same lock - simpler and safe,
// at a negligible cost since note events are rare compared to the audio render loop.

#include <jni.h>

#define TSF_IMPLEMENTATION
#define TSF_NO_STDIO
#include "tsf.h"

extern "C" {

JNIEXPORT jlong JNICALL
Java_org_midimech_app_TsfEngine_nativeLoad(JNIEnv *env, jobject, jbyteArray sfData, jint sampleRate) {
    jsize len = env->GetArrayLength(sfData);
    jbyte *bytes = env->GetByteArrayElements(sfData, nullptr);
    tsf *f = tsf_load_memory(bytes, len);
    env->ReleaseByteArrayElements(sfData, bytes, JNI_ABORT);
    if (!f) {
        return 0;
    }

    tsf_set_output(f, TSF_MONO, sampleRate, 0.0f);
    tsf_set_max_voices(f, 128);

    // tsf_channel_note_on "needs channel preset to be set" first, and our note events can
    // arrive on any of the 16 MIDI channels (Core's MPE polyphony assigns one per voice
    // slot), so warm all of them up to program 0 (Acoustic Grand Piano) now rather than
    // risk a channel-allocation happening for the first time on the audio thread.
    for (int channel = 0; channel < 16; channel++) {
        tsf_channel_set_presetnumber(f, channel, 0, 0);
    }

    return reinterpret_cast<jlong>(f);
}

JNIEXPORT void JNICALL
Java_org_midimech_app_TsfEngine_nativeClose(JNIEnv *, jobject, jlong handle) {
    if (handle) {
        tsf_close(reinterpret_cast<tsf *>(handle));
    }
}

JNIEXPORT void JNICALL
Java_org_midimech_app_TsfEngine_nativeNoteOn(JNIEnv *, jobject, jlong handle, jint channel, jint key, jfloat vel) {
    if (handle) {
        tsf_channel_note_on(reinterpret_cast<tsf *>(handle), channel, key, vel);
    }
}

JNIEXPORT void JNICALL
Java_org_midimech_app_TsfEngine_nativeNoteOff(JNIEnv *, jobject, jlong handle, jint channel, jint key) {
    if (handle) {
        tsf_channel_note_off(reinterpret_cast<tsf *>(handle), channel, key);
    }
}

JNIEXPORT void JNICALL
Java_org_midimech_app_TsfEngine_nativeProgramChange(JNIEnv *, jobject, jlong handle, jint channel, jint program) {
    if (handle) {
        tsf_channel_set_presetnumber(reinterpret_cast<tsf *>(handle), channel, program, 0);
    }
}

JNIEXPORT void JNICALL
Java_org_midimech_app_TsfEngine_nativeControlChange(JNIEnv *, jobject, jlong handle, jint channel, jint controller, jint value) {
    if (handle) {
        tsf_channel_midi_control(reinterpret_cast<tsf *>(handle), channel, controller, value);
    }
}

JNIEXPORT void JNICALL
Java_org_midimech_app_TsfEngine_nativePitchBend(JNIEnv *, jobject, jlong handle, jint channel, jint value14) {
    if (handle) {
        tsf_channel_set_pitchwheel(reinterpret_cast<tsf *>(handle), channel, value14);
    }
}

JNIEXPORT void JNICALL
Java_org_midimech_app_TsfEngine_nativeRenderFloat(JNIEnv *env, jobject, jlong handle, jfloatArray buffer, jint numSamples) {
    if (!handle) {
        return;
    }
    jfloat *buf = env->GetFloatArrayElements(buffer, nullptr);
    tsf_render_float(reinterpret_cast<tsf *>(handle), buf, numSamples, 0);
    env->ReleaseFloatArrayElements(buffer, buf, 0);
}

} // extern "C"
