"""Screen-space effects: the office's panoramic warp, CCTV glitches, the
monitor flip and jumpscare helpers."""

import math
import random

import pygame

from .settings import SCREEN_H, SCREEN_W


class PanoramaWarp:
    """The original office's curved-panorama look.

    The view is cut into thin vertical strips and each strip is stretched
    vertically a little more the further it is from the screen centre, so
    the room seems to wrap around the player.
    """

    def __init__(self, strength=0.1, strips=128):
        self.strength = strength
        w = SCREEN_W // strips
        self.strips = []
        for i in range(strips):
            x0 = i * w
            d = (x0 + w / 2 - SCREEN_W / 2) / (SCREEN_W / 2)
            h = int(round(SCREEN_H * (1 + strength * d * d)))
            self.strips.append((x0, w, h, (h - SCREEN_H) // 2))

    def apply(self, src, dest):
        """Draw ``src`` (screen sized) warped onto ``dest``."""
        scale = pygame.transform.scale
        for x0, w, h, off in self.strips:
            dest.blit(scale(src.subsurface((x0, 0, w, SCREEN_H)), (w, h)), (x0, -off))

    def unwarp(self, pos):
        """Map a point on the warped screen back to the unwarped view."""
        x, y = pos
        d = (x - SCREEN_W / 2) / (SCREEN_W / 2)
        s = 1 + self.strength * d * d
        return x, SCREEN_H / 2 + (y - SCREEN_H / 2) / s


class CamGlitch:
    """Rolling CRT bar and occasional horizontal tearing for camera feeds."""

    def __init__(self):
        self.bar_y = random.uniform(0, SCREEN_H)
        self.tears = []          # [y, h, dx, ttl]
        self.bar = pygame.Surface((SCREEN_W, 60), pygame.SRCALPHA)
        for i in range(60):
            a = int(16 * math.sin(math.pi * i / 60))
            pygame.draw.line(self.bar, (255, 255, 255, a), (0, i), (SCREEN_W, i))

    def kick(self, n=3):
        """Burst of tearing (camera switch, animatronic moving)."""
        for _ in range(n):
            self.tears.append([random.randrange(0, SCREEN_H - 40), random.randint(6, 40),
                               random.choice((-1, 1)) * random.randint(8, 50), random.uniform(0.05, 0.18)])

    def update(self, dt):
        self.bar_y = (self.bar_y + dt * 70) % (SCREEN_H + 120)
        for t in self.tears:
            t[3] -= dt
        self.tears = [t for t in self.tears if t[3] > 0]
        if random.random() < dt * 0.25:
            self.kick(1)

    def draw(self, screen):
        for y, h, dx, _ in self.tears:
            band = screen.subsurface((0, y, SCREEN_W, h)).copy()
            screen.blit(band, (dx, y))
        screen.blit(self.bar, (0, int(self.bar_y) - 60))


def make_tablet():
    """The security tablet that flips up in front of the player."""
    w, h = SCREEN_W + 40, SCREEN_H + 40
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(surf, (28, 28, 30), (0, 0, w, h), border_radius=34)
    pygame.draw.rect(surf, (62, 62, 66), (0, 0, w, h), 6, border_radius=34)
    pygame.draw.rect(surf, (6, 8, 8), (44, 40, w - 88, h - 80), border_radius=10)
    # A faint reflection across the dark screen.
    glare = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.polygon(glare, (255, 255, 255, 14), [(w * 0.15, 40), (w * 0.42, 40), (w * 0.22, h - 40),
                                                       (w * -0.05, h - 40)])
    surf.blit(glare, (0, 0))
    pygame.draw.circle(surf, (50, 50, 54), (w // 2, h - 20), 8)
    return surf


def draw_tablet(screen, tablet, p):
    """Monitor rising (p: 0 hidden .. 1 covering the screen)."""
    if p <= 0:
        return
    e = 1 - (1 - p) ** 2
    w = int(tablet.get_width() * (0.8 + 0.2 * e))
    h = int(tablet.get_height() * (0.55 + 0.45 * e))
    img = pygame.transform.scale(tablet, (w, h))
    x = (SCREEN_W - w) // 2
    y = int(SCREEN_H - 20 - (SCREEN_H - 20 + (h - SCREEN_H) // 2) * e) + int((1 - e) * 60)
    screen.blit(img, (x, y))


def tinted(surface, color):
    """Copy of an alpha sprite multiplied by an RGB colour (alpha kept)."""
    out = surface.copy()
    out.fill(color + (255,), special_flags=pygame.BLEND_RGBA_MULT)
    return out
