#!/usr/bin/python3
import os, sys, traceback

from src.util import error

# suppress pygame messages to keep console clean
with open(os.devnull, "w") as devnull:
    stdout = sys.stdout
    sys.stdout = devnull
    import pygame, pygame.midi, pygame.gfxdraw

    sys.stdout = stdout

from src.settings_loader import load_settings
from src.backends.desktop import open_launchpads, open_midi_ports
from src.core import Core
from src.frontends.pygame_frontend import PygameFrontend


def main():
    core = None
    try:
        options, scale_db = load_settings()

        io = open_midi_ports(options)
        if not io.midi_out:
            error(
                "No MIDI output device detected.  Install a midi loopback device and name it 'midimech'!"
            )
        io.control_surfaces = open_launchpads(options)

        core = Core(options, scale_db, io)

        if io.midi_in:
            io.midi_in.callback = core.cb_midi_in
        if io.visualizer:
            io.visualizer.callback = core.cb_visualizer
        if io.foot_in:
            io.foot_in.callback = core.cb_foot

        PygameFrontend(core).run()
    except SystemExit:
        pass
    except:
        print(traceback.format_exc())
    del core
    pygame.midi.quit()
    pygame.display.quit()
    os._exit(0)


if __name__ == "__main__":
    main()
