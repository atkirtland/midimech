import rtmidi2

try:
    import launchpad_py as launchpad
except ImportError:
    try:
        import launchpad
    except ImportError:
        from src.util import error
        error("The project dependencies have changed! Run the requirements setup command again!")

from src.io_interfaces import IOContext, RawSurface


def open_midi_ports(options) -> IOContext:
    """Scan rtmidi2 ports by name and open the ones midimech cares about."""
    io = IOContext()

    outnames = rtmidi2.get_out_ports()
    for i in range(len(outnames)):
        name = outnames[i]
        name_lower = name.lower()
        if "linnstrument" in name_lower:
            print("Instrument (Out): " + name)
            io.linn_out = rtmidi2.MidiOut()
            try:
                io.linn_out.open_port(i)
            except:
                print("Unable to open LinnStrument")
        elif options.split_out and options.split_out in name_lower:
            print("Split (Out): " + name)
            io.split_out = rtmidi2.MidiOut()
            io.split_out.open_port(i)
        elif options.midi_out in name_lower:
            print("Loopback (Out): " + name)
            io.midi_out = rtmidi2.MidiOut()
            io.midi_out.open_port(i)

    innames = rtmidi2.get_in_ports()
    for i in range(len(innames)):
        name = innames[i]
        name_lower = name.lower()
        if "visualizer" in name_lower:
            print("Visualizer (In): " + name)
            io.visualizer = rtmidi2.MidiIn()
            io.visualizer.open_port(i)
        elif "linnstrument" in name_lower:
            print("Instrument (In): " + name)
            io.midi_in = rtmidi2.MidiIn()
            io.midi_in.open_port(i)
        elif options.foot_in and options.foot_in in name_lower:
            print("Foot Controller (In): " + name)
            io.foot_in = rtmidi2.MidiIn()
            io.foot_in.open_port(i)

    return io


def open_launchpads(options):
    """Probe for Launchpads via launchpad_py. Returns raw RawSurface entries -
    Core wraps these in src.launchpad.Launchpad once it exists (that wrapper needs `core`)."""
    surfaces = []
    if not options.launchpad:
        return surfaces

    lp = launchpad.LaunchpadProMk3()
    if lp.Check(0):
        if lp.Open(0):
            surfaces.append(RawSurface(lp, "promk3"))
    lp = launchpad.LaunchpadPro()
    if lp.Check(0):
        if lp.Open(0):
            surfaces.append(RawSurface(lp, "pro"))
    lp = launchpad.LaunchpadLPX()
    if lp.Check(1):
        lp = launchpad.LaunchpadLPX()
        if lp.Open(1):
            surfaces.append(RawSurface(lp, "lpx"))
        if launchpad.LaunchpadLPX().Check(3):
            lp = launchpad.LaunchpadLPX()
            if lp.Open(3):  # second
                surfaces.append(RawSurface(lp, "lpx", options.octave_separation))

    if surfaces:
        print('Launchpads:', len(surfaces))

    return surfaces
