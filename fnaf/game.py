"""Top-level game object: window, asset loading, save file and main loop."""

import json
import os
import sys

import pygame

from .settings import FPS, SCREEN_H, SCREEN_W, TITLE
from .effects import PanoramaWarp, make_tablet
from .util import make_noise_frames, make_scanlines, make_vignette

if getattr(sys, "frozen", False):
    # Packaged with PyInstaller: keep the save next to the executable, not in
    # the temporary folder the bundle is unpacked into.
    _GAME_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _GAME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_PATH = os.path.join(_GAME_DIR, "save.json")


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

    def build(self):
        """Yields (fraction_done, label) while generating assets."""
        stages = [
            ("Tuning the static", 0.04, self._build_effects()),
            ("Recording sounds", 0.30, self.sounds.build()),
            ("Building the office", 0.18, self.office.build()),
            ("Wiring the cameras", 0.30, self.feeds.build()),
            ("Waking the animatronics", 0.18, self._build_characters()),
        ]
        done = 0.0
        for label, weight, gen in stages:
            n = 0
            for _ in gen:
                n += 1
                # Asymptotic progress within a stage (we don't know its length).
                yield done + weight * (1 - 1 / (1 + n / 12.0)), label
            done += weight
            yield done, label

    def _build_effects(self):
        self.noise = make_noise_frames(6)
        yield
        self.scanlines = make_scanlines()
        self.vignette = make_vignette(strength=0.55)
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
                frames.append(rc(name, 230, body=False, mouth=mouth, **kw))
                yield
            self.jumpscares[name] = frames
        self.jumpscares["Golden"] = [rc("Golden", 250, body=False, mouth=0.15, eyes="none")]
        yield
        self.menu_faces = [
            rc("Freddy", 165, body=False, eyes="normal"),
            rc("Freddy", 165, body=False, eyes="pinpoint", mouth=0.3),
            rc("Endo", 165, body=False, eyes="pinpoint"),
            rc("Freddy", 165, body=False, eyes="none", mouth=0.15),
        ]
        yield
        for name in ("Freddy", "Bonnie", "Chica", "Foxy"):
            self.portraits[name] = rc(name, 52, body=False)
            yield
        dead = rc("Freddy", 120, body=False, eyes="pinpoint")
        img = dead.surface.copy()
        img.fill((70, 64, 70), special_flags=pygame.BLEND_RGB_MULT)
        dead.surface = img
        self.portraits["Freddy_dead"] = dead
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
        flags = pygame.SCALED
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
