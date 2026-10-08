"""Top-level game object: window, asset loading, save file and main loop."""

import json
import os
import sys

import pygame

from .settings import FPS, SCREEN_H, SCREEN_W, TITLE
from .effects import PanoramaWarp, make_tablet
from .util import make_light_mask, make_noise_frames, make_scanlines, make_vignette

if getattr(sys, "frozen", False):
    # Packaged with PyInstaller: keep the save next to the executable, not in
    # the temporary folder the bundle is unpacked into.
    _GAME_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _GAME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_PATH = os.path.join(_GAME_DIR, "save.json")


def grime(sprite, light=1.0, side=0.0):
    """Light a sprite like the original's renders: lit from the front (or a
    little to one side), falling off into shadow, with a grainy finish."""
    surf = sprite.surface
    w, h = surf.get_size()
    ax, ay = sprite.anchor
    lights = [(ax + w * side, ay + h * 0.06, w * 0.62, h * 0.5,
               (int(210 * light), int(200 * light), int(190 * light)))]
    mask = make_light_mask(w, h, (58, 52, 54), lights)
    grain = make_noise_frames(1, w, h, pixel=2, contrast=0.35)[0]
    grain.fill((70, 70, 70), special_flags=pygame.BLEND_RGB_MULT)
    grain.fill((185, 185, 185), special_flags=pygame.BLEND_RGB_ADD)
    out = surf.copy()
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    out.blit(grain, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    sprite.surface = out
    return sprite


class Assets:
    """Everything generated at startup. ``build()`` is a progress generator."""

    def __init__(self):
        from . import audio, characters, office, rooms
        self.characters = characters
        self.office_mod = office
        self.sounds = audio.SoundBank()
        self.office = office.Office()
        self.feeds = rooms.CamFeeds()
        self.noise = []
        self.scanlines = None
        self.vignette = None
        self.warp = None
        self.tablet = None
        self.jumpscares = {}
        self.menu_faces = []
        self.portraits = {}
        self.screens = {}

    def screen_image(self, kind):
        """Full-screen interstitial art: 'newspaper', 'game_over' or a paycheck kind."""
        img = self.screens.get(kind)
        if img is None:
            from . import screens
            if kind == "newspaper":
                img = screens.newspaper()
            elif kind == "game_over":
                img = screens.game_over()
            else:
                img = screens.paycheck(kind)
            self.screens[kind] = img
        return img

    def _build_screens(self):
        for kind in ("game_over", "newspaper"):
            self.screen_image(kind)
            yield

    def build(self):
        """Yields (fraction_done, label) while generating assets."""
        # Sounds are synthesised in a separate process while the art is drawn
        # here; the last stage collects them (or builds them in-process).
        self.sounds.start_background()
        stages = [
            ("Tuning the static", 0.04, self._build_effects()),
            ("Building the office", 0.14, self.office.build()),
            ("Wiring the cameras", 0.36, self.feeds.build()),
            ("Waking the animatronics", 0.2, self._build_characters()),
            ("Printing the paperwork", 0.06, self._build_screens()),
            ("Recording sounds", 0.2, self.sounds.build()),
        ]
        done = 0.0
        for label, weight, gen in stages:
            n = 0
            for _ in gen:
                self.sounds.pump()
                n += 1
                # Asymptotic progress within a stage (we don't know its length).
                yield done + weight * (1 - 1 / (1 + n / 12.0)), label
            done += weight
            yield done, label

    def _build_effects(self):
        self.noise = []
        for _ in range(6):
            self.noise += make_noise_frames(1)
            yield
        self.scanlines = make_scanlines()
        self.vignette = make_vignette(strength=0.62)
        self.warp = PanoramaWarp()
        self.tablet = make_tablet()
        yield

    def _build_characters(self):
        rc = self.characters.render_character
        js = {
            "Freddy": dict(eyes="normal"),
            "Bonnie": dict(eyes="pinpoint"),
            "Chica": dict(eyes="normal"),
            "Foxy": dict(eyes="glow"),
        }
        for name, kw in js.items():
            frames = []
            for mouth in (1.0, 0.72):
                frames.append(grime(rc(name, 250, body=False, mouth=mouth, **kw)))
                yield
            self.jumpscares[name] = frames
        self.jumpscares["Golden"] = [grime(rc("Golden", 270, body=False, mouth=0.15, eyes="none"), 0.8)]
        yield
        self.menu_faces = [
            grime(rc("Freddy", 165, body=False, eyes="normal"), 0.9, side=-0.18),
            grime(rc("Freddy", 165, body=False, eyes="pinpoint", mouth=0.3), 0.9, side=-0.18),
            grime(rc("Endo", 165, body=False, eyes="pinpoint"), 0.9, side=-0.18),
            grime(rc("Freddy", 165, body=False, eyes="none", mouth=0.15), 0.9, side=-0.18),
        ]
        yield
        for name in ("Freddy", "Bonnie", "Chica", "Foxy"):
            self.portraits[name] = grime(rc(name, 52, body=False), 1.05)
            yield
        yield


class Game:
    def __init__(self, args=None):
        self.args = args
        pygame.mixer.pre_init(22050, -16, 2, 512)
        pygame.init()
        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init()
            except pygame.error:
                pass
        if args is not None and args.mute and pygame.mixer.get_init():
            pygame.mixer.quit()  # SoundBank falls back to silent mode
        flags = pygame.SCALED | pygame.RESIZABLE
        if args is not None and args.fullscreen:
            flags |= pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), flags)
        pygame.display.set_caption(TITLE)
        self.clock = pygame.time.Clock()
        self.running = True
        self.save = self._load_save()
        self.assets = Assets()
        from .scenes import LoadingScene
        self.scene = LoadingScene(self, self._first_scene)

    def _first_scene(self):
        from . import scenes
        a = self.args
        self._set_icon()
        if a is not None and a.custom:
            return scenes.NightIntroScene(self, 7, tuple(a.custom))
        if a is not None and a.night:
            return scenes.NightIntroScene(self, a.night)
        return scenes.MenuScene(self)

    def _set_icon(self):
        p = self.assets.portraits.get("Freddy")
        if p:
            try:
                pygame.display.set_icon(pygame.transform.smoothscale(p.surface, (32, 32)))
            except pygame.error:
                pass

    # -- saves ---------------------------------------------------------------
    def _load_save(self):
        try:
            with open(SAVE_PATH) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {"night": 1, "stars": 0}

    def write_save(self):
        try:
            with open(SAVE_PATH, "w") as f:
                json.dump(self.save, f, indent=2)
        except OSError:
            pass

    # -- loop ----------------------------------------------------------------
    def change(self, scene):
        self.scene = scene

    def run(self):
        while self.running:
            dt = min(self.clock.tick(FPS) / 1000.0, 0.05)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                else:
                    self.scene.handle(event)
            self.scene.update(dt)
            self.scene.draw(self.screen)
            pygame.display.flip()
        pygame.quit()
