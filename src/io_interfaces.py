from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol


class MidiOut(Protocol):
    def send_raw(self, *bytes_: int) -> None: ...
    def send_cc(self, channel: int, cc: int, value: int) -> None: ...


class MidiIn(Protocol):
    callback: Optional[Callable[[list, float], None]]


# Matches launchpad_py's device objects (LaunchpadLPX, LaunchpadPro, LaunchpadProMk3)
# exactly, so raw launchpad_py devices satisfy this protocol with no adapter needed.
class ControlSurface(Protocol):
    mode: str

    def LedCtrlXY(self, x: int, y: int, r: int, g: int, b: int) -> None: ...
    def LedCtrlXYByCode(self, x: int, y: int, code: int) -> None: ...
    def ButtonStateXY(self, returnPressure: bool = False): ...
    def Reset(self) -> None: ...
    def LedSetMode(self, mode: int) -> None: ...


@dataclass
class RawSurface:
    """A control surface as opened by a backend, before Core wraps it in src.launchpad.Launchpad
    (which needs a `core` back-reference that doesn't exist until Core is constructed)."""
    device: ControlSurface
    mode: str
    octave_separation: int = 0


@dataclass
class IOContext:
    """Bundles the MIDI I/O a backend opens for injection into Core."""
    midi_out: Optional[MidiOut] = None
    split_out: Optional[MidiOut] = None
    linn_out: Optional[MidiOut] = None
    midi_in: Optional[MidiIn] = None
    visualizer: Optional[MidiIn] = None
    foot_in: Optional[MidiIn] = None
    control_surfaces: List[RawSurface] = field(default_factory=list)
