#!/usr/bin/python3
# from tkinter import *
from collections import OrderedDict
import os, sys, copy, binascii, struct, math, traceback, signal
from dataclasses import dataclass
from src import vecshim as glm
from src.vecshim import ivec2, vec2, ivec3, vec3
import time

from src.util import *
from src.constants import *
from src.note import Note
from src.device import Device, DeviceSettings
from src.launchpad import Launchpad
from src.articulation import Articulation
from src.io_interfaces import IOContext, MidiIn, MidiOut
from typing import Optional
# from src.gamepad import Gamepad

try:
    import musicpy as mp
except ImportError:
    # musicpy pulls in pygame transitively, which isn't available on every backend
    # (e.g. Android/Chaquopy) - chord analysis is simply unavailable there.
    mp = None

class Core:
    CORE = None
    
    def rotate_mode(self, notes: str, mode: int):
        """Rotates a mode string (see: scales.yaml strings with x and .)"""
        notes = copy.copy(notes)
        while mode:
            if notes[0] == 'x':
                notes = notes[1:] + notes[0]
            while notes[0] == '.':
                notes = notes[1:] + notes[0]
            mode -= 1
        return notes

    def prev_bank(self):
        return self.next_bank(-1)
    
    def next_bank(self, ofs=1):
        if not self.midi_out:
            return False
        self.bank = max(0, min(127, self.bank + ofs))
        msb = (self.bank >> 7) & 0x7f
        lsb = self.bank & 0x7f
        self.midi_out.send_cc(0, 0, msb)
        self.midi_out.send_cc(0, 32, lsb)
        print('Bank Select: ', self.bank)
        return True
    
    def prev_program(self):
        return self.next_program(-1)
    
    def next_program(self, ofs=1):
        if not self.midi_out:
            return False
        self.program = max(0, min(127, self.program + ofs))
        self.midi_write(self.midi_out, [0xc0, self.program], 0)
        print('Program Change:', self.program)
        return True

    def prev_mode(self, ofs=1):
        self.next_mode(-ofs)

    def next_mode(self, ofs=1):
        """Go to next mode according to offset (ofs), wrapping around if necessary"""
        self.set_mode((self.mode_index + ofs) % self.scale_notes.count('x'))
        self.dirty = self.dirty_lights = True

    def prev_scale(self, ofs=1):
        self.next_scale(-ofs)

    def next_scale(self, ofs=1):
        """Goes to first mode of the next scale in the scale db (ofs=offset)"""
        self.scale_index = (self.scale_index + ofs) % len(self.scale_db)
        self.scale_name = self.scale_db[self.scale_index]['name']
        self.set_mode(0)
        self.dirty = self.dirty_lights = True
    
    def set_mode(self, mode: int):
        """Set mode by index (0-indexed)"""
        self.scale_notes = self.rotate_mode(self.scale_db[self.scale_index]['notes'], mode)
        self.mode_index = mode
        try:
            self.mode_name = self.scale_db[self.scale_index]['modes'][mode]
        except:
            self.mode_name = 'Mode ' + str(self.mode_index + 1)
        self.scale_root = mode

    def set_scale(self, scale: int, mode: int):
        """Set scale and mode by number, 0-indexed"""
        self.scale_index = scale
        self.scale_name = self.scale_db[scale]['name']
        self.set_mode(mode)
    
    def has_velocity_curve(self):
        """Does user have custom velocity curve from the config?"""
        return abs(self.velocity_curve_ - 1.0) > EPSILON

    def has_velocity_settings(self):
        """Does user have any velocity settings from the config?"""
        return (
            self.options.min_velocity > 0
            or self.options.max_velocity < 127
            or self.has_velocity_curve()
        )

    def velocity_curve(self, val):  # 0-1
        """Apply custom velocity curve from config, if available"""
        if self.has_velocity_curve():
            val = val**self.velocity_curve_
        return val

    def send_ls_cc(self, channel, cc, val):
        """Send CC to LinnStrument channel with value, if connected"""
        if not self.linn_out:
            return
        # msg = [0xb0 | channel, cc, val]
        self.linn_out.send_cc(channel, cc, val)
        # self.linn_out.send_messages(0xb0, [(channel, cc, val)])

    def send_all_notes_off(self):
        if not self.midi_out:
            return
        # for ch in range(0,15):
        ch = 0
        self.midi_write(self.midi_out, [0xb0 | ch, 120, 0], 0)
        self.midi_write(self.midi_out, [0xb0 | ch, 123, 0], 0)

    def ls_color(self, x, y, col):
        """Set LinnStrument pad color"""
        if self.linn_out:
            self.send_ls_cc(0, 20, x + 1)
            self.send_ls_cc(0, 21, self.board_h - y - 1)
            self.send_ls_cc(0, 22, col)

    def set_light(self, x, y, col, index=None, mark=False):  # col is [1,11], 0 resets
        """Set light to color `col` at x, y if in range and connected"""
        if y < 0 or y >= self.board_h:
            return
        if x < 0 or x >= self.board_w:
            return

        if not index:
            index = self.get_note_index(x, y, transpose=False)

        self.mark_lights[y][x] = mark
        
        self.ls_color(x, y, col)

        if index is not None:
            for lp in self.launchpads:
                if self.scale_notes[index] != '.':
                    if self.options.launchpad_colors:
                        lp_col = self.options.launchpad_colors[index]
                    else:
                        lp_col = self.options.colors[index] / 4
                else:
                    if self.options.launchpad_colors:
                        lp_col = 0
                    else:
                        lp_col = ivec3(0)
                if 0 <= x < 8 and 0 <= y < 8:
                    if not self.is_macro_button(x, y):
                        if self.options.launchpad_colors:
                            lp.out.LedCtrlXYByCode(x, y+1, lp_col)
                        else:
                            lp.out.LedCtrlXY(x, y+1, lp_col[0], lp_col[1], None if lp_col[2] == 0 else lp_col[2])
                    else:
                        if self.options.launchpad_colors:
                            lp.out.LedCtrlXYByCode(x, y+1, 3)
                        else:
                            lp.out.LedCtrlXY(x, y+1, 63, 63, 63)

    def reset_light(self, x, y, reset_red=True):
        """Reset the light at x, y"""
        note = self.get_note_index(x, y, transpose=False)
        
        if self.is_split():
            split_chan = self.channel_from_split(x, self.board_h - y - 1)
            if split_chan:
                light_col = self.options.split_lights[note]
                try:
                    light_col = light_col if self.scale_notes[note]!='.' else 7
                except IndexError:
                    light_col = 7
            else:
                light_col = self.options.lights[note]
                try:
                    light_col = light_col if self.scale_notes[note]!='.' else 7
                except IndexError:
                    light_col = 7
        else:
            light_col = self.options.lights[note]
            try:
                light_col = light_col if self.scale_notes[note]!='.' else 7
            except IndexError:
                light_col = 7

        self.set_light(x, y, light_col, note)
        self.mark_lights[y][x] = False

    def reset_launchpad_light(self, x, y, launchpad=None):
        """Reset the launchpad light at x, y"""
        note = self.get_note_index(x, 8-y-1, transpose=False)
        # if self.is_split():
        #     split_chan = self.channel_from_split(x, self.board_h - y - 1)
        #     if split_chan:
        #         light_col = self.options.split_lights[note]
        #     else:
        # light_col = self.options.lights[note]
        # else:
        #     light_col = self.options.lights[note]
        for lp in ([launchpad] if launchpad else self.launchpads):
            self.set_launchpad_light(x, y, note)

    def set_mark_light(self, x, y, state=True, launchpad=None):
        """Set launchpad light to touched color"""
        self.mark_lights[y][x] = state
        for lp in ([launchpad] if launchpad else self.launchpads):
            lp_col = self.options.mark_color
            if state:
                lp.out.LedCtrlXY(x, y, lp_col[0], lp_col[1], lp_col[2])

    # `color` below is an scale index (0, 1, 2...)
    def set_launchpad_light(self, x, y, color, launchpad=None):
        """Set launchpad light to color index"""
        if self.is_macro_button(x, 8 - y - 1):
            if self.options.launchpad_colors:
                col = 1
            else:
                col = glm.ivec3(63,63,63)
        else:
            if color != -1: # not mark
                if color is not None and self.scale_notes[color] != '.':
                    if self.options.launchpad_colors:
                        col = self.options.launchpad_colors[color]
                    else:
                        col = self.options.colors[color] / 4
                else:
                    if self.options.launchpad_colors:
                        col = 0
                    else:
                        col = glm.ivec3(0,0,0)
            else:
                if self.options.launchpad_colors:
                    col = 1
                else:
                    col = self.options.mark_color / 4

        for lp in ([launchpad] if launchpad else self.launchpads):
            if self.options.launchpad_colors:
                lp.out.LedCtrlXYByCode(x, 8-y, col)
            else:
                lp.out.LedCtrlXY(x, 8-y, col[0], col[1], col[2])

    def setup_lights(self):
        """Set all lights"""
        for y in range(self.board_h):
            for x in range(self.board_w):
                if self.mark_lights[y][x]:
                    self.set_mark_light(x, y, True)
                else:
                    self.reset_light(x, y)

    def reset_lights(self):
        """Reset all lights to device defaults"""
        for y in range(self.board_h):
            for x in range(self.board_w):
                self.set_light(x, y, 0)

    # def get_octave(self, x, y):
    #     try:
    #         return self.octaves[y - self.board_h + self.flipped][x] + self.octave
    #     except IndexError:
    #         pass

    def xy_to_midi(self, x, y, transpose=True):
        """x, y coordinate to midi note based on layout (this can be improved)"""
        y = self.board_h - y - 1
        column_offset = self.options.column_offset
        row_offset = self.options.row_offset
        xx = x + self.options.base_offset
        r = row_offset * y + (column_offset * (xx + self.position.x))
        r += 24
        if self.flipped:
            r += 7
        r += self.tonic if transpose else 0
        # print(x, y, r)
        return r

    def get_note_index(self, x, y, transpose=True):
        """Get the note index (0-11) for a given x, y"""
        y += self.flipped
        x += self.position.x
        column_offset = self.options.column_offset
        row_offset = self.options.row_offset
        y = self.board_h - y - 1
        x += self.options.base_offset
        tr = self.tonic if transpose else 0
        return (row_offset * y + column_offset * x + tr) % len(NOTES)
        # ofs = (self.board_h - y) // 2 + BASE_OFFSET
        # step = 2 if WHOLETONE else 1
        # tr = self.tonic if transpose else 0
        # if y % 2 == 1:
        #     return ((x - ofs) * step - tr) % len(NOTES)
        # else:
        #     return ((x - ofs) * step + 7 - tr) % len(NOTES)

    def get_note(self, x, y, transpose=True):
        """Get note name for x, y"""
        return NOTES[self.get_note_index(x, y, transpose=transpose)]

    def get_color(self, x, y):
        """Get color for x, y"""
        # return NOTE_COLORS[get_note_index(x, y)]
        note = self.get_note_index(x, y, transpose=False)
        # note = (note - self.tonic) % 12
        # if self.is_split():
        #     split_chan = self.channel_from_split(x, self.board_h - y - 1)
        #     if split_chan:
        #         light_col = self.options.split_lights[note]
        #         try:
        #             light_col = light_col if self.scale_notes[note]!='.' else 7
        #         except IndexError:
        #             light_col = 7
        #     else:
        #         light_col = self.options.lights[note]
        #         try:
        #             light_col = light_col if self.scale_notes[note]!='.' else 7
        #         except IndexError:
        #             light_col = 7

        # else:
        #     light_col = self.options.lights[note]
        #     try:
        #         light_col = light_col if self.scale_notes[note]!='.' else 7
        #     except IndexError:
        #         light_col = 7

        if self.scale_notes[note] != '.':
            if self.channel_from_split(x, self.board_h - y - 1):
                return self.options.split_colors[note]
            else:
                return self.options.colors[note]
        else:
            return None

    def mouse_held(self):
        """Is mouse button is being held down?"""
        return self.mouse_midi != -1

    # layout button x, y and velocity
    def mouse_pos_to_press(self, x, y):
        """Translate board space x, y position to grid coordinate x, y with velocity"""
        vel = y % int(self.button_sz)
        x /= int(self.button_sz)
        y /= int(self.button_sz)

        vel = vel / int(self.button_sz)
        
        vel = 1 - vel
        
        vel *= 127
        vel = clamp(0, 127, int(vel))

        x, y = int(x), int(y)
        return (x, y, vel)

    def mouse_press(self, x, y, state=True, hold=False, hover=False, button_held=True):
        """Do mouse press at x, y"""
        if y < 0:
            return

        if hover:
            if not button_held:
                return

        # if we're not intending to hold the note, we release the previous primary note
        if not hover:
            if self.mouse_held():
                self.mouse_release()

        x, y, vel = self.mouse_pos_to_press(x, y)

        if hover and self.mouse_midi_vel is not None:
            # if hovering, get velocity of last click
            vel = self.mouse_midi_vel
        if not hover and self.mouse_midi_vel is None:
            self.mouse_midi_vel = vel # store velocity for initial click

        # vel = y % int(self.button_sz)
        # x /= int(self.button_sz)
        # y /= int(self.button_sz)

        # vel = vel / int(self.button_sz)
        # vel = 1 - vel
        # vel *= 127
        # vel = clamp(0, 127, int(vel))

        # x, y = int(x), int(y)
        v = ivec2(x, y)

        self.mark_xy(x, y, state)
        midinote = self.xy_to_midi(v.x, v.y)
        if hover:
            if self.mouse_midi == midinote:
                return
            else:
                self.mouse_release()
        if not hold:
            self.mouse_mark = v
            self.mouse_midi = midinote
            self.mouse_midi_vel = vel
        
        split_chan = self.channel_from_split(x, self.board_h - y - 1)
        
        data = [(0x90 if state else 0x80), midinote, vel]
        if split_chan:
            self.midi_write(self.split_out, data, 0)
        else:
            self.midi_write(self.midi_out, data, 0)

    def mouse_hold(self, x, y):
        """Do mouse hold at x, y"""
        return self.mouse_press(x, y, True, hold=True)

    def mouse_release(self, x=None, y=None):
        """Do mouse release at x, y"""
        # x and y provided? it's a specific coordinate
        if x is not None and y is not None:
            return self.mouse_press(x, y, False)
        # x and y not provided? it uses the primary mouse coordinate
        if self.mouse_midi != -1:
            self.mark_xy(self.mouse_mark.x, self.mouse_mark.y, False)
            data = [0x80, self.mouse_midi, 127]
            split_chan = self.channel_from_split(self.mouse_mark.x, self.board_h - self.mouse_mark.y - 1)
            if split_chan:
                self.midi_write(self.split_out, data, 0)
            else:
                self.midi_write(self.midi_out, data, 0)
            
            self.mouse_midi = -1

    def mouse_hover(self, x, y, button_held=True):
        """Do mouse hover at x, y"""
        self.mouse_press(x, y, hover=True, button_held=button_held)

    # Given an x,y position, find the octave
    #  (used to initialize octaves 2D array)
    def get_octave(self, x, y):
        """Get octave for x, y"""
        y = self.board_h - y - 1
        # if self.flipped:
        #     if self.tonic % 2 == 0:
        #         y -= 1
        #     octave = int(x + 4 + self.position.x + y * 2.5) // 6
        # else:
        if self.tonic % 2:
            y -= 1
        octave = int(x + 4 + self.position.x + y * 2.5) // 6
        return octave

    def held_note_count(self):
        """How many held notes?"""
        count = 0
        for n in self.notes:
            if n is not None and n.location:
                count += 1
        return count

    def init_board(self):
        """Initialize board"""
        # self.octaves = [
        #     # 200 size ---------------------------------------------------------------v
        #     # 128 size ------------------------------------v
        #     [3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 7, 7, 7, 7],
        #     [3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6, 7, 7],
        #     [2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6],
        #     [2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 6],
        #     [1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5],
        #     [1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5],
        #     [1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 4, 5],
        #     [0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4]
        # ]
        # generate grid of octaves like above
        # self.octaves = []
        # for y in range(self.board_h + 1):  # 1 = flipping
        #     self.octaves.append([])
        #     line = []
        #     for x in range(self.max_width):
        #         octave = self.get_octave(x, y)
        #         self.octaves[y].append(octave)
        #         line.append(octave)
            # print(line)

        self.notes = [None] * 16  # polyphony
        for i, note in enumerate(self.notes):
            self.notes[i] = Note()

        # Indexed directly by MIDI note number (0-127 inclusive), so need 128 slots.
        self.left_chord_notes = [False] * 128
        self.chord_notes = [False] * 128
        self.note_set = set()
        self.vis_board = [[0 for x in range(self.max_width)] for y in range(self.board_h)]

    def midi_write(self, dev, msg, ts=0):
        """Write MIDI message `msg` to device `dev`"""
        # if type(dev) in (list,tuple):
        #     for d in dev:
        #         self.midi_write(d, msg, ts)
        #     return
        if dev:
            dev.send_raw(*msg)

    def next_free_note(self):
        """Get the next note available in the polyphony array"""
        for note in self.notes:
            if note.location is None:
                return note
        return None

    def is_mpe(self):
        return self.options.one_channel == 0

    def note_on(self, data, timestamp, width=None, curve=True, mpe=None, octave=0, transpose=0, force_channel=None):
        # if mpe is None:
        #     mpe = self.options.mpe
        d0 = data[0]
        # print(data)
        ch = d0 & 0x0F
        msg = (data[0] & 0xF0) >> 4
        aftertouch = (msg == 10)
        
        if force_channel:
            data[0] = (d0 & 0xF0) + (force_channel-1)
        elif not self.is_mpe():
            data[0] = (d0 & 0xF0) + (self.options.one_channel-1)
        
        row = None
        col = None

        within_hardware_split = False
        if width is None:
            if self.options.hardware_split:
                # if self.board_w == 25: # 200
                left_width = self.split_point
                right_width = self.board_w - left_width
                # print('hardware splits', left_width, right_width)
                if ch >= 8:
                    width = right_width
                    within_hardware_split = True
                else:
                    width = left_width
                # else:
                #     left_width = 8
                #     right_width = 8
                #     if ch >= 8:
                #         width = right_width
                #         within_hardware_split = True
                #     else:
                #         width = left_width
            else:
                width = self.board_w
            # else: # 128
                # width = 8 if self.options.hardware_split else 16
        
        if self.options.debug:
            print("MIDI:", data)
            print("Message:", msg)
            print("Channel:", ch)
            print("---")

        row = data[1] // width
        col = data[1] % width
        col_full = col + (left_width if within_hardware_split else 0)
        # midinote = self.xy_to_midi(col, row)
        # y = self.board_h - row - 1
        column_offset = self.options.column_offset
        row_offset = self.options.row_offset
        y = row
        x = col
        midinote = row_offset * y + column_offset * x
        midinote += 32
        if self.flipped:
            midinote += 7
        if within_hardware_split:
            midinote += self.options.column_offset * left_width
        visual_midinote = midinote
        midinote += 12 * (octave + self.octave)
        midinote += self.options.column_offset * self.position.x
        midinote += transpose + self.tonic

        # print(x, y, midinote)
        # return

        # # if mpe:
        
        # col_full = col + (left_width if within_hardware_split else 0)
        # if within_hardware_split:
        #     data[1] += left_width
        # data[1] = col + 30 + 2.5 * row
        # data[1] *= 2
        # data[1] = int(data[1])
        # if within_hardware_split:
        #     data[1] += left_width * 2
        #     # print(data[1])
        # # else:
        # #     row = ch % 8
        # #     col = ch // 8
        # #     data[1] *= 2
        # #     try:
        # #         data[1] -= row * 5
        # #     except IndexError:
        # #         pass
        
        # data[1] += (self.octave + self.octave_base + octave) * 12
        # data[1] += transpose
        # data[1] += BASE_OFFSET
        # midinote = data[1] - 24 + self.position.x * 2
        col_full = x + (left_width if within_hardware_split else 0)
        
        # figure out if note is on left or right side
        side = self.channel_from_split(col_full, row, force=True)

        # if the note is on the right side, we shift by the current octave split
        if side == 1:
            octave_split = self.options.octave_split
            if octave_split != 0:
                midinote += 12 * octave_split

        # if we have a split set up, set the side found above to split_chan,
        #   otherwise everything should be in the same region
        if self.is_split():
            split_chan = side
        else:
            split_chan = 0

        # extreme octave/transpose/position combinations can push this outside the valid
        # MIDI note range; clamp rather than send an invalid byte (crashes some backends'
        # raw MIDI write, e.g. Python's bytes() on a negative int).
        midinote = clamp(0, 127, midinote)
        data[1] = midinote

        if not aftertouch:
            self.mark(visual_midinote - 24, 1, only_row=row)
        # data[1] += self.out_octave * 12 + self.position.x * 2
        # if self.flipped:
        #     data[1] += 7
        
        # velocity (or pressure if aftertouch)
        vel = data[2] / 127
        if curve and not aftertouch:
            # apply curve
            if self.has_velocity_settings():
                vel = self.velocity_curve(data[2] / 127)
                data[2] = clamp(
                    self.options.min_velocity,
                    self.options.max_velocity,
                    int(vel * 127 + 0.5),
                )

        if aftertouch:
            # TODO: add aftertouch values into notes array
            #   This is not necessary yet
            pass
        else:
            # if self.options.mpe:
            note = self.notes[ch]
            # else:
            #     note = self.next_free_note()
            if note:
                if note.location is None:
                    note.location = ivec2(0)
                note.location.x = x
                note.location.y = y
                note.pressure = vel
                note.midinote = midinote
                note.split = split_chan

            # if self.options.jazz:
            #     if side == 0:
            #         self.left_chord_notes[data[1]] = True
                    # self.dirty_left_chord = True
        
            self.chord_notes[midinote] = True
            self.note_set.add(midinote)
            self.dirty_chord = True

        if self.is_split():
            if split_chan == 0:
                # self.midi_out.write([[data, ev[1]]]
                self.midi_write(self.midi_out, data, timestamp)
            else:
                self.midi_write(self.split_out, data, timestamp)
        else:
            # print(data[1]) # midi note number
            self.midi_write(self.midi_out, data, timestamp)

    def note_off(self, data, timestamp, width=None, mpe=None, octave=0, transpose=0, force_channel=None):
        # if mpe is None:
        #     mpe = self.options.mpe
        
        d0 = data[0]
        # print(data)
        ch = d0 & 0x0F
        msg = (data[0] & 0xF0) >> 4
        if force_channel:
            data[0] = (d0 & 0xF0) + (force_channel-1)
        elif not self.is_mpe():
            data[0] = (d0 & 0xF0) + (self.options.one_channel-1)
        row = None
        col = None

        # if width is None:
        #     if self.board_w == 25: # 200
        #         width = 11 if self.options.hardware_split else 25
        #     else: # 128
        #         width = 8 if self.options.hardware_split else 16
        # if width is None:
        #     left_width = 5
        #     right_width = 11
        #     width = left_width if ch < 8 else right_width
        
        within_hardware_split = False
        if width is None:
            if self.options.hardware_split:
                # if self.board_w == 25: # 200
                left_width = self.split_point
                right_width = self.board_w - left_width
                # print('hardware splits', left_width, right_width)
                if ch >= 8:
                    width = right_width
                    within_hardware_split = True
                else:
                    width = left_width
                # else:
                #     left_width = 8
                #     right_width = 8
                #     if ch >= 8:
                #         width = right_width
                #         within_hardware_split = True
                #     else:
                #         width = left_width
            else:
                width = self.board_w
        
        # # if not mpe:
        # #     row = ch % 8
        # #     col = ch // 8
        # # if mpe:
        #     # row and col within the current split
        #     # row = data[1] // width
        #     # col = data[1] % width
        #     # print(data[1])
        #     # # data[1] = data[1] % width + 30 + 2.5 * row
        #     # data[1] *= 2
        #     # data[1] = int(data[1])
        #     # if self.options.hardware_split and ch >= 8:
        #     #     data[1] += self.board_w
        # row = data[1] // width
        # col = data[1] % width
        # col_full = col + (left_width if within_hardware_split else 0)
        # if within_hardware_split:
        #     data[1] += left_width
        # data[1] = col + 30 + 2.5 * row
        # data[1] *= 2
        # data[1] = int(data[1])
        # if within_hardware_split:
        #     data[1] += left_width * 2
        # # else:
        # #     data[1] *= 2
        # #     try:
        # #         data[1] -= row * 5
        # #     except IndexError:
        # #         pass
        
        # data[1] += (self.octave + self.octave_base + octave) * 12
        # data[1] += BASE_OFFSET
        # data[1] += transpose
        # midinote = data[1] - 24 + self.position.x * 2

        row = data[1] // width
        col = data[1] % width
        # print('xy', col, row)
        # col_full = col + (left_width if within_hardware_split else 0)
        # midinote = self.xy_to_midi(col, row)
        # y = self.board_h - row - 1
        column_offset = self.options.column_offset
        row_offset = self.options.row_offset
        y = row
        x = col
        midinote = row_offset * y + column_offset * x
        midinote += 32
        if self.flipped:
            midinote += 7
        if within_hardware_split:
            midinote += self.options.column_offset * left_width
        visual_midinote = midinote
        midinote += 12 * (octave + self.octave)
        midinote += self.options.column_offset * self.position.x
        midinote += transpose + self.tonic
        # print('off', x, y, midinote)

        col_full = x + (left_width if within_hardware_split else 0)
        side = self.channel_from_split(col_full, y, force=True)

        # if the note is on the right side, we shift by the current octave split
        if side == 1:
            octave_split = self.options.octave_split
            if octave_split != 0:
                midinote += 12 * octave_split
        
        if self.is_split():
            split_chan = side
        else:
            split_chan = 0

        # see note_on(): clamp rather than send an invalid MIDI note byte.
        midinote = clamp(0, 127, midinote)
        data[1] = midinote

        self.mark(visual_midinote - 24, 0, only_row=y)
        # data[1] += self.out_octave * 12 + self.position.x * 2
        # if self.flipped:
        #     data[1] += 7

        # if self.options.jazz:
        #     if side == 0:
        #         self.left_chord_notes[data[1]] = False
                # self.dirty_left_chord = True
        
        self.chord_notes[data[1]] = False
        try:
            self.note_set.remove(data[1])
        except KeyError:
            pass
        self.dirty_chord = True

        if self.is_split():
            if split_chan == 0:
                self.midi_write(self.midi_out, data, timestamp)
            else:
                self.midi_write(self.split_out, data, timestamp)
        else:
            self.midi_write(self.midi_out, data, timestamp)
        # print('note off: ', data)

    # def device_to_xy(self, data, force_channel=None):
    #     # mpe = self.options.mpe
        
    #     d0 = data[0]
    #     ch = d0 & 0x0F
    #     msg = (data[0] & 0xF0) >> 4
    #     if force_channel:
    #         data[0] = (d0 & 0xF0) + (force_channel-1)
    #     elif not self.is_mpe():
    #         data[0] = (d0 & 0xF0) + (self.options.one_channel-1)
    #     row = None
    #     col = None

    #     within_hardware_split = False
    #     if self.options.hardware_split:
    #         if self.board_w == 25: # 200
    #             left_width = 11
    #             right_width = 14
    #             if ch >= 8:
    #                 width = right_width
    #                 within_hardware_split = True
    #             else:
    #                 width = left_width
    #         else:
    #             left_width = 8
    #             right_width = 8
    #             if ch >= 8:
    #                 width = right_width
    #                 within_hardware_split = True
    #             else:
    #                 width = left_width
    #     else:
    #         width = self.board_w
        
    #     # if not mpe:
    #     #     row = ch % 8
    #     #     col = ch // 8
    #     # if mpe:
    #     row = data[1] // width
    #     col = data[1] % width
    #     if within_hardware_split:
    #         data[1] += left_width
    #     data[1] = col + 30 + 2.5 * row
    #     data[1] *= 2
    #     data[1] = int(data[1])
    #     if within_hardware_split:
    #         data[1] += left_width * 2
    #     # else:
    #     #     data[1] *= 2
    #     #     try:
    #     #         data[1] -= row * 5
    #     #     except IndexError:
    #     #         pass
        
    #     return col, row
    
    def cb_midi_in(self, data, timestamp, force_channel=None):
        """LinnStrument MIDI Callback"""
        # d4 = None
        # if len(data)==4:
        #     d4 = data[3]
        #     data = data[:3]
        d0 = data[0]
        # print(data)
        ch = d0 & 0x0F
        msg = (data[0] & 0xF0) >> 4
        row = None
        col = None
        # if not self.options.mpe:
        #     row = ch % 8
        #     col = ch // 8
        if msg == 9:  # note on
            if data[2] == 0: # 0 vel
                self.note_off(data, timestamp)
            else:
                self.note_on(data, timestamp)
            # print('note on: ', data)
        elif msg == 8:  # note off
            self.note_off(data, timestamp)
        elif 0xF0 <= msg <= 0xF7:  # sysex
            # rewrite the output channel based on app's MPE settings
            self.midi_write(self.midi_out, data, timestamp)
        else:
            # rewrite the output channel based on app's MPE settings
            if force_channel:
                data[0] = (d0 & 0xF0) | (force_channel-1)
            elif not self.is_mpe():
                data[0] = (d0 & 0xF0) | (self.options.one_channel-1)
            
            skip = False
            if msg == 14:
                if self.is_split():
                    # experimental: ignore pitch bend for a certain split
                    split_chan = self.notes[ch].split
                    if self.options.stable_left and split_chan == 0:
                        data[1] = 0
                        data[2] = 64
                        self.midi_write(self.midi_out, data, timestamp)
                        skip = True
                    if self.options.stable_right and split_chan == 1:
                        data[1] = 0
                        data[2] = 64
                        self.midi_write(self.split_out, data, timestamp)
                        skip = True
                
            # use_stabilizer = self.options.stabilizer
            # bend = decompose_pitch_bend([data[1], data[2]])
            # print('bend', bend)
            # note_ofs = bend * 24
            # print(' note_ofs', note_ofs)
            # closest_ofs = round(note_ofs)
            # print(' closest_ofs', closest_ofs)
            # diff = note_ofs - closest_ofs # diff between note and tuning
            # diff **= 0.9 # bend the curve
            # print(' diff', diff)
            # note_ofs = closest_ofs + diff
            # bend = note_ofs / 24
            # print(' end bend', bend)
            # data[1], data[2] = compose_pitch_bend(bend)
            # print(data[1], data[2])
            # semitones = pitch_bend_to_semitones(bend)
            # print('pitch', bend, semitones)
            # stabilized = True
            
            # This block has to happen before the below block rewrites y axis to pitch bend
            if self.options.y_bend:
                pb_range = self.options.bend_range * 2
                bend_threshold = 1 # units
                if msg == 14:
                    # if y-bending enabled, rewrite pitch bend based on y bend value
                    note = self.notes[ch]
                    # if note.y_bend > EPSILON:
                    val = decompose_pitch_bend((data[1], data[2]))
                    note.bend = val
                    val += note.y_bend / pb_range
                    data[1], data[2] = compose_pitch_bend(val)

                if msg == 11 and data[1] == 74:
                    # print(data[2])
                    if data[2] > 127 - bend_threshold:
                        bend = (data[2] - (127 - bend_threshold)) / bend_threshold
                    elif data[2] <= bend_threshold:
                        # bend down?
                        # bend = -(bend_threshold - data[2]) / bend_threshold
                        bend = None
                    else:
                        bend = None
                    note = self.notes[ch]
                    data = [0xe0 | ch,0,0]
                    if force_channel:
                        data[0] = 0xe0 | (force_channel-1)
                    elif not self.is_mpe():
                        data[0] = 0xe0 | (self.options.one_channel-1)
                    if bend is not None:
                        if bend > 0.9:
                            bend = 1.0
                        elif bend < -0.9:
                            bend = -1.0
                        note.y_bend = bend
                        data[1], data[2] = compose_pitch_bend(note.bend + note.y_bend / pb_range)
                    else:
                        note.y_bend = 0.0
                        data[1], data[2] = compose_pitch_bend(note.bend + note.y_bend / pb_range)


            if skip:
                pass
            elif msg == 11 and data[1] == 64:  # sustain pedal
                if self.is_split():
                    for dev in self.sustainable_devices():
                        self.midi_write(dev, data, timestamp)
                else:
                    self.midi_write(self.midi_out, data, timestamp)
            elif self.is_split(): # everything else (if split)...
                # print('ch', ch)
                try:
                    note = self.notes[ch]
                except:
                    note = None
                if ch == 0:
                    self.midi_write(self.midi_out, data, timestamp)
                    self.midi_write(self.split_out, data, timestamp)
                # else:
                #     split_chan = 1 if ch >= 8 else 0
                #     if split_chan:
                #         self.midi_write(self.split_out, data, timestamp)
                #     else:
                #         self.midi_write(self.midi_out, data, timestamp)
                elif note and note.location is not None:
                    col = note.location.x
                    row = note.location.y
                    split_chan = self.channel_from_split(col, row)
                    if split_chan:
                        self.midi_write(self.split_out, data, timestamp)
                    else:
                        self.midi_write(self.midi_out, data, timestamp)
                else:
                    self.midi_write(self.midi_out, data, timestamp)
                    self.midi_write(self.split_out, data, timestamp)
            else:  # everything else (if not split)...
                self.midi_write(self.midi_out, data, timestamp)

    def cb_visualizer(self, data, timestamp):
        """Visualizer MIDI Callback"""
        # print(msg, timestamp)
        ch = data[0] & 0x0F
        msg = data[0] >> 4
        if msg == 9:  # note on
            self.mark(data[1] + self.vis_octave * 12, 1, True, vis=True)
        elif msg == 8:  # note off
            self.mark(data[1] + self.vis_octave * 12, 0, True, vis=True)
        # else:
            # print(msg, data)

    def cb_foot(self, data, timestamp):
        """Foot controller MIDI Callback"""
        ch = data[0] & 0x0F
        msg = (data[0] & 0xF0) >> 4
        if msg == 11:
            # change velocity curve
            val = data[1]
            val2 = None
            if val == 27:  # left expr pedal
                self.midi_write(self.midi_out, data, 0)
                if self.is_split():
                    data[1] = 67  # soft pedal
                    self.midi_write(self.split_out, data, 0)
            elif val == 7:  # right expr pedal
                val2 = 1.0 - data[2] / 127
                low = self.options.velocity_curve_low
                high = self.options.velocity_curve_high
                self.velocity_curve_ = low + val2 * (high - low)

    def is_macro_button(self, x, y):
        """Is pad at x, y bound to a macro?"""
        return False
        # return x == 0 and y == 0

    def macro(self, x, y, val):
        """Do macro on x, y pad"""
        if not self.is_macro_button(x, y):
            return False
        if x == 0 and y == 0:
            if val is True:
                return
            if val is False:
                val = 0.0
            self.articulation.set(val)
        return True

    # uses button state events (mk3 pro)
    def cb_launchpad_in(self, lp, event, timestamp=0):
        """Launchpad MIDI Callback"""
        if (lp.mode == "pro" or lp.mode == "promk3") and event[0] >= 255:
        # if event[0] >= 255: # uncomment this for testing pro behavior on launchpad X
            # I'm testing the mk3 method on an lpx, so I'll check this here
            vel = event[2] if lp.mode == 'lpx' else event[1]
            for note in self.note_set:
                self.midi_write(self.midi_out, [160, note, vel], timestamp)
                self.articulation.pressure(vel / 127)
        elif lp.mode == 'lpx' and event[0] >= 255: # pressure
            x = event[0] - 255
            y = 8 - (event[1] - 255)
            vel = event[2]
            note = y * 8 + x
            note += 12
            if not self.is_macro_button(x,  8 - y - 1):
                self.note_on([160, note, event[2]], timestamp, width=8, transpose=lp.transpose, octave=lp.get_octave(), force_channel=self.options.launchpad_channel)
                self.articulation.pressure(vel / 127)
            else:
                self.macro(x, 8 - y - 1, vel / 127)
        elif event[2] == 0: # note off
            x = event[0]
            y = 8 - event[1]
            if 0 <= x < 8 and 0 <= y < 8:
                self.reset_launchpad_light(x, y, launchpad=lp)
                if not self.is_macro_button(x, 8 - y - 1):
                    note = y * 8 + x
                    self.note_off([128, note, event[2]], timestamp, width=8, transpose=lp.transpose, octave=lp.get_octave(), force_channel=self.options.launchpad_channel)
                else:
                    self.macro(x, 8 - y - 1, False)
            else:
                # Launchpad X buttons
                lp.button(x, 8 - y - 1)
        else: # note on
            x = event[0]
            y = 8 - event[1]
            if 0 <= x < 8 and 0 <= y < 8:
                self.set_launchpad_light(x, y, -1, launchpad=lp)
                if not self.is_macro_button(x, 8 - y - 1):
                    note = y * 8 + x
                    self.note_on([144, note, event[2]], timestamp, width=8, transpose=lp.transpose, octave=lp.get_octave(), force_channel=self.options.launchpad_channel)
                else:
                    self.macro(x, 8 - y - 1, True)

    # uses raw events (Launchpad X)
    # def cb_launchpad_in(self, event, timestamp=0):
    #     if event[0] == 144:
    #         # convert to x, y (lower left is 0, 0)
    #         y = event[1] // 10 - 1
    #         x = event[1] % 10 - 1
    #         # convert it to no overlap chromatic
            
    #         self.launchpad_state[y][x] = None
    #         note = y * 8 + x
    #         self.note_off([128, note, event[2]], timestamp, width=8, mpe=True)
    #     elif event[0] == 160:
    #         y = event[1] // 10 - 1
    #         x = event[1] % 10 - 1
    #         state = self.launchpad_state[y][x]
    #         self.launchpad_state[y][x] = event[2]
    #         note = y * 8 + x
    #         if state is None: # just pressed
    #             self.note_on([144, note, event[2]], timestamp, width=8, mpe=True, curve=False)
    #         self.note_on([160, note, event[2]], timestamp, width=8, mpe=True, curve=False)
    #     elif event[0] == 176:
    #         if event == [176, 93, 127, 0]:
    #             self.move_board(-1)
    #             self.dirty = self.dirty_lights = True
    #         elif event == [176, 94, 127, 0]:
    #             self.move_board(1)
    #             self.dirty = self.dirty_lights = True
            
        # if events[0] >= 255:
        #     print("PRESSURE: " + str(events[0]-255) + " " + str(events[1]))
        # else:
        #     if events[1] > 0:
        #         print("PRESSED:  ", end='')
        #     else:
        #         print("RELEASED: ", end='')
        #     print(str(events[0]) + " " + str(events[1]))

    # def save():
    #     self.cfg = ConfigParser(allow_no_value=True)
    #     general = self.cfg['general'] = {}
    #     if self.options.lights:
    #         general['lights'] = ','.join(map(str,self.options.lights))
    #     general['one_channel'] = self.options.one_channel
    #     general['velocity_curve'] = self.options.velocity_curve
    #     general['min_velocity'] = self.options.min_velocity
    #     general['max_velocity'] = self.options.max_velocity
    #     general['mpe'] = self.options.mpe
    #     general['hardware_split'] = self.options.hardware_split
    #     general['show_lowest_note'] = self.options.show_lowest_note
    #     general['midi_out'] = self.options.midi_out
    #     general['split_out'] = self.options.split_out
    #     general['split'] = SPLIT
    #     general['fps'] = self.options.fps
    #     general['sustain'] = SUSTAIN
    #     self.cfg['general'] = general
    #     with open('settings_temp.ini', 'w') as configfile:
    #         self.cfg.write(configfile)

    # def init_launchpad(self):
    #     pattern = [
    #         'ggggbb',
    #         'cggbbb',
    #     ]
        
    #     self.launchpad.LedCtrlXY(x, y+1, lp_col[0], lp_col[1], lp_col[2])

    #     for y in range(1, 9):
    #         for x in range(0, 8):
    #             yy = y - 2
    #             xx = x
    #             yy -= 3
    #             xx -= (8-yy-1)//2
    #             col = pattern[yy%2][xx%6]
    #             if col == 'c': #cyan
    #                 col = [0, 63, 63]
    #             elif col == 'g': #green
    #                 col = [0, 63, 0]
    #             elif col == 'b': #black
    #                 col = [0, 0, 0]
    #             self.launchpad.LedCtrlXY(x, y, col[0], col[1], col[2])

    def sig(self, signal, frame):
        """Signal handler"""
        self.quit()

    def __init__(self, options, scale_db, io: IOContext):
        Core.CORE = self

        signal.signal(signal.SIGINT, self.sig)
        signal.signal(signal.SIGTERM, self.sig)

        self.options = options
        self.scale_db = scale_db
        self.split_point = options.split_point
        self.split_state = options.split

        if mp is None:
            self.options.chord_analyzer = False

        dups = {}
        scale_count = 0
        for scale in self.scale_db:
            notes = scale['notes']
            count = notes.count('x')
            dupes = (scale.get('duplicates') is True) or False
            if not dupes:
                scale_count += count
                for i in range(count):
                    mode_notes = self.rotate_mode(notes, i)
                    try:
                        name = scale['name'] + ' ' + scale['modes'][i]
                    except:
                        name = scale['name'] + ' Mode ' + str(i+1)
                    if mode_notes in dups:
                        print('Duplicate scale: ', dups[mode_notes], ' and ', name)
                        break
                    else:
                        dups[mode_notes] = name
            else:
                scale_count += 1

        self.panel_sz = 32
        self.status_sz = 32
        self.menu_sz = 32
        self.max_width = 25  # MAX WIDTH OF LINNSTRUMENT
        self.board_h = 8
        self.scale = vec2(64.0)

        self.board_w = self.options.width
        self.board_sz = ivec2(self.board_w, self.board_h)
        self.screen_w = self.board_w * self.scale.x
        self.screen_h = self.board_h * self.scale.y + self.menu_sz + self.status_sz
        self.button_sz = self.screen_w / self.board_w
        self.screen_sz = ivec2(self.screen_w, self.screen_h)

        self.lowest_note = None  # x,y location of lowest note currently pressed
        self.lowest_note_midi = None  # midi number of lowest note currently pressed
        self.octave = 0
        self.out_octave = 0
        self.vis_octave = (
            -2
        )  # this is for both the visualizer and the keyboard simulator marking atm
        self.octave_base = -2
        self.position = glm.ivec2(0, 0) # only x is used right now
        self.rotated = False  # transpose -3 whole steps
        self.flipped = False  # vertically shift +1
        self.config_save_timer = 1.0

        self.velocity_curve_ = self.options.velocity_curve

        self.mouse_mark = ivec2(0)
        self.mouse_midi = -1
        self.mouse_midi_vel = None

        self.last_note = None # ivec2
        self.chord = ''

        self.scale_index = 0
        self.mode_index = 0
        self.scale_name = self.scale_db[self.scale_index]['name']
        self.mode_name = self.scale_db[self.scale_index]['modes'][self.mode_index]
        self.scale_notes = self.scale_db[self.scale_index]['notes']
        self.scale_root = 0
        self.tonic = 0

        self.program = 0
        self.bank = 0

        self.articulation = Articulation(self)

        self.init_board()

        self.out = []
        self.midi_out: Optional[MidiOut] = io.midi_out
        self.split_out: Optional[MidiOut] = io.split_out
        self.linn_out: Optional[MidiOut] = io.linn_out
        self.midi_in: Optional[MidiIn] = io.midi_in
        self.visualizer: Optional[MidiIn] = io.visualizer
        self.foot_in: Optional[MidiIn] = io.foot_in

        self.launchpads = [
            Launchpad(self, rs.device, rs.mode, index, rs.octave_separation)
            for index, rs in enumerate(io.control_surfaces)
        ]

        self.done = False

        for lp in self.launchpads:
            lp.set_lights()

        self.dirty = True
        self.dirty_lights = True
        self.dirty_chord = False

        w = self.max_width
        h = self.board_h
        self.board = [[0 for x in range(w)] for y in range(h)]
        self.vis_board = [[0 for x in range(w)] for y in range(h)]
        self.mark_lights = [[False for x in range(w)] for y in range(h)]
        self.launchpad_state = [[None for x in range(8)] for y in range(8)]

        self.setup_rpn()

    def midi_mode_rpn(self, on=True):
        if on:
            self.rpn(0, 1 if self.is_mpe() else 0)
            self.rpn(100, 1 if self.is_mpe() else 0)
        else:
            self.rpn(0, 1)
            self.rpn(100, 1)

    def setup_rpn(self, on=True):
        """Sets all relevant RPN settings"""
        if on:
            self.midi_mode_rpn()
            # if self.options.mpe:
            self.mpe_rpn()
            self.transpose_rpn()
            # else:
            #     self.rows_rpn()
            self.bend_rpn()
            self.split_rpn(self.options.hardware_split)
        else:
            self.midi_mode_rpn(False)
            self.transpose_rpn(False)
            self.mpe_rpn(False)
            self.bend_rpn(False)
            self.split_rpn(False)

    def split_rpn(self, on=True):
        """Sets up RPN for hardware split (used on LinnStrument 200)"""
        if self.options.hardware_split:
            self.rpn(200, 1 if on else 0) # split active
            self.rpn(202, self.split_point + 1)

            # lights
            self.send_ls_cc(0, 20, 0)
            self.send_ls_cc(0, 21, 1)
            self.send_ls_cc(0, 22, 7 if on else 0)
        else:
            self.rpn(200, 0)
            self.rpn(202, self.split_point if self.split_point else 8)

    def rpn(self, num, value):
        if not self.linn_out:
            return
        """LinnStrument RPN"""
        num_msb, num_lsb = decode_value(num)
        value_msb, value_lsb = decode_value(value)
        self.midi_write(self.linn_out, [176, 99, num_msb])
        self.midi_write(self.linn_out, [176, 98, num_lsb])
        self.midi_write(self.linn_out, [176, 6, value_msb])
        self.midi_write(self.linn_out, [176, 38, value_lsb])
        self.midi_write(self.linn_out, [176, 101, 127])
        self.midi_write(self.linn_out, [176, 100, 127])
        time.sleep(0.05)

    def mpe_rpn(self, on=True):
        """Sets up MPE settings (except MIDI mode)"""
        if not self.linn_out:
            return

        if on:
            # self.rpn(0, 1)
            # self.rpn(100, 1)
            self.rpn(227, 0) # no overlap

            if self.options.hardware_split:
                # left side channels
                self.rpn(1, 1)
                self.rpn(2, 0)
                for x in range(3, 10):
                    self.rpn(x, 1)
                for x in range(10, 18):
                    self.rpn(x, 0)

                # right side channels
                self.rpn(101, 16) # main=16
                for x in range(110, 117):
                    self.rpn(x, 1)
                self.rpn(117, 0) # 16 off
            else:
                # left side only
                self.rpn(1, 1)
                self.rpn(2, 0)
                for x in range(3, 18):
                    self.rpn(x, 1)

        else:
            self.rpn(227, 5)

    # def rows_rpn(self):
    #     self.rpn(0, 2)
    #     self.rpn(100, 2)

    def bend_rpn(self, on=True):
        if on:
            # set bend range for both split
            self.rpn(19, self.options.bend_range)
            self.rpn(119, self.options.bend_range)
        else:
            # reset bend range for both splits
            self.rpn(19, 48)
            self.rpn(119, 48)
    
    def transpose_rpn(self, on=True):
        if on:
            # set transpose in both splits
            self.rpn(36, 2)
            self.rpn(37, 13)
            self.rpn(136, 2)
            self.rpn(137, 13)

            # turn transpose light off
            self.send_ls_cc(0, 20, 0)
            self.send_ls_cc(0, 21, 4)
            self.send_ls_cc(0, 22, 7)

        else:
            # reset transpose in both splits
            self.rpn(36, 5)
            self.rpn(37, 7)
            self.rpn(136, 5)
            self.rpn(137, 7)

    # def test(self):
    #     self.transpose_rpn()

    def move_board(self, val):
        self.send_all_notes_off()
        
        aval = abs(val)
        if aval > 1:  # more than one shift
            assert False
            # sval = sign(val)
            # for rpt in range(aval):
            #     self.move_board(sval)
            #     self.position.x += sval
        elif val == 1:  # shift right (add column left)
            for y in range(len(self.board)):
                self.board[y] = [0] + self.board[y][:-1]
            self.position.x += val
        elif val == -1:  # shift left (add column right)
            for y in range(len(self.board)):
                self.board[y] = self.board[y][1:] + [0]
            self.position.x += val
        self.dirty = self.dirty_lights = True

    def quit(self):
        self.reset_lights()
        self.setup_rpn(False)
        self.done = True

    def clear_marks(self, use_lights=False):
        y = 0
        for row in self.board:
            x = 0
            for x in range(len(row)):
                idx = self.get_note_index(x, y)
                try:
                    self.board[y][x] = False
                except IndexError:
                    pass
                if use_lights:
                    # if state:
                    #     self.set_light(x, y, 1)
                    # else:
                    self.reset_light(x, y)
            y += 1
        self.dirty = True
        if use_lights:
            self.dirty_lights = True

    def mark_xy(self, x, y, state, use_lights=False):
        if self.flipped:
            y -= 1
        # print(x, y)
        idx = self.get_note_index(x, y)
        try:
            self.board[y + self.flipped][x] = state
        except IndexError:
            print("mark_xy: Out of range")
            pass
        if use_lights:
            if state:
                self.set_light(x, y, self.options.mark_light, mark=True)
            else:
                self.reset_light(x, y)
        self.dirty = True

    def mark(self, midinote, state, use_lights=False, only_row=None, vis=False):
        if only_row is not None:
            only_row = self.board_h - only_row - 1 - self.flipped  # flip
            try:
                rows = [self.board[only_row]]
                y = only_row
            except IndexError:
                rows = self.board
                y = 0
        else:
            rows = self.board
            y = 0
        target_board = self.vis_board if vis else self.board
        for row in rows:
            x = 0
            for x in range(len(row)):
                idx = self.get_note_index(x, y, transpose=False)
                # print(x, y, midinote%12, idx)
                if midinote % 12 == idx:
                    octave = self.get_octave(x, y)
                    if octave == midinote // 12:
                        # print(octave)
                        target_board[y + self.flipped][x + self.position.x] = state
                        if use_lights and not vis:
                            if state:
                                self.set_light(x, y, self.options.mark_light, mark=True)
                            else:
                                self.reset_light(x, y)
                        elif use_lights and vis:
                            # Trigger the unified visualizer light logic
                            self.set_vis_light(x, y, state)
            y += 1
        self.dirty = True

    def channel_from_split(self, col, row, force=False):
        if not force and not self.is_split():
            return 0
        w = self.board_w
        col += 1  # move start point from C to A#
        col -= (row + 1) // 2  # make the split line diagonal
        ch = 0 if col < w // 2 else 1  # channel 0 to 1 depending on split
        return ch

    def is_split(self):
        # TODO: make this work with hardware overlap (non-mpe)
        return self.split_state and self.split_out

    def set_tonic(self, val):
        self.send_all_notes_off()
        
        self.tonic = val
        # print('val', val)
        # odd = (self.tonic % 2 == 1)
        # self.tonic = val
        # print('new tonic', self.tonic)
        # new_odd = (self.tonic % 2 == 1)
        # # if odd != new_odd:
        # #     self.flipped = not self.flipped
        # if new_odd:
        #     self.position.x = (self.tonic // 2 - 3) % 6
        #     self.position.x -= 6
        # else:
        #     self.position.x = (self.tonic // 2) % 6
        # print('new pos', self.position.x)
        
        self.dirty = self.dirty_lights = True

    def poll_launchpads(self):
        """Drain pending button/pressure events from each connected Launchpad."""
        for lp in self.launchpads:
            while True:
                event = lp.out.ButtonStateXY(returnPressure=True)
                if event:
                    self.cb_launchpad_in(lp, event)
                else:
                    break

    def logic(self, dt):
        if self.launchpads:
            self.articulation.logic(dt)

        if self.dirty_lights:
            self.setup_lights()
            self.dirty_lights = False

        if not self.options.lite:
            if self.options.chord_analyzer:
                if self.dirty_chord:
                    self.chord = self.analyze(self.chord_notes)

            self.dirty_chord = False

    def analyze(self, chord_notes):
        notes = []
        r = ''
        for i, note in enumerate(chord_notes):
            if note:
                notes.append(NOTES[i % 12])
        if notes:
            r = mp.alg.detect(mp.chord(','.join(notes)))
            # try:
            #     r = r[0:self.chord.index(' sort')]
            # except ValueError:
            #     pass
            # if self.chord.startswith('note '):
            #     r = r[len('note '):-1]
        else:
            r = ''
        self.dirty = True
        return r

    def sustainable_devices(self):
        if not self.is_split() or not self.options.sustain_split:
            return [self.midi_out]
        if self.options.sustain_split == "left":
            return [self.midi_out]
        if self.options.sustain_split == "right":
            return [self.split_out]
        if self.options.sustain_split == "both":
            return [self.midi_out, self.split_out]
        return [self.midi_out]

    def deinit(self):
        for lp in self.launchpads:
            if lp.out:
                lp.out.Reset()
                lp.out.LedSetMode(0)
        for out in self.out:
            out.close()
            out.abort()
        self.out = []


    def set_vis_light(self, x, y, state):
        """Set visualizer light to white on all connected devices, or restore to normal."""
        if state:
            # 1. LinnStrument (Color 8 is White)
            self.ls_color(x, y, 8)
            
            # 2. Launchpad (Index 3 is White)
            if 0 <= x < 8 and 0 <= y < 8:
                for lp in self.launchpads:
                    if self.options.launchpad_colors:
                        lp.out.LedCtrlXYByCode(x, y+1, 3)
                    else:
                        lp.out.LedCtrlXY(x, y+1, 63, 63, 63)
        else:
            # Restore to marked state if the user is physically holding the pad
            if self.mark_lights[y][x]:
                self.set_light(x, y, self.options.mark_light, mark=True)
            # Otherwise, reset the light back to the standard scale color
            else:
                self.reset_light(x, y)
