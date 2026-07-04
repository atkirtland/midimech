import copy
import os
import sys
import traceback

import glm
from glm import ivec2, ivec3, vec2, vec3

with open(os.devnull, "w") as devnull:
    # suppress pygame messages (to keep console output clean)
    stdout = sys.stdout
    sys.stdout = devnull
    import pygame, pygame.midi, pygame.gfxdraw

    sys.stdout = stdout
import pygame_gui

from src.constants import BORDER_COLOR, FONT_SZ, TITLE


class Object:
    def __init__(self, **kwargs):
        self.game = kwargs.get("game", None)
        self.attached = False
        if self.game:
            self.game.world.attach(self)

        self.pos = glm.vec2(*kwargs.get("pos", (0.0, 0.0)))
        self.vel = glm.vec2(*kwargs.get("vel", (0.0, 0.0)))
        self.sz = glm.vec2(*kwargs.get("sz", (0.0, 0.0)))
        self.surface = kwargs.get("surface", None)


class Screen(Object):
    def __init__(self, core, screen):
        self.core = core
        self.pos = glm.vec2(0.0, 0.0)
        self.sz = glm.vec2(core.screen_w, core.screen_h)
        self.surface = pygame.Surface(core.screen_sz).convert()
        self.screen = screen

    def render(self):
        self.screen.blit(self.surface, (0, 0))


class PygameFrontend:
    """Desktop display + input frontend: mirrors the grid on-screen, provides a menu button
    bar, and a keyboard/mouse fallback input path. Drives Core's main loop."""

    def __init__(self, core):
        self.core = core

        # simulator keys
        self.keys = {}
        i = 0
        for key in "1234567890-=":
            self.keys[ord(key)] = 62 + i
            i += 2
        self.keys[pygame.K_BACKSPACE] = 62 + i
        i = 0
        for key in "qwertyuiop[]\\":
            self.keys[ord(key)] = 57 + i
            i += 2
        i = 0
        for key in "asdfghjkl;'":
            self.keys[ord(key)] = 52 + i
            i += 2
        self.keys[pygame.K_RETURN] = 52 + i
        i = 0
        for key in "zxcvbnm,./":
            self.keys[ord(key)] = 47 + i
            i += 2
        self.keys[pygame.K_RSHIFT] = 47 + i

        pygame.init()
        pygame.display.set_caption(TITLE)
        self.icon = pygame.image.load('icon.png')
        pygame.display.set_icon(self.icon)
        if core.options.lite:
            self.screen = Screen(
                core, pygame.display.set_mode((256, 256), pygame.DOUBLEBUF)
            )
        else:
            self.screen = Screen(
                core, pygame.display.set_mode(core.screen_sz, pygame.DOUBLEBUF)
            )

        bs = ivec2(core.button_sz, core.panel_sz)  # // 2 double panel
        self.gui = pygame_gui.UIManager(core.screen_sz)
        y = 0
        self.btn_octave_down = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((2, y), bs), text="<OCT", manager=self.gui
        )
        self.btn_octave_up = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x + 2, y), bs), text="OCT>", manager=self.gui
        )
        self.btn_transpose_down = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 2 + 2, y), (bs.x, bs.y)),
            text='<TR',
            manager=self.gui
        )
        self.btn_transpose_up = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 3 + 2, y), (bs.x, bs.y)),
            text='TR>',
            manager=self.gui
        )
        self.btn_move_left = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 4 + 2, y), bs),
            text="<MOV",
            manager=self.gui,
        )
        self.btn_move_right = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 5 + 2, y), bs),
            text="MOV>",
            manager=self.gui,
        )
        self.btn_rotate = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 6 + 2, y), bs),
            text="ROT",
            manager=self.gui,
        )
        self.btn_flip = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 7 + 2, y), bs),
            text="FLIP",
            manager=self.gui,
        )

        self.btn_split = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 8 + 2, y), (bs.x * 2, bs.y)),
            text="SPLIT: " + ("ON" if core.split_state else "OFF"),
            manager=self.gui,
        )

        self.btn_mpe = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 10 + 2, y), (bs.x * 2, bs.y)),
            text="MPE: " + ("OFF" if core.options.one_channel else "ON"),
            manager=self.gui,
        )

        self.btn_prev_scale = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 12 + 2, y), (bs.x, bs.y)),
            text='<SCL',
            manager=self.gui
        )
        self.btn_next_scale = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 13 + 2, y), (bs.x, bs.y)),
            text='SCL>',
            manager=self.gui
        )

        self.btn_prev_mode = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 14 + 2, y), (bs.x, bs.y)),
            text='<MOD',
            manager=self.gui
        )
        self.btn_next_mode = pygame_gui.elements.UIButton(
            relative_rect=pygame.Rect((bs.x * 15 + 2, y), (bs.x, bs.y)),
            text='MOD>',
            manager=self.gui
        )

        self.font = pygame.font.Font(None, FONT_SZ)
        self.clock = pygame.time.Clock()

    def resize(self):
        core = self.core
        core.board_sz = ivec2(core.board_w, core.board_h)
        core.screen_w = core.board_w * core.scale.x
        core.screen_h = core.board_h * core.scale.y + core.menu_sz + core.status_sz
        core.button_sz = core.screen_w / core.board_w
        core.screen_sz = ivec2(core.screen_w, core.screen_h)
        self.screen = Screen(core, pygame.display.set_mode(core.screen_sz))
        core.dirty_lights = True

    def pump_events(self):
        core = self.core
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                core.quit()
                break
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    core.quit()
                elif ev.key == pygame.K_F1:
                    core.clear_marks(use_lights=True)
                    core.send_all_notes_off()
                else:
                    try:
                        n = self.keys[ev.key]
                        n -= 12
                        n += core.octave * 12
                        core.mark(n + core.vis_octave * 12, 1, True)
                        data = [0x90, n, 127]
                        if core.midi_out:
                            core.midi_write(core.midi_out, data, 0)
                    except KeyError:
                        pass
            elif ev.type == pygame.KEYUP:
                try:
                    n = self.keys[ev.key]
                    n -= 12
                    n += core.octave * 12
                    core.mark(n + core.vis_octave * 12, 0, True)
                    data = [0x80, n, 0]
                    if core.midi_out:
                        core.midi_write(core.midi_out, data, 0)
                except KeyError:
                    pass

            if not core.options.lite:
                if ev.type == pygame.MOUSEMOTION:
                    x, y = ev.pos
                    y -= core.menu_sz
                    core.mouse_hover(x, y, pygame.mouse.get_pressed(3)[0])
                elif ev.type == pygame.MOUSEBUTTONDOWN:
                    x, y = ev.pos
                    y -= core.menu_sz
                    if ev.button == 1:
                        core.mouse_press(x, y)
                    elif ev.button == 2:
                        core.mouse_release(x, y)
                    elif ev.button == 3:
                        core.mouse_hold(x, y)
                elif ev.type == pygame.MOUSEBUTTONUP:
                    core.mouse_release()
                elif ev.type == pygame_gui.UI_BUTTON_PRESSED:
                    if ev.ui_element == self.btn_octave_down:
                        core.octave -= 1
                        core.dirty = core.dirty_lights = True
                        core.clear_marks(use_lights=False)
                    elif ev.ui_element == self.btn_octave_up:
                        core.octave += 1
                        core.dirty = core.dirty_lights = True
                        core.clear_marks(use_lights=False)
                    elif ev.ui_element == self.btn_move_left:
                        core.move_board(-1)
                        core.clear_marks(use_lights=False)
                    elif ev.ui_element == self.btn_move_right:
                        core.move_board(1)
                        core.clear_marks(use_lights=False)
                    elif ev.ui_element == self.btn_rotate:
                        if core.rotated:
                            core.position.x += 3
                            core.rotated = False
                        else:
                            core.position.x -= 3
                            core.rotated = True
                        core.dirty = core.dirty_lights = True
                        core.clear_marks(use_lights=False)
                    elif ev.ui_element == self.btn_flip:
                        core.flipped = not core.flipped
                        core.dirty = core.dirty_lights = True
                        core.clear_marks(use_lights=False)
                    elif ev.ui_element == self.btn_split:
                        if core.split_out:
                            core.split_state = not core.split_state
                            self.btn_split.set_text(
                                "SPLIT: " + ("ON" if core.split_state else "OFF")
                            )
                            core.dirty = core.dirty_lights = True
                        else:
                            print("You need to add another MIDI loopback device called 'split'")
                    elif ev.ui_element == self.btn_mpe:
                        core.options.one_channel = 0 if core.options.one_channel else 1
                        self.btn_mpe.set_text(
                            "MPE: " + ("OFF" if core.options.one_channel else "ON")
                        )
                        core.midi_mode_rpn()
                        core.dirty = True
                    elif ev.ui_element == self.btn_transpose_down:
                        core.set_tonic(core.tonic - 1)
                    elif ev.ui_element == self.btn_transpose_up:
                        core.set_tonic(core.tonic + 1)
                    elif ev.ui_element == self.btn_next_scale:
                        core.next_scale()
                    elif ev.ui_element == self.btn_prev_scale:
                        core.prev_scale()
                    elif ev.ui_element == self.btn_next_mode:
                        core.next_mode()
                    elif ev.ui_element == self.btn_prev_mode:
                        core.prev_mode()

                self.gui.process_events(ev)

    def render(self):
        core = self.core
        if not core.dirty:
            return False

        if core.options.lite:
            self.screen.surface.blit(self.icon, (0, 0, 256, 256))
            return True

        core.dirty = False

        self.screen.surface.fill((0, 0, 0))
        b = 2  # border
        sz = core.screen_w / core.board_w
        y = 0
        rad = int(sz // 2 - 8)

        for row in core.board:
            x = 0
            for cell in row:
                note = core.get_note(x, y, True)

                split_chan = core.channel_from_split(x, y)

                lit_col = ivec3(255, 0, 0)
                unlit_col = copy.copy(core.get_color(x, y) or ivec3(0))
                black = unlit_col == ivec3(0)
                inner_col = copy.copy(unlit_col)
                for i in range(len(unlit_col)):
                    unlit_col[i] = min(255, unlit_col[i] * 1.5)

                ry = y + core.menu_sz  # real y
                rect = [x * sz + b, core.menu_sz + y * sz + b, sz - b, sz - b]
                inner_rect = [rect[0] + 4, rect[1] + 4, rect[2] - 8, rect[3] - 8]
                pygame.draw.rect(self.screen.surface, unlit_col, rect, border_radius=8)
                pygame.draw.rect(
                    self.screen.surface, inner_col, inner_rect, border_radius=8
                )
                if not black:
                    pygame.draw.rect(
                        self.screen.surface,
                        BORDER_COLOR,
                        rect,
                        width=2,
                        border_radius=8,
                    )
                else:
                    pygame.draw.rect(
                        self.screen.surface, vec3(24), rect, width=2, border_radius=8
                    )
                vis_cell = core.vis_board[y][x] if x < len(core.vis_board[y]) else 0

                if cell or vis_cell:
                    circ = ivec2(
                        int(x * sz + b / 2 + sz / 2),
                        int(core.menu_sz + y * sz + b / 2 + sz / 2),
                    )
                    if cell and vis_cell:  # Both are active (Purple)
                        circle_col = ivec3(150, 0, 255)
                        circle_inner_col = ivec3(100, 0, 200)
                        shadow_col = ivec3(0, 0, 0)
                    elif cell:  # Local input only (Red)
                        circle_col = ivec3(255, 0, 0)
                        circle_inner_col = ivec3(200, 0, 0)
                        shadow_col = ivec3(0, 0, 0)
                    elif vis_cell:  # Visualizer only (Blue)
                        circle_col = ivec3(0, 100, 255)
                        circle_inner_col = ivec3(0, 80, 200)
                        shadow_col = ivec3(0, 0, 0)

                    pygame.gfxdraw.aacircle(
                        self.screen.surface,
                        circ.x + 1,
                        circ.y - 1,
                        rad,
                        circle_col,
                    )
                    pygame.gfxdraw.filled_circle(
                        self.screen.surface,
                        circ.x + 1,
                        circ.y - 1,
                        rad,
                        circle_col,
                    )

                    pygame.gfxdraw.aacircle(
                        self.screen.surface, circ.x - 1, circ.y + 1, rad, shadow_col
                    )
                    pygame.gfxdraw.filled_circle(
                        self.screen.surface, circ.x - 1, circ.y + 1, rad, shadow_col
                    )

                    pygame.gfxdraw.filled_circle(
                        self.screen.surface, circ.x, circ.y, rad, circle_col
                    )
                    pygame.gfxdraw.aacircle(
                        self.screen.surface, circ.x, circ.y, rad, circle_col
                    )

                    pygame.gfxdraw.filled_circle(
                        self.screen.surface,
                        circ.x,
                        circ.y,
                        int(rad * 0.9),
                        circle_inner_col,
                    )
                    pygame.gfxdraw.aacircle(
                        self.screen.surface,
                        circ.x,
                        circ.y,
                        int(rad * 0.9),
                        circle_inner_col,
                    )

                text = self.font.render(note, True, (0, 0, 0))
                textpos = text.get_rect()
                textpos.x = x * sz + sz // 2 - FONT_SZ // 4
                textpos.y = core.menu_sz + y * sz + sz // 2 - FONT_SZ // 4
                textpos.x -= 1
                textpos.y += 1
                self.screen.surface.blit(text, textpos)

                text = self.font.render(note, True, ivec3(255))
                textpos = text.get_rect()
                textpos.x = x * sz + sz // 2 - FONT_SZ // 4
                textpos.y = core.menu_sz + y * sz + sz // 2 - FONT_SZ // 4
                textpos.x += 1
                textpos.y -= 1
                self.screen.surface.blit(text, textpos)

                text = self.font.render(note, True, ivec3(200))
                textpos = text.get_rect()
                textpos.x = x * sz + sz // 2 - FONT_SZ // 4
                textpos.y = core.menu_sz + y * sz + sz // 2 - FONT_SZ // 4
                self.screen.surface.blit(text, textpos)

                x += 1
            y += 1

        text = self.font.render(core.scale_name, True, ivec3(127))
        textpos = text.get_rect()
        textpos.x = core.screen_w * 1 / 4 - textpos[2] / 2
        textpos.y = core.screen_h - core.status_sz * 3 / 4
        self.screen.surface.blit(text, textpos)

        text = self.font.render(core.mode_name, True, ivec3(127))
        textpos = text.get_rect()
        textpos.x = core.screen_w * 2 / 4 - textpos[2] / 2
        textpos.y = core.screen_h - core.status_sz * 3 / 4
        self.screen.surface.blit(text, textpos)

        chord = core.chord or '-'
        text = self.font.render(chord, True, ivec3(127))
        textpos = text.get_rect()
        textpos.x = core.screen_w * 3 / 4 - textpos[2] / 2
        textpos.y = core.screen_h - core.status_sz * 3 / 4
        self.screen.surface.blit(text, textpos)

        return True

    def draw(self):
        self.gui.draw_ui(self.screen.surface)
        self.screen.render()
        pygame.display.flip()

    def run(self):
        core = self.core
        try:
            core.done = False
            while not core.done:
                try:
                    dt = self.clock.tick(core.options.fps) / 1000.0
                except:
                    core.deinit()
                    break
                core.poll_launchpads()
                self.pump_events()
                core.logic(dt)
                if not core.options.lite:
                    self.gui.update(dt)
                if core.done:
                    break
                self.render()
                self.draw()
        except:
            print(traceback.format_exc())

        core.deinit()

        return 0
