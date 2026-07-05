"""Android backend: wraps Java objects Kotlin already opened (android.media.midi ports,
the built-in synth, the virtual MIDI service) into the same MidiOut/ControlSurface/IOContext
shapes the desktop backend produces. Core never needs to know which backend it's talking to.

The Launchpad X wire protocol below (Programmer-mode SysEx, RGB/indexed LED, button decode)
is reproduced byte-for-byte from launchpad_py's LaunchpadPro/LaunchpadLPX classes (the
authoritative source, not memory) so it matches real hardware exactly.

Why every method here writes to / reads from a *list* of ports, not one
-------------------------------------------------------------------------
Launchpad X exposes two MIDI port pairs on the same USB device: a "DAW" port (a fixed,
Ableton-session-style control surface - not freely programmable) and a "MIDI" port (what
Programmer mode / custom RGB lighting actually needs). Desktop hits this exact ambiguity
too - `src/backends/desktop.py`'s `open_launchpads()` works around it with a hardcoded
pygame.midi device index (`lp.Open(1)`, not `lp.Open(0)`).

On Android there's no equivalent trick available: `MidiDeviceInfo.PortInfo.getName()` came
back as an empty string for both ports on a real test device, so there's no metadata to
pick the right one by, and no evidence the numeric port index (0 vs 1) reliably means the
same thing across phones/OS versions either. So instead of guessing, `MainActivity.kt`
opens *every* input and output port the device reports (see `openPortsWithRetry`), and this
module writes every outgoing message to all of them and merges incoming events from all of
them. The port that isn't the real Programmer-mode one just silently ignores our messages -
confirmed on real hardware: no exceptions, no visible effect - so this is safe, just
slightly redundant. If a future Android/Chaquopy update exposes real port names or a
documented way to distinguish DAW vs MIDI ports, this could be narrowed back to one port
each, but "write to all, harmlessly ignored by the wrong one" is the robust choice for now.
"""

import time

from src.io_interfaces import IOContext, RawSurface


def _led_number(x, y):
    """Programmer-mode LED index for grid position x,y (0..8), matching launchpad_py's
    LedCtrlXY/LedCtrlXYByCode coordinate remap (column 9 wraps to column 0, y flipped)."""
    xx = (x + 1) % 10
    return 90 - 10 * y + xx


def _decode_button_event(status, data1, data2, want_pressure):
    """Mirrors launchpad_py's ButtonStateXY exactly: only note-on (0x90), the top-row
    CC buttons (0xB0), and (when requested) poly-aftertouch/pressure (0xA0) are meaningful;
    anything else (e.g. a real 0x80 note-off, which Launchpad X's Programmer mode never
    actually sends) is ignored."""
    if status == 0xA0:
        if not want_pressure:
            return None
        x = (data1 - 1) % 10
        y = (99 - data1) // 10
        return [x + 255, y + 255, data2]
    if status in (0x90, 0xB0):
        x = (data1 - 1) % 10
        y = (99 - data1) // 10
        return [x, y, data2]
    return None


class AndroidMidiOut:
    """Wraps one or more android.media.midi.MidiInputPort objects (ports the app writes TO).

    Launchpad X exposes both a "DAW" port (fixed session-view control surface, not freely
    programmable) and a "MIDI" port (what Programmer mode / custom lighting needs), and
    Android gives us no reliable way to tell them apart (port names came back empty on at
    least one real device). So we write every message to all of them - the port that isn't
    the real Programmer-mode one just silently ignores it (confirmed: no exceptions, no
    visible effect), and whichever one actually is responds correctly."""

    def __init__(self, input_ports):
        self._ports = list(input_ports)

    def send_raw(self, *bytes_):
        data = bytes(bytes_)
        for port in self._ports:
            try:
                port.send(data, 0, len(data))
            except Exception as e:
                # A port can go dead mid-write if the device was just unplugged, so we don't
                # want to raise here - but print so a real bug doesn't masquerade as that.
                print(f"MIDIMECH_ANDROID: send_raw failed for {list(data)}: {e!r}")

    def send_cc(self, channel, cc, value):
        self.send_raw(0xB0 | channel, cc, value)


class AndroidLaunchpadSurface:
    """Satisfies the ControlSurface protocol against a Launchpad X's MIDI ports.

    `receiver` is a Kotlin LaunchpadReceiver: incoming bytes are decoded and queued on the
    Java side (MidiReceiver.onSend fires on a Binder thread), and `receiver.pollEvent()`
    non-blockingly dequeues one [status, data1, data2] triple (or None) - the Java-side
    equivalent of launchpad_py's `ReadCheck()`/`ReadRaw()`.
    """

    mode = "lpx"

    def __init__(self, input_ports, receiver):
        self._out = AndroidMidiOut(input_ports)
        self._receiver = receiver
        print("MIDIMECH_ANDROID: AndroidLaunchpadSurface init, entering Programmer mode")
        # launchpad_py's LaunchpadLPX.Open() enters Programmer mode automatically as part
        # of opening the device; we have no such implicit "Open" step, so do it here -
        # otherwise the pad stays in its default Session-mode look (uniform blue).
        self.LedSetMode(1)
        print("MIDIMECH_ANDROID: Programmer mode SysEx sent, settled")

    def LedSetMode(self, mode):
        if mode < 0 or mode > 1:
            return
        self._out.send_raw(0xF0, 0, 32, 41, 2, 12, 14, mode, 0xF7)
        # launchpad_py sleeps only 10ms here; real USB-MIDI enumeration/mode-switch settling
        # on some phones seems to need noticeably longer, so this is intentionally generous
        # while we confirm timing is really the cause.
        time.sleep(0.15)

    def LedCtrlXY(self, x, y, red, green, blue=None):
        if x < 0 or x > 9 or y < 0 or y > 9:
            return
        if blue is None:
            blue = 0
            red *= 21
            green *= 21
        red = max(0, min(63, red)) << 1
        green = max(0, min(63, green)) << 1
        blue = max(0, min(63, blue)) << 1
        led = _led_number(x, y)
        self._out.send_raw(0xF0, 0, 32, 41, 2, 12, 3, 3, led, red, green, blue, 0xF7)

    def LedCtrlXYByCode(self, x, y, colorcode):
        if x < 0 or x > 9 or y < 0 or y > 9:
            return
        led = _led_number(x, y)
        self._out.send_raw(0x90, led, colorcode)

    def ButtonStateXY(self, returnPressure=False):
        while True:
            item = self._receiver.pollEvent()
            if item is None:
                return []
            decoded = _decode_button_event(item[0], item[1], item[2], returnPressure)
            if decoded is not None:
                return decoded

    def Reset(self):
        for x in range(9):
            for y in range(9):
                self._out.send_raw(0x90, (x + 1) + ((y + 1) * 10), 0)


class AndroidFanOutMidiOut:
    """The Android `midi_out`: forwards every note/CC/pitch-bend Core sends to the
    built-in synth, and (if connected) out through the virtual MIDI service - so an
    external synth app can be driven too. Core only ever knows about one midi_out;
    this fan-out is entirely an Android-backend concern."""

    def __init__(self, synth, virtual_service=None):
        self._synth = synth
        self._virtual = virtual_service

    def send_raw(self, *bytes_):
        data = bytes(bytes_)
        self._synth.onMidi(data)
        if self._virtual is not None:
            self._virtual.sendToConnectedApps(data)

    def send_cc(self, channel, cc, value):
        self.send_raw(0xB0 | channel, cc, value)


def build_io_context(launchpad_input_ports, launchpad_receiver, synth, virtual_service=None):
    """Assembles the same IOContext/RawSurface shapes the desktop backend produces -
    Core.__init__ needs zero changes to consume this. `launchpad_input_ports` is every
    input port the Launchpad exposes (see AndroidMidiOut for why: DAW port + MIDI port
    ambiguity), not just one."""
    surface = AndroidLaunchpadSurface(list(launchpad_input_ports), launchpad_receiver)
    midi_out = AndroidFanOutMidiOut(synth, virtual_service)
    return IOContext(midi_out=midi_out, control_surfaces=[RawSurface(surface, "lpx")])
