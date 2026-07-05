import importlib.resources
import sys
from configparser import ConfigParser

from src import vecshim as glm

from src.settings import Settings, DEFAULT_OPTIONS
from src.util import error, get_color, get_option

try:
    import yaml
except ImportError:
    error("The project dependencies have changed! Run the requirements setup command again!")


def load_scale_db():
    """Load scales.yaml, bundled as package data under src/ so it's reachable the same way
    whether src/ is a real directory (desktop) or packaged assets (Chaquopy/Android)."""
    scales_file = importlib.resources.files("src").joinpath("scales.yaml")
    with scales_file.open("r") as stream:
        try:
            scale_db = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            error('Cannot load scales.yaml')
    return scale_db


def load_settings():
    """Parse settings.ini (and scales.yaml) into a Settings instance + scale_db. Returns (options, scale_db)."""
    cfg = ConfigParser(allow_no_value=True)
    cfg.read("settings.ini")
    try:
        opts = cfg["general"]
    except KeyError:
        opts = None

    scale_db = load_scale_db()

    options = Settings()

    options.column_offset = get_option(opts, "column_offset", DEFAULT_OPTIONS.column_offset)
    options.row_offset = get_option(opts, "row_offset", DEFAULT_OPTIONS.row_offset)
    options.base_offset = get_option(opts, "base_offset", DEFAULT_OPTIONS.base_offset)

    options.colors = get_option(opts, "colors", DEFAULT_OPTIONS.colors)
    options.colors = list(options.colors.split(","))
    options.colors = list(map(lambda x: glm.ivec3(get_color(x)), options.colors))

    options.launchpad_colors = get_option(opts, "launchpad_colors", DEFAULT_OPTIONS.launchpad_colors)
    if options.launchpad_colors:
        options.launchpad_colors = list(options.launchpad_colors.split(","))
        options.launchpad_colors = list(int(x) for x in options.launchpad_colors)

    options.split_colors = get_option(opts, "split_colors", DEFAULT_OPTIONS.split_colors)
    options.split_colors = list(options.split_colors.split(","))
    options.split_colors = list(map(lambda x: glm.ivec3(get_color(x)), options.split_colors))

    options.lights = get_option(opts, "lights", DEFAULT_OPTIONS.lights)
    if options.lights:
        options.lights = list(
            map(lambda x: int(x), options.lights.split(","))
        )
    options.lights = list(map(lambda x: 3 if x == 7 else x, options.lights))

    options.split_lights = get_option(
        opts, "split_lights", DEFAULT_OPTIONS.split_lights
    )
    if options.split_lights:
        options.split_lights = list(
            map(lambda x: int(x), options.split_lights.split(","))
        )
    options.split_lights = list(map(lambda x: 5 if x == 7 else x, options.split_lights))

    if len(options.colors) != 12:
        error("Invalid color configuration. Make sure you have 12 colors under the colors option or remove it.")
    if len(options.split_colors) != 12:
        error("Invalid split color configuration. Make sure you have 12 colors under the split_colors option or remove it.")
    if len(options.lights) != 12:
        error("Invalid light color configuration. Make sure you have 12 light colors under the lights option or remove it.")
    if len(options.split_lights) != 12:
        error("Invalid light color configuration for split. Make sure you have 12 light colors under the split_lights option or remove it.")

    options.one_channel = get_option(
        opts, "one_channel", DEFAULT_OPTIONS.one_channel
    )
    options.bend_range = get_option(
        opts, "bend_range", DEFAULT_OPTIONS.bend_range
    )

    if "--lite" in sys.argv:
        options.lite = True
    else:
        options.lite = get_option(
            opts, "lite", DEFAULT_OPTIONS.lite
        )

    # bend the velocity curve, examples: 0.5=sqrt, 1.0=default, 2.0=squared
    options.velocity_curve = get_option(
        opts, "velocity_curve", DEFAULT_OPTIONS.velocity_curve
    )

    # these settings are only used with the foot controller
    options.velocity_curve_low = get_option(
        opts, "velocity_curve_low", DEFAULT_OPTIONS.velocity_curve_low
    )  # loudest (!)
    options.velocity_curve_high = get_option(
        opts, "velocity_curve_high", DEFAULT_OPTIONS.velocity_curve_high
    )  # quietest (!)

    if options.velocity_curve < 0.0001:  # if its near zero, set default
        options.velocity_curve = 1.0  # default

    options.mark_light = get_option(
        opts, "mark_light", DEFAULT_OPTIONS.mark_light
    )
    options.mark_color = get_option(
        opts, "mark_color", DEFAULT_OPTIONS.mark_color
    )
    options.mark_color = glm.ivec3(get_color(options.mark_color))

    options.min_velocity = get_option(
        opts, "min_velocity", DEFAULT_OPTIONS.min_velocity
    )
    options.max_velocity = get_option(
        opts, "max_velocity", DEFAULT_OPTIONS.max_velocity
    )
    options.show_lowest_note = get_option(
        opts, "show_lowest_note", DEFAULT_OPTIONS.show_lowest_note
    )

    options.y_bend = get_option(
        opts, "y_bend", DEFAULT_OPTIONS.y_bend
    )

    options.vibrato = get_option(opts, "vibrato", DEFAULT_OPTIONS.vibrato)
    options.midi_out = get_option(opts, "midi_out", DEFAULT_OPTIONS.midi_out)
    options.split_out = get_option(
        opts, "split_out", DEFAULT_OPTIONS.split_out
    )
    options.fps = get_option(opts, "fps", DEFAULT_OPTIONS.fps)
    options.chord_analyzer = get_option(opts, "chord_analyzer", DEFAULT_OPTIONS.chord_analyzer)
    options.split = get_option(
        opts, "split", DEFAULT_OPTIONS.split
    )
    options.foot_in = get_option(opts, "foot_in", DEFAULT_OPTIONS.foot_in)

    # which split the sustain affects
    options.sustain_split = get_option(
        opts, "sustain_split", "both"
    )  # left, right, both
    if options.sustain_split not in ("left", "right", "both"):
        print("Invalid sustain split value. Settings: left, right, both.")
        sys.exit(1)

    options.octave_separation = get_option(opts, "octave_separation", DEFAULT_OPTIONS.octave_separation)
    options.octave_split = get_option(opts, "octave_split", DEFAULT_OPTIONS.octave_split)

    hardware_split = False
    options.size = get_option(opts, "size", DEFAULT_OPTIONS.size)
    if options.size == 128:
        options.width = 16
        options.split_point = None
    elif options.size == 200:
        options.width = 25
        options.split_point = 11
        hardware_split = True
    elif options.size < 0:  # test hardware split
        options.width = 16
        options.split_point = -options.size
        hardware_split = True

    # Note: The default below is what is determined by size above.
    # Overriding hardware_split is only useful for 128 user testing 200 behavior
    options.hardware_split = get_option(opts, "hardware_split", hardware_split)

    options.launchpad = get_option(opts, 'launchpad', True)
    options.launchpad_channel = get_option(opts, 'launchpad_channel', 1)
    options.experimental = get_option(opts, 'experimental', False)
    options.debug = get_option(opts, 'debug', False)
    options.stabilizer = get_option(opts, 'stabilizer', False)
    options.stable_left = get_option(opts, 'stable_left', False)
    options.stable_right = get_option(opts, 'stable_right', False)

    return options, scale_db
