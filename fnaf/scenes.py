"""Game screens: loading, menu, the night itself, 6 AM, game over, extras."""

import math
import random

import pygame

from . import hud
from .effects import CamGlitch, draw_tablet, tinted
from .logic import NightState
from .settings import (CAM_ORDER, CAM_PAN_MAX, CHARACTERS, OFFICE_SCROLL_MAX,
                       SCREEN_H, SCREEN_W)
from .util import add_glows, draw_text, font

ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}

# The phone guy's messages (subtitles). Written for this remake.
PHONE_CALLS = {
    1: [
        "Hello? Hello, hello! Uh, welcome to your first night shift.",
        "I'm supposed to walk you through a couple of things. Won't take long.",
        "The animatronic characters get a little... free-roaming at night.",
        "Something about their servos locking up if they stand still too long.",
        "Problem is, after hours they don't recognise you as a person.",
        "They'll assume you're an empty costume and try to, uh, help you into one.",
        "Which would hurt. A lot. Anyway!",
        "Watch them on the cameras - the bar at the bottom of your screen.",
        "If one shows up at your door, shut it. The door lights let you peek.",
        "But doors, lights and cameras all eat power, and power is limited.",
        "So, uh, don't waste it. Okay! Make it to six. Talk to you tomorrow.",
    ],
    2: [
        "Hey, you made it! Knew you would. Mostly.",
        "Heads up: Bonnie and Chica get a lot more active from here on.",
        "They like to stand right outside your doors, where the cameras can't see.",
        "If you hear footsteps, check your door lights.",
        "Also - keep an eye on Pirate Cove. Foxy gets restless when nobody watches.",
        "If he leaves the cove, close that left door. Fast.",
        "Oh, and if your door buttons ever stop working... someone's in the room with you.",
        "Don't touch the monitor. Just... sit very still until six.",
    ],
    3: [
        "Night three! You're basically a veteran at this point.",
        "Freddy himself starts moving tonight. He likes the dark.",
        "He won't move while you're looking right at him on the camera.",
        "And you'll hear him laugh when he does move. Check the east hall.",
        "Okay. I'll let you get to it.",
    ],
    4: [
        "Hey, hey! Hey - if you're hearing this... I had a rough night.",
        "I'm kind of glad I recorded this ahead of time, actually.",
        "Could you do me a favour? Maybe check inside those suits sometime?",
        "I'll try to hold out until someone... wait. Is that the door?",
        "*knocking* ... *a music box starts playing*",
        "Oh no-  *static*",
    ],
    5: [
        "*garbled, reversed voices*",
        "*a low, mechanical moan*",
        "*static* ...IT'S ME... *static*",
    ],
}


def _ordinal(n):
    return ORDINALS.get(n, "%dth" % n)


class Scene:
    def __init__(self, game):
        self.game = game
        self.assets = game.assets
        self.sounds = game.assets.sounds

    def handle(self, event):
        pass

    def update(self, dt):
        pass

    def draw(self, screen):
        pass

    def static(self, screen, alpha, t=None):
        frames = self.assets.noise
        if not frames or alpha <= 0:
            return
        f = frames[random.randrange(len(frames))]
        f.set_alpha(int(max(0, min(255, alpha))))
        screen.blit(f, (0, 0))


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

class LoadingScene(Scene):
    def __init__(self, game, after):
        super().__init__(game)
        self.after = after
        self.gen = self.assets.build()
        self.progress = 0.0
        self.label = "Loading"

    def update(self, dt):
        start = pygame.time.get_ticks()
        while pygame.time.get_ticks() - start < 30:
            try:
                self.progress, self.label = next(self.gen)
            except StopIteration:
                self.game.change(self.after())
                return

    def draw(self, screen):
        screen.fill((0, 0, 0))
        self.static(screen, 22)
        w = 520
        x, y = (SCREEN_W - w) // 2, SCREEN_H // 2 + 30
        draw_text(screen, "Five Nights at Freddy's", (SCREEN_W // 2, y - 90), 54, (230, 230, 230),
                  anchor="center", bold=True)
        pygame.draw.rect(screen, (90, 90, 90), (x, y, w, 18), 2)
        pygame.draw.rect(screen, (220, 220, 220), (x + 4, y + 4, int((w - 8) * self.progress), 10))
        draw_text(screen, self.label + "...", (SCREEN_W // 2, y + 46), 24, (150, 150, 150), anchor="center")


# --------------------------------------------------------------------------
# Menu
# --------------------------------------------------------------------------

class MenuScene(Scene):
    def __init__(self, game):
        super().__init__(game)
        self.t = 0.0
        self.sel = 0
        self.glitch = 0.0
        self.glitch_face = 0
        self.next_glitch = random.uniform(1.0, 4.0)
        self.scan_y = 0.0
        self.sounds.stop_all()
        self.sounds.loop("music", "menu", 0.6)
        self.sounds.loop("static", "static", 0.08)
        self.items = []
        self.rebuild()

    def rebuild(self):
        save = self.game.save
        self.items = [("New Game", "new")]
        self.items.append(("Continue", "continue"))
        if save.get("beat5"):
            self.items.append(("6th Night", "night6"))
        if save.get("beat6"):
            self.items.append(("Custom Night", "custom"))
        self.items.append(("Quit", "quit"))
        self.rects = []

    def activate(self, action):
        g = self.game
        self.sounds.play("blip", 0.6)
        if action == "new":
            g.save["night"] = 1
            g.write_save()
            g.change(NewspaperScene(g))
        elif action == "continue":
            g.change(NightIntroScene(g, g.save.get("night", 1)))
        elif action == "night6":
            g.change(NightIntroScene(g, 6))
        elif action == "custom":
            g.change(CustomNightScene(g))
        elif action == "quit":
            g.running = False

    def handle(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_UP, pygame.K_w):
                self.sel = (self.sel - 1) % len(self.items)
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.sel = (self.sel + 1) % len(self.items)
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_KP_ENTER):
                self.activate(self.items[self.sel][1])
            elif event.key == pygame.K_ESCAPE:
                self.game.running = False
        elif event.type == pygame.MOUSEMOTION:
            for i, r in enumerate(self.rects):
                if r.collidepoint(event.pos):
                    self.sel = i
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, r in enumerate(self.rects):
                if r.collidepoint(event.pos):
                    self.activate(self.items[i][1])

    def update(self, dt):
        self.t += dt
        self.scan_y = (self.scan_y + dt * 140) % (SCREEN_H + 200)
        self.next_glitch -= dt
        if self.glitch > 0:
            self.glitch -= dt
        elif self.next_glitch <= 0:
            self.glitch = random.uniform(0.06, 0.2)
            self.glitch_face = random.randrange(1, len(self.assets.menu_faces))
            self.next_glitch = random.uniform(1.5, 5.0)

    def draw(self, screen):
        screen.fill((0, 0, 0))
        faces = self.assets.menu_faces
        if faces:
            face = faces[self.glitch_face if self.glitch > 0 else 0]
            jitter = (random.randint(-6, 6), random.randint(-3, 3)) if self.glitch > 0 else (0, 0)
            flick = 0.8 + 0.2 * math.sin(self.t * 1.3) + random.uniform(-0.08, 0.08)
            img = face.surface.copy()
            k = int(255 * max(0.2, min(1.0, flick)))
            img.fill((k, k, k), special_flags=pygame.BLEND_RGB_MULT)
            x = SCREEN_W - 380 - face.anchor[0] + jitter[0]
            y = SCREEN_H // 2 + 30 - face.anchor[1] + jitter[1]
            screen.blit(img, (x, y))
            if self.glitch > 0:
                add_glows(screen, face.glows, 1.0, (x, y))
        self.static(screen, 46 + 30 * math.sin(self.t * 0.7) + (90 if self.glitch > 0 else 0))
        # Rolling scan band.
        band = pygame.Surface((SCREEN_W, 90), pygame.SRCALPHA)
        band.fill((255, 255, 255, 10))
        screen.blit(band, (0, int(self.scan_y) - 150))
        screen.blit(self.assets.scanlines, (0, 0))

        y = 70
        for word in ("Five", "Nights", "at", "Freddy's"):
            draw_text(screen, word, (90, y), 84, (238, 238, 238), bold=True)
            y += 66
        stars = self.game.save.get("stars", 0)
        for i in range(stars):
            _star(screen, 112 + i * 56, y + 30, 22)

        self.rects = []
        y = 420
        for i, (label, action) in enumerate(self.items):
            r = draw_text(screen, label, (130, y), 44, (240, 240, 240), bold=True)
            if action == "continue":
                draw_text(screen, "Night %d" % self.game.save.get("night", 1), (r.right + 18, y + 12), 26,
                          (190, 190, 190))
            if i == self.sel:
                draw_text(screen, ">>", (80, y), 44, (240, 240, 240), bold=True)
            self.rects.append(r.inflate(260, 8).move(110, 0))
            y += 50
        draw_text(screen, "Fan remake made with pygame. Characters (c) Scott Cawthon.",
                  (SCREEN_W - 18, SCREEN_H - 14), 20, (110, 110, 110), anchor="bottomright")
        draw_text(screen, "F11: fullscreen", (18, SCREEN_H - 14), 20, (110, 110, 110), anchor="bottomleft")


def _star(screen, x, y, r):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((x + math.cos(a) * rr, y + math.sin(a) * rr))
    pygame.draw.polygon(screen, (240, 240, 240), pts)


class NewspaperScene(Scene):
    """The help-wanted ad shown when starting a new game."""

    def __init__(self, game):
        super().__init__(game)
        self.t = 0.0

    def handle(self, event):
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN) and self.t > 0.5:
            self.t = max(self.t, 6.5)

    def update(self, dt):
        self.t += dt
        if self.t > 7.5:
            self.game.change(NightIntroScene(self.game, 1))

    def draw(self, screen):
        screen.blit(self.assets.screen_image("newspaper"), (0, 0))
        self.static(screen, 18)
        if self.t > 6.5:
            fade = 255 * min(1.0, (self.t - 6.5) / 1.0)
        elif self.t < 0.8:
            fade = 255 * (1 - self.t / 0.8)
        else:
            fade = 0
        if fade > 0:
            veil = pygame.Surface((SCREEN_W, SCREEN_H))
            veil.set_alpha(int(fade))
            screen.blit(veil, (0, 0))


class NightIntroScene(Scene):
    def __init__(self, game, night, ai_levels=None):
        super().__init__(game)
        self.night = night
        self.ai_levels = ai_levels
        self.t = 0.0
        self.sounds.stop_all()
        self.sounds.play("deep", 0.7)

    def handle(self, event):
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN) and self.t > 0.6:
            self.t = max(self.t, 2.6)

    def update(self, dt):
        self.t += dt
        if self.t >= 3.4:
            self.game.change(NightScene(self.game, self.night, self.ai_levels))

    def draw(self, screen):
        screen.fill((0, 0, 0))
        a = 255
        if self.t < 0.4:
            a = int(255 * self.t / 0.4)
        elif self.t > 2.6:
            a = int(255 * max(0.0, 1 - (self.t - 2.6) / 0.8))
        draw_text(screen, "12:00 AM", (SCREEN_W // 2, SCREEN_H // 2 - 34), 72, (240, 240, 240),
                  anchor="center", bold=True, alpha=a)
        label = "Custom Night" if self.night == 7 else _ordinal(self.night) + " Night"
        draw_text(screen, label, (SCREEN_W // 2, SCREEN_H // 2 + 34), 52, (240, 240, 240),
                  anchor="center", bold=True, alpha=a)
        if self.t < 0.5:
            self.static(screen, 200 * (1 - self.t / 0.5))


# --------------------------------------------------------------------------
# The night
# --------------------------------------------------------------------------

JUMPSCARE_TIME = 1.4

# Where a room sounds like it is from the office: (stereo pan -1..1, loudness).
ROOM_AUDIO = {
    "1A": (0.0, 0.22), "1B": (0.0, 0.3), "5": (-0.6, 0.28), "1C": (-0.4, 0.32),
    "7": (0.6, 0.3), "6": (0.5, 0.35), "2A": (-0.5, 0.5), "3": (-0.65, 0.62),
    "2B": (-0.8, 0.78), "LDOOR": (-0.92, 1.0), "4A": (0.5, 0.5), "4B": (0.8, 0.78),
    "RDOOR": (0.92, 1.0), "OFFICE": (0.0, 1.0),
}
SIDE_PAN = {"L": -0.85, "R": 0.85}


class NightScene(Scene):
    def __init__(self, game, night, ai_levels=None):
        super().__init__(game)
        self.night = night
        self.st = NightState(night, ai_levels)
        self.speed = game.args.speed if game.args else 1.0
        self.t = 0.0
        self.scroll = OFFICE_SCROLL_MAX / 2
        self.cam_anim = 0.0      # monitor raise animation 0 (down) .. 1 (up)
        self.cam_target = False
        self.door_pos = {"L": 0.0, "R": 0.0}
        self.pan = 0.0
        self.pan_dir = 1
        self.pan_hold = 1.0
        self.glitch = 0.0
        self.switch_static = 0.0
        self.bar_armed = True
        self.flicker = False
        self.freddy_lit = False
        self.blink_timer = 0.0
        self.sting_seen = {"L": False, "R": False}
        self.state = "play"
        self.js_t = 0.0
        self.paused = False
        self.fade_in = 1.0
        self.call = list(PHONE_CALLS.get(night, [])) if night <= 5 else []
        self.call_t = -3.0
        self.call_line = -1
        self.call_line_t = 0.0
        self.call_muted = False
        self.mute_rect = pygame.Rect(30, 30, 150, 40)
        self.view = pygame.Surface((SCREEN_W, SCREEN_H)).convert()
        self.cam_fx = CamGlitch()
        self.js_ghosts = {}
        self.js_bg = None
        self.halluc = 0.0
        self.halluc_face = None
        self.itsme = None          # (cam, seconds left)
        self.sounds.stop_all()
        self.sounds.loop("fan", "fan", 0.35)
        self.sounds.loop("ambience", "ambience", 0.4)
        if self.call:
            self.sounds.play("ring", 0.6)

    # -- input ---------------------------------------------------------------
    def handle(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE and self.state == "play":
            self.paused = not self.paused
            if self.paused:
                self.sounds.pause()
            else:
                self.sounds.unpause()
            return
        if self.paused:
            if event.type == pygame.KEYDOWN and event.key == pygame.K_q:
                self.sounds.unpause()
                self.game.change(MenuScene(self.game))
            return
        if self.state != "play":
            return
        st = self.st
        if event.type == pygame.KEYDOWN:
            k = event.key
            if k in (pygame.K_SPACE, pygame.K_s, pygame.K_TAB):
                self.toggle_cams()
            elif k == pygame.K_q:
                self.door("L")
            elif k == pygame.K_e:
                self.door("R")
            elif k == pygame.K_a:
                self.light("L")
            elif k == pygame.K_d:
                self.light("R")
            elif k in (pygame.K_LEFT, pygame.K_RIGHT) and self.cam_target and self.cam_anim >= 1:
                i = CAM_ORDER.index(st.cam) + (1 if k == pygame.K_RIGHT else -1)
                self.switch_cam(CAM_ORDER[i % len(CAM_ORDER)])
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.call_line < len(self.call) and not self.call_muted and self.mute_rect.collidepoint(event.pos) \
                    and self.call_t > -3:
                self.call_muted = True
                self.sounds.play("click", 0.6)
                return
            if self.cam_target and self.cam_anim >= 1:
                for cam, r in hud.map_buttons().items():
                    if r.collidepoint(event.pos):
                        self.switch_cam(cam)
                        return
            elif not self.cam_target and self.cam_anim <= 0:
                ux, uy = self.assets.warp.unwarp(event.pos)
                ox, oy = ux + self.scroll, uy
                for (side, kind), r in self.office_mod.BUTTONS.items():
                    if r.collidepoint(ox, oy):
                        if kind == "door":
                            self.door(side)
                        else:
                            self.light(side)
                        return
                if self.office_mod.NOSE_RECT.collidepoint(ox, oy):
                    self.sounds.play("honk", 0.8)

    @property
    def office_mod(self):
        return self.assets.office_mod

    def door(self, side):
        if self.cam_target or self.cam_anim > 0:
            return
        if self.st.toggle_door(side):
            self.sounds.play("door", 0.8, pan=SIDE_PAN[side] * 0.7)
        else:
            self.sounds.play("error", 0.6, pan=SIDE_PAN[side] * 0.5)

    def light(self, side):
        if self.cam_target or self.cam_anim > 0:
            return
        if not self.st.toggle_light(side):
            self.sounds.play("error", 0.6)
        else:
            self.sounds.play("click", 0.4)

    def toggle_cams(self):
        st = self.st
        if st.power_out or self.state != "play":
            return
        self.cam_target = not self.cam_target
        if self.cam_target:
            self.sounds.play("cam_up", 0.6)
        else:
            self.sounds.play("cam_down", 0.6)
            st.set_cams(False)

    def switch_cam(self, cam):
        if cam == self.st.cam:
            return
        self.st.set_cam(cam)
        self.sounds.play("blip", 0.55)
        self.cam_fx.kick(4)
        self.switch_static = 0.22
        self.pan = 0.0
        self.pan_dir = 1
        self.pan_hold = 0.8

    # -- update --------------------------------------------------------------
    def update(self, dt):
        if self.paused:
            return
        self.t += dt
        self.fade_in = max(0.0, self.fade_in - dt * 1.2)
        st = self.st
        if self.state == "jumpscare":
            self.js_t += dt
            limit = 2.4 if st.killer == "Golden" else JUMPSCARE_TIME
            if self.js_t >= limit:
                self.sounds.stop_all()
                if st.killer == "Golden":
                    self.game.change(CrashScene(self.game))
                else:
                    self.game.change(GameOverScene(self.game, self.night, st.killer))
            return

        self._update_input(dt)

        st.update(dt * self.speed)
        for name, data in st.pop_events():
            self._on_event(name, data)
        if self.state != "play":
            return
        if st.result == "win":
            self.sounds.stop_all()
            self.game.change(SixAMScene(self.game, self.night, self.st.custom and self.st))
            return

        # Monitor animation.
        if st.power_out:
            self.cam_target = False
        target = 1.0 if self.cam_target else 0.0
        if self.cam_anim != target:
            step = dt / 0.26
            self.cam_anim = min(target, self.cam_anim + step) if target > self.cam_anim else max(target, self.cam_anim - step)
            if self.cam_anim >= 1.0 and self.cam_target:
                st.set_cams(True)
                self.switch_static = 0.25
        for side in "LR":
            goal = 1.0 if st.doors[side] else 0.0
            p = self.door_pos[side]
            self.door_pos[side] = min(goal, p + dt * 5.5) if goal > p else max(goal, p - dt * 5.5)

        # Camera pan.
        if self.pan_hold > 0:
            self.pan_hold -= dt
        else:
            self.pan += self.pan_dir * dt * 38
            if self.pan >= CAM_PAN_MAX or self.pan <= 0:
                self.pan = max(0, min(CAM_PAN_MAX, self.pan))
                self.pan_dir *= -1
                self.pan_hold = 1.4
        self.glitch = max(0.0, self.glitch - dt)
        self.switch_static = max(0.0, self.switch_static - dt)
        if self.cam_anim >= 1:
            self.cam_fx.update(dt)

        # Door lights.
        any_light = st.lights["L"] or st.lights["R"]
        self.flicker = any_light and random.random() < (0.12 if (st.at_door("L") or st.at_door("R")) else 0.04)
        if any_light:
            self.sounds.loop("buzz", "buzz", 0.0 if self.flicker else 0.45)
        else:
            self.sounds.stop("buzz")
        for side in "LR":
            who = st.at_door(side)
            if not who:
                self.sting_seen[side] = False
            elif st.lights[side] and not self.sting_seen[side]:
                self.sting_seen[side] = True
                self.sounds.play("sting", 0.9, pan=SIDE_PAN[side] * 0.4)

        # Loops tied to the cameras.
        if self.cam_anim > 0.5 and not st.power_out:
            self.sounds.loop("static", "static", 0.5 if self.glitch > 0 else 0.16)
        else:
            self.sounds.stop("static")
        kitchen = st.occupants("6") if not st.power_out else frozenset()
        on_cam6 = st.cams_up and st.cam == "6"
        if "Chica" in kitchen:
            self.sounds.loop("kitchen", "kitchen", 0.8 if on_cam6 else 0.12)
        else:
            self.sounds.stop("kitchen", 300)
        if "Freddy" in kitchen:
            self.sounds.loop("musicbox", "musicbox", 0.55 if on_cam6 else 0.06)
        elif not st.power_out:
            self.sounds.stop("musicbox", 400)

        self._update_hallucinations(dt)

        # Power-out show: Freddy's face blinks to the music box.
        if st.power_out and st.power_out["phase"] == "show":
            self.blink_timer -= dt
            if self.blink_timer <= 0:
                self.freddy_lit = not self.freddy_lit
                self.blink_timer = random.uniform(0.05, 0.35) if self.freddy_lit else random.uniform(0.05, 0.5)
        else:
            self.freddy_lit = False

        # Phone call subtitles.
        if self.call and not self.call_muted:
            self.call_t += dt
            if self.call_t >= 0:
                if self.call_line < 0:
                    self.call_line = 0
                    self.call_line_t = 0.0
                else:
                    self.call_line_t += dt
                    if self.call_line < len(self.call) and self.call_line_t > 1.6 + 0.055 * len(self.call[self.call_line]):
                        self.call_line += 1
                        self.call_line_t = 0.0
        talking = (self.call and not self.call_muted and 0 <= self.call_line < len(self.call)
                   and not st.power_out)
        if talking:
            self.sounds.loop("voice", "garble" if self.night == 5 else "voice", 0.5)
        else:
            self.sounds.stop("voice", 250)

    def _update_hallucinations(self, dt):
        if self.halluc > 0:
            self.halluc -= dt
        if self.itsme:
            left = self.itsme[1] - dt
            self.itsme = (self.itsme[0], left) if left > 0 else None

    def _hallucinate(self, kind, on_cams):
        """Rare flashes like the original's: faces, and 'IT'S ME'."""
        if self.st.power_out:
            return
        if on_cams and self.cam_anim >= 1:
            self.itsme = (self.st.cam, random.uniform(1.5, 3.0))
        elif kind == "faces":
            self.halluc = random.uniform(0.06, 0.14)
            self.halluc_face = random.choice(["Golden", "Freddy", "Bonnie"])
            self.sounds.play("static_burst", 0.35)
        else:
            self.halluc = random.uniform(0.25, 0.45)
            self.halluc_face = None

    def _update_input(self, dt):
        st = self.st
        mx, my = pygame.mouse.get_pos()
        if pygame.mouse.get_focused():
            on_bar = hud.CAM_BAR.collidepoint(mx, my)
            if on_bar and self.bar_armed and not st.power_out:
                self.bar_armed = False
                self.toggle_cams()
            elif not on_bar:
                self.bar_armed = True
        if self.cam_target or self.cam_anim > 0:
            return
        speed = 0.0
        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT]:
            speed = -1.0
        elif keys[pygame.K_RIGHT]:
            speed = 1.0
        elif pygame.mouse.get_focused():
            edge = SCREEN_W * 0.32
            if mx < edge:
                speed = -(edge - mx) / edge
            elif mx > SCREEN_W - edge:
                speed = (mx - (SCREEN_W - edge)) / edge
        self.scroll = max(0.0, min(float(OFFICE_SCROLL_MAX), self.scroll + speed * 1000 * dt))

    def _on_event(self, name, data):
        st = self.st
        if name == "move":
            who, src, dst = data["who"], data["src"], data["dst"]
            if who in ("Bonnie", "Chica") and st.cams_up and st.cam in (src, dst):
                self.glitch = random.uniform(1.2, 2.6)
                self.cam_fx.kick(6)
                self.sounds.play("static_burst", 0.5)
            if who in ("Bonnie", "Chica"):
                # Footsteps you can place by ear: louder and more to one side
                # the closer they get.
                near = dst if dst in ROOM_AUDIO else src
                pan, gain = ROOM_AUDIO.get(near, (0.0, 0.3))
                if src in ("LDOOR", "RDOOR"):
                    pan, gain = ROOM_AUDIO[src][0], 0.55
                if gain >= 0.45:
                    self.sounds.play("footsteps", 0.85 * gain, pan=pan)
        elif name == "laugh":
            pan, gain = ROOM_AUDIO.get(st.freddy.loc, (0.0, 0.5))
            self.sounds.play("laugh", 0.35 + 0.5 * gain, pan=pan * 0.8)
        elif name == "breathing":
            # Lifting the monitor with someone already in the room.
            self.sounds.play("groan", 0.55, pan=SIDE_PAN["L" if data.get("who") == "Bonnie" else "R"] * 0.3)
        elif name == "hallucination":
            self._hallucinate(data.get("kind"), data.get("cams"))
        elif name == "foxy_run":
            self.sounds.play("run", 0.9 if (st.cams_up and st.cam == "2A") else 0.65, pan=-0.6)
        elif name == "foxy_bang":
            self.sounds.play("bang", 0.95, pan=-0.85)
        elif name == "golden":
            self.sounds.play("deep", 0.6)
        elif name == "power_out":
            self.sounds.stop_all()
            self.sounds.play("powerdown", 0.9)
            for side in "LR":
                if self.door_pos[side] > 0.5:
                    self.sounds.play("door", 0.7, pan=SIDE_PAN[side] * 0.7)
            if self.cam_anim > 0:
                self.sounds.play("cam_down", 0.6)
            self.cam_target = False
            self.cam_anim = 0.0
        elif name == "musicbox":
            self.sounds.loop("musicbox", "musicbox", 0.7)
        elif name == "blackout":
            self.sounds.stop("musicbox")
            self.sounds.play("deep", 0.5)
        elif name == "kill":
            self.start_jumpscare(data["who"])

    def start_jumpscare(self, who):
        self.state = "jumpscare"
        self.js_t = 0.0
        self.cam_target = False
        self.cam_anim = 0.0
        self.st.cams_up = False
        self.sounds.stop_all()
        self.sounds.play("scream_gf" if who == "Golden" else "scream", 1.0)

    # -- draw ----------------------------------------------------------------
    def draw(self, screen):
        st = self.st
        if self.state == "jumpscare":
            self.draw_jumpscare(screen)
            return

        cams_visible = self.cam_anim >= 1.0
        if not cams_visible:
            self.draw_office(screen)
            tab = draw_tablet(screen, self.assets.tablet, self.cam_anim)
            if tab is not None and self.cam_anim > 0.45:
                # The screen flickers on as the tablet comes up.
                screen.set_clip(tab.clip(screen.get_rect()))
                self.static(screen, int(110 * (self.cam_anim - 0.45) / 0.55))
                screen.set_clip(None)
        else:
            self.draw_cams(screen)

        if not (st.power_out and st.power_out["phase"] == "black"):
            if not st.power_out:
                hud.draw_clock(screen, st.hour_label, self.night)
                hud.draw_power(screen, st.power, st.usage)
                hud.draw_cam_bar(screen, hud.CAM_BAR.collidepoint(pygame.mouse.get_pos()))
            else:
                hud.draw_clock(screen, st.hour_label, self.night)
        if self.halluc > 0 and not cams_visible:
            frames = self.assets.jumpscares.get(self.halluc_face) if self.halluc_face else None
            if frames:
                img = frames[0].surface
                jx, jy = random.randint(-30, 30), random.randint(-20, 20)
                screen.blit(img, (SCREEN_W // 2 - frames[0].anchor[0] + jx, SCREEN_H // 2 - frames[0].anchor[1] + jy))
                self.static(screen, 120)
            elif random.random() < 0.8:
                draw_text(screen, "IT'S ME", (SCREEN_W // 2 + random.randint(-4, 4), SCREEN_H // 2 - 80), 120,
                          (225, 220, 215), anchor="center", alpha=random.randint(120, 200))
        self.draw_call(screen)
        if self.night == 1 and self.t < 24 and not st.power_out:
            a = int(255 * min(1.0, (self.t - 1.0) / 0.6, (24 - self.t) / 1.5)) if self.t > 1.0 else 0
            if a > 0:
                draw_text(screen, "Cameras: hover the bar or SPACE    Doors: Q / E    Lights: A / D    Look: mouse",
                          (SCREEN_W // 2, 194), 22, (200, 200, 200), anchor="center", alpha=a,
                          shadow=(0, 0, 0))

        if self.fade_in > 0:
            veil = pygame.Surface((SCREEN_W, SCREEN_H))
            veil.set_alpha(int(255 * self.fade_in))
            screen.blit(veil, (0, 0))
        if self.paused:
            veil = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 170))
            screen.blit(veil, (0, 0))
            draw_text(screen, "PAUSED", (SCREEN_W // 2, SCREEN_H // 2 - 30), 80, (240, 240, 240), anchor="center",
                      bold=True)
            draw_text(screen, "Esc: resume     Q: quit to menu", (SCREEN_W // 2, SCREEN_H // 2 + 40), 30,
                      (200, 200, 200), anchor="center")

    def draw_office(self, screen):
        st = self.st
        phase = st.power_out["phase"] if st.power_out else None
        self.assets.office.draw(
            self.view, int(self.scroll), t=self.t,
            door_pos=self.door_pos,
            door_closed=dict(st.doors),
            lights=dict(st.lights),
            at_door={"L": st.at_door("L"), "R": st.at_door("R")},
            power_out=phase,
            freddy_lit=self.freddy_lit,
            golden=(st.golden == "office"),
            flicker=self.flicker,
        )
        self.assets.warp.apply(self.view, screen)
        screen.blit(self.assets.vignette, (0, 0), special_flags=pygame.BLEND_RGB_MULT)

    def draw_cams(self, screen):
        st = self.st
        cam = st.cam
        if cam == "6" or self.glitch > 0:
            screen.fill((0, 0, 0))
        else:
            occ = st.occupants(cam)
            foxy_stage = st.foxy.stage if st.foxy.state == "cove" else 3
            img = self.assets.feeds.get(cam, occ,
                                        foxy_stage=foxy_stage if cam == "1C" else 0,
                                        golden_poster=(cam == "2B" and st.golden == "poster"),
                                        freddy_stare=(cam == "1A" and occ == frozenset(["Freddy"])))
            screen.blit(img, (-int(self.pan), 0))
            if cam == "2A" and st.foxy.state == "running":
                self.assets.feeds.draw_foxy_run(screen, -int(self.pan), st.foxy.run_progress)
        if self.glitch > 0:
            self.static(screen, 235)
        elif cam == "6":
            self.static(screen, 70)
            draw_text(screen, "-CAMERA DISABLED-", (SCREEN_W // 2 - 120, 220), 44, (235, 235, 235), anchor="center",
                      bold=True)
            draw_text(screen, "AUDIO ONLY", (SCREEN_W // 2 - 120, 268), 36, (235, 235, 235), anchor="center",
                      bold=True)
        else:
            self.static(screen, 255 if self.switch_static > 0.1 else 110 if self.switch_static > 0 else
                        random.randint(16, 32))
        if self.itsme and self.itsme[0] == cam and self.glitch <= 0:
            draw_text(screen, "IT'S ME", (SCREEN_W // 2 + random.randint(-3, 3), SCREEN_H // 2 - 60), 110,
                      (210, 210, 205), anchor="center", alpha=random.randint(150, 220))
        self.cam_fx.draw(screen)
        screen.blit(self.assets.scanlines, (0, 0))
        hud.draw_cam_frame(screen, cam, self.t)
        hud.draw_map(screen, cam, self.t)

    def draw_call(self, screen):
        if not self.call or self.call_muted or self.call_line >= len(self.call):
            return
        if self.call_t > -3:
            r = self.mute_rect
            pygame.draw.rect(screen, (30, 30, 30), r)
            pygame.draw.rect(screen, (200, 200, 200), r, 2)
            draw_text(screen, "MUTE CALL", r.center, 24, (230, 230, 230), anchor="center", bold=True)
        if self.call_line >= 0:
            line = self.call[self.call_line]
            a = int(255 * min(1.0, self.call_line_t / 0.25))
            draw_text(screen, line, (SCREEN_W // 2, 150), 28, (235, 235, 210), anchor="center",
                      alpha=a, shadow=(0, 0, 0))

    # Where each killer lunges in from (screen x at the start of the lunge).
    JS_FROM = {"Bonnie": 330, "Chica": 950, "Freddy": 800, "Foxy": -260, "Golden": SCREEN_W // 2}

    def _scaled(self, frame, scale):
        """Jumpscare frame at a quantised scale (cached: no per-frame rescaling)."""
        q = round(scale * 20) / 20.0
        key = (id(frame), q)
        img = self.js_ghosts.get(key)
        if img is None:
            src = frame.surface
            img = src if q == 1.0 else pygame.transform.scale(
                src, (int(src.get_width() * q), int(src.get_height() * q)))
            self.js_ghosts[key] = img
        return img, q

    def _ghost(self, surface, color):
        key = (id(surface), color)
        g = self.js_ghosts.get(key)
        if g is None:
            g = self.js_ghosts[key] = tinted(surface, color)
        return g

    def draw_jumpscare(self, screen):
        st = self.st
        who = st.killer
        t = self.js_t
        frames = self.assets.jumpscares.get(who)
        power_out = st.power_out is not None

        # Background: a darkened snapshot of the office (or darkness), shaking with the hit.
        if power_out or who == "Golden":
            screen.fill((0, 0, 0))
        else:
            if self.js_bg is None:
                self.draw_office(screen)
                screen.fill((105, 84, 84), special_flags=pygame.BLEND_RGB_MULT)
                self.js_bg = screen.copy()
            screen.blit(self.js_bg, (0, 0))
        if not frames:
            return

        if who == "Golden":
            frame = frames[0]
            pulse = 1.0 + 0.015 * math.sin(t * 50)
            self._blit_scaled(screen, frame, SCREEN_W // 2, SCREEN_H // 2, pulse)
            self.static(screen, 35 + 30 * abs(math.sin(t * 23)))
            return

        lunge = 0.2 if who == "Foxy" else 0.09
        k = min(1.0, t / lunge)
        e = 1 - (1 - k) ** 3
        start_x = 300 if (who == "Freddy" and power_out) else self.JS_FROM.get(who, SCREEN_W // 2)
        cx = start_x + (SCREEN_W // 2 - start_x) * e
        cy = SCREEN_H * 0.47 + (1 - e) * 150
        if who == "Foxy":
            scale = 0.4 + 0.8 * e
        else:
            scale = 0.75 + 0.45 * e
        if k >= 1:
            # Thrashing: jitter in size and position, fading out a little.
            amp = 1.0 - 0.35 * min(1.0, (t - lunge) / JUMPSCARE_TIME)
            scale += 0.035 * math.sin(t * 47) + random.uniform(-0.02, 0.02)
            cx += random.uniform(-20, 20) * amp
            cy += random.uniform(-14, 14) * amp
            screen.scroll(random.randint(-10, 10), random.randint(-6, 6))
        frame = frames[int(t * 15) % len(frames)]
        x, y, img = self._blit_scaled(screen, frame, cx, cy, scale)
        q = img.get_width() / float(frame.surface.get_width())
        add_glows(screen, [(gx * q + x, gy * q + y, gr * q, gc) for gx, gy, gr, gc in frame.glows])

        # Colour-split ghosts that make the image tear now and then.
        if k >= 1 and random.random() < 0.35:
            for color, dx in (((80, 0, 0), random.randint(6, 14)), ((0, 30, 55), -random.randint(6, 14))):
                screen.blit(self._ghost(img, color), (x + dx, y + random.randint(-4, 4)),
                            special_flags=pygame.BLEND_RGB_ADD)
        # Impact flash.
        if t < 0.07:
            flash = pygame.Surface((SCREEN_W, SCREEN_H))
            flash.fill((255, 255, 255))
            flash.set_alpha(int(110 * (1 - t / 0.07)))
            screen.blit(flash, (0, 0))
        if random.random() < 0.3:
            self.static(screen, random.randint(30, 70))

    def _blit_scaled(self, screen, frame, cx, cy, scale):
        img, q = self._scaled(frame, scale)
        x = int(cx - frame.anchor[0] * q)
        y = int(cy - frame.anchor[1] * q)
        screen.blit(img, (x, y))
        return x, y, img


# --------------------------------------------------------------------------
# After the night
# --------------------------------------------------------------------------

class SixAMScene(Scene):
    def __init__(self, game, night, custom_state=None):
        super().__init__(game)
        self.night = night
        self.custom_state = custom_state
        self.t = 0.0
        self.sounds.stop_all()
        self.sounds.play("chimes", 0.9)
        self._record()

    def _record(self):
        g = self.game
        s = g.save
        if self.night <= 4:
            s["night"] = max(s.get("night", 1), self.night + 1)
        elif self.night == 5:
            s["beat5"] = True
            s["stars"] = max(s.get("stars", 0), 1)
        elif self.night == 6:
            s["beat6"] = True
            s["stars"] = max(s.get("stars", 0), 2)
        elif self.night == 7 and self.custom_state:
            levels = tuple(self.custom_state.roster[n].ai for n in CHARACTERS)
            if all(lv >= 20 for lv in levels):
                s["stars"] = 3
        g.write_save()

    def handle(self, event):
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN) and self.t > 2.5:
            self.t = max(self.t, 7.0)

    def update(self, dt):
        self.t += dt
        if self.t >= 7.6:
            g = self.game
            if self.night <= 4:
                g.change(NightIntroScene(g, self.night + 1))
            elif self.night == 5:
                g.change(EndingScene(g, "week"))
            elif self.night == 6:
                g.change(EndingScene(g, "overtime"))
            else:
                levels = tuple(self.custom_state.roster[n].ai for n in CHARACTERS) if self.custom_state else ()
                g.change(EndingScene(g, "fired" if levels and min(levels) >= 20 else "custom"))

    def draw(self, screen):
        screen.fill((0, 0, 0))
        cx, cy = SCREEN_W // 2, SCREEN_H // 2
        roll = max(0.0, min(1.0, (self.t - 0.8) / 1.6))
        roll = roll * roll * (3 - 2 * roll)
        f = font(120, bold=True)
        dw, dh = f.size("6")
        aw = f.size("AM")[0]
        left = cx - (dw + 30 + aw) // 2
        clip = pygame.Rect(left - 10, cy - dh // 2, dw + 20, dh)
        screen.set_clip(clip)
        draw_text(screen, "5", (left + dw // 2, cy - roll * dh), 120, (245, 245, 245), anchor="center", bold=True)
        draw_text(screen, "6", (left + dw // 2, cy + dh - roll * dh), 120, (245, 245, 245), anchor="center",
                  bold=True)
        screen.set_clip(None)
        draw_text(screen, "AM", (left + dw + 30, cy), 120, (245, 245, 245), anchor="midleft", bold=True)
        if self.t < 0.4:
            self.static(screen, 255 * (1 - self.t / 0.4))
        if self.t > 6.6:
            veil = pygame.Surface((SCREEN_W, SCREEN_H))
            veil.set_alpha(int(255 * min(1.0, (self.t - 6.6) / 1.0)))
            screen.blit(veil, (0, 0))


class GameOverScene(Scene):
    def __init__(self, game, night, killer):
        super().__init__(game)
        self.night = night
        self.killer = killer
        self.t = 0.0
        self.sounds.stop_all()
        self.sounds.loop("static", "static", 0.6)

    def handle(self, event):
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN) and self.t > 3.0:
            self.t = 99

    def update(self, dt):
        self.t += dt
        if self.t > 2.2:
            self.sounds.set_volume("static", max(0.0, 0.6 - (self.t - 2.2)))
        if self.t >= 9.0:
            self.game.change(MenuScene(self.game))

    def draw(self, screen):
        screen.fill((0, 0, 0))
        if self.t < 2.2:
            self.static(screen, 255)
            return
        # The guard, stuffed into a suit.
        a = int(255 * min(1.0, (self.t - 2.2) / 0.8))
        img = self.assets.screen_image("game_over")
        if a < 255:
            img = img.copy()
            img.set_alpha(a)
        screen.blit(img, (0, 0))
        draw_text(screen, "GAME OVER", (SCREEN_W - 60, SCREEN_H - 60), 76, (230, 230, 230), anchor="bottomright",
                  bold=True, alpha=a)
        self.static(screen, 60 if self.t < 3 else 26)


class CrashScene(Scene):
    """Golden Freddy 'crashes' the game back to the menu."""

    def __init__(self, game):
        super().__init__(game)
        self.t = 0.0

    def update(self, dt):
        self.t += dt
        if self.t > 1.6:
            self.game.change(MenuScene(self.game))

    def draw(self, screen):
        screen.fill((0, 0, 0))
        if self.t < 0.15:
            self.static(screen, 255)


class EndingScene(Scene):
    CAPTIONS = {
        "week": ("Congratulations!", "You survived the week.  The 6th Night is now unlocked."),
        "overtime": ("Overtime!", "Your dedication has been noted.  The Custom Night is now unlocked."),
        "fired": ("4/20 MODE COMPLETE", "...that's one way to leave a job."),
        "custom": ("Shift complete", "Set all four to 20 for the final star."),
    }

    def __init__(self, game, kind):
        super().__init__(game)
        self.kind = kind
        self.t = 0.0
        self.image = self.assets.screen_image(kind)
        self.sounds.loop("music", "menu", 0.4)

    def handle(self, event):
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN) and self.t > 1.5:
            self.game.change(MenuScene(self.game))

    def update(self, dt):
        self.t += dt

    def draw(self, screen):
        screen.blit(self.image, (0, 0))
        title, sub = self.CAPTIONS[self.kind]
        draw_text(screen, title, (SCREEN_W // 2, 62), 64, (240, 240, 240), anchor="center", shadow=(0, 0, 0))
        draw_text(screen, sub, (SCREEN_W // 2, 112), 28, (205, 205, 205), anchor="center", shadow=(0, 0, 0))
        if self.t > 1.5:
            draw_text(screen, "click to continue", (SCREEN_W // 2, SCREEN_H - 34), 24, (150, 150, 150),
                      anchor="center")
        if self.t < 1.0:
            veil = pygame.Surface((SCREEN_W, SCREEN_H))
            veil.set_alpha(int(255 * (1 - self.t)))
            screen.blit(veil, (0, 0))


class CustomNightScene(Scene):
    PRESETS = [("All 0", (0, 0, 0, 0)), ("Night 6", (4, 10, 12, 16)), ("Golden Freddy", (1, 9, 8, 7)),
               ("4/20 Mode", (20, 20, 20, 20))]

    def __init__(self, game):
        super().__init__(game)
        self.levels = list(game.save.get("custom", [1, 3, 3, 1]))
        self.rects = {}

    def handle(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.game.change(MenuScene(self.game))
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for key, r in self.rects.items():
                if not r.collidepoint(event.pos):
                    continue
                self.sounds.play("blip", 0.5)
                if key == "ready":
                    self.game.save["custom"] = self.levels
                    self.game.write_save()
                    self.game.change(NightIntroScene(self.game, 7, tuple(self.levels)))
                elif key == "back":
                    self.game.change(MenuScene(self.game))
                elif key[0] == "preset":
                    self.levels = list(self.PRESETS[key[1]][1])
                else:
                    i, d = key
                    self.levels[i] = max(0, min(20, self.levels[i] + d))

    def draw(self, screen):
        screen.fill((0, 0, 0))
        draw_text(screen, "Customize Night", (SCREEN_W // 2, 60), 60, (240, 240, 240), anchor="center", bold=True)
        self.rects = {}
        for i, name in enumerate(CHARACTERS):
            x = 175 + i * 310
            box = pygame.Rect(0, 0, 230, 250)
            box.midtop = (x, 120)
            pygame.draw.rect(screen, (18, 18, 20), box)
            pygame.draw.rect(screen, (200, 200, 200), box, 2)
            portrait = self.assets.portraits.get(name)
            if portrait:
                screen.set_clip(box.inflate(-4, -4))
                portrait.blit(screen, (box.centerx, box.y + 120))
                screen.set_clip(None)
            draw_text(screen, name, (x, box.bottom + 26), 36, (240, 240, 240), anchor="center", bold=True)
            draw_text(screen, "A.I. Level", (x, box.bottom + 66), 26, (190, 190, 190), anchor="center")
            draw_text(screen, str(self.levels[i]), (x, box.bottom + 112), 56, (240, 240, 240), anchor="center",
                      bold=True)
            for d, label in ((-1, "<"), (1, ">")):
                r = pygame.Rect(0, 0, 50, 50)
                r.center = (x + d * 80, box.bottom + 112)
                pygame.draw.rect(screen, (60, 60, 60), r)
                pygame.draw.rect(screen, (220, 220, 220), r, 2)
                draw_text(screen, label, r.center, 40, (240, 240, 240), anchor="center", bold=True)
                self.rects[(i, d)] = r
        for j, (label, _) in enumerate(self.PRESETS):
            r = pygame.Rect(0, 0, 200, 40)
            r.center = (SCREEN_W // 2 + (j - 1.5) * 220, SCREEN_H - 120)
            pygame.draw.rect(screen, (40, 40, 40), r)
            pygame.draw.rect(screen, (170, 170, 170), r, 2)
            draw_text(screen, label, r.center, 26, (220, 220, 220), anchor="center")
            self.rects[("preset", j)] = r
        for key, label, cx in (("back", "BACK", 140), ("ready", "READY", SCREEN_W - 140)):
            r = pygame.Rect(0, 0, 180, 54)
            r.center = (cx, SCREEN_H - 50)
            pygame.draw.rect(screen, (70, 20, 20) if key == "back" else (20, 70, 20), r)
            pygame.draw.rect(screen, (230, 230, 230), r, 2)
            draw_text(screen, label, r.center, 36, (240, 240, 240), anchor="center", bold=True)
            self.rects[key] = r
