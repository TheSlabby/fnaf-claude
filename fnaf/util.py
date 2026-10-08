"""Shared drawing helpers: the Pen, lighting, text and screen effects.

Everything visual in the game is drawn with pygame primitives through a
``Pen``, which maps a local coordinate system (origin + scale, optionally
mirrored) onto a surface. Art is usually drawn supersampled and then
smoothscaled down (see ``render_ss``) to get anti-aliased edges.

This module only uses APIs that exist in both pygame 2.1+ and pygame-ce.
"""

import math
import os
import random

import pygame

from .settings import SCREEN_W, SCREEN_H

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)


# --------------------------------------------------------------------------
# Colour helpers
# --------------------------------------------------------------------------

def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c1, c2, t):
    t = clamp(t, 0.0, 1.0)
    return tuple(int(round(lerp(a, b, t))) for a, b in zip(c1, c2))


def shade(color, k):
    """Multiply an RGB(A) colour by k (k<1 darkens, k>1 brightens)."""
    out = tuple(int(clamp(c * k, 0, 255)) for c in color[:3])
    return out + tuple(color[3:])


# --------------------------------------------------------------------------
# Pen: drawing in a local coordinate system
# --------------------------------------------------------------------------

class Pen:
    """Draws shapes in local units onto ``surf``.

    A local point (x, y) maps to pixel (ox + x*s*fx, oy + y*s) where fx is -1
    when the pen is mirrored. ``glows`` collects glowing points (pixel x, y,
    pixel radius, colour) that are added *after* lighting so eyes shine in
    the dark; child pens share the parent's list.
    """

    def __init__(self, surf, ox=0.0, oy=0.0, s=1.0, flip=False):
        self.surf = surf
        self.ox = ox
        self.oy = oy
        self.s = s
        self.fx = -1 if flip else 1
        self.glows = []

    # -- coordinate mapping -------------------------------------------------
    def sub(self, x, y, scale=1.0, flip=False):
        """A child pen whose origin is local (x, y) and unit is ``scale`` units."""
        px, py = self.pt(x, y)
        child = Pen(self.surf, px, py, self.s * scale, flip != (self.fx < 0))
        child.glows = self.glows
        return child

    def pt(self, x, y):
        return (self.ox + x * self.s * self.fx, self.oy + y * self.s)

    def pts(self, pts):
        return [self.pt(x, y) for x, y in pts]

    def px(self, v):
        """Convert a local length into pixels."""
        return v * self.s

    def _w(self, w):
        return 0 if not w else max(1, int(round(w * self.s)))

    # -- primitives -----------------------------------------------------------
    def ellipse(self, color, x, y, rx, ry, width=0):
        cx, cy = self.pt(x, y)
        rxp, ryp = abs(rx * self.s), abs(ry * self.s)
        r = pygame.Rect(0, 0, max(1, int(round(2 * rxp))), max(1, int(round(2 * ryp))))
        r.center = (int(round(cx)), int(round(cy)))
        pygame.draw.ellipse(self.surf, color, r, self._w(width))

    def circle(self, color, x, y, r, width=0):
        self.ellipse(color, x, y, r, r, width)

    def rellipse(self, color, x, y, rx, ry, angle_deg, width=0, n=28):
        """Rotated ellipse (angle in degrees, clockwise on screen)."""
        a = math.radians(angle_deg)
        ca, sa = math.cos(a), math.sin(a)
        pts = []
        for i in range(n):
            t = 2 * math.pi * i / n
            ex, ey = rx * math.cos(t), ry * math.sin(t)
            pts.append((x + ex * ca - ey * sa, y + ex * sa + ey * ca))
        self.poly(color, pts, width)

    def poly(self, color, pts, width=0):
        if len(pts) >= 3:
            pygame.draw.polygon(self.surf, color, self.pts(pts), self._w(width))

    def line(self, color, a, b, width=0.05):
        pygame.draw.line(self.surf, color, self.pt(*a), self.pt(*b), max(1, int(round(width * self.s))))

    def lines(self, color, pts, width=0.05, closed=False):
        if len(pts) >= 2:
            pygame.draw.lines(self.surf, color, closed, self.pts(pts), max(1, int(round(width * self.s))))

    def thick(self, color, a, b, width):
        """A capsule: a thick line with rounded ends (good for limbs)."""
        (x1, y1), (x2, y2) = a, b
        dx, dy = x2 - x1, y2 - y1
        d = math.hypot(dx, dy) or 1e-6
        nx, ny = -dy / d * width / 2, dx / d * width / 2
        self.poly(color, [(x1 + nx, y1 + ny), (x2 + nx, y2 + ny), (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)])
        self.circle(color, x1, y1, width / 2)
        self.circle(color, x2, y2, width / 2)

    def rect(self, color, x, y, w, h, radius=0, width=0):
        """Axis-aligned rectangle with local top-left (x, y)."""
        x0, y0 = self.pt(x, y)
        x1, y1 = self.pt(x + w, y + h)
        r = pygame.Rect(int(round(min(x0, x1))), int(round(min(y0, y1))),
                        max(1, int(round(abs(x1 - x0)))), max(1, int(round(abs(y1 - y0)))))
        pygame.draw.rect(self.surf, color, r, self._w(width),
                         border_radius=int(round(radius * self.s)) if radius else 0)

    def arc(self, color, x, y, rx, ry, start_deg, stop_deg, width=0.05):
        """Elliptical arc. Angles are counter-clockwise from +x (pygame style).

        Mirrored pens mirror the arc too.
        """
        cx, cy = self.pt(x, y)
        r = pygame.Rect(0, 0, max(2, int(round(2 * abs(rx) * self.s))), max(2, int(round(2 * abs(ry) * self.s))))
        r.center = (int(round(cx)), int(round(cy)))
        a0, a1 = math.radians(start_deg), math.radians(stop_deg)
        if self.fx < 0:
            a0, a1 = math.pi - a1, math.pi - a0
        pygame.draw.arc(self.surf, color, r, a0, a1, max(1, int(round(width * self.s))))

    def vgrad(self, x, y, w, h, top, bottom):
        """Vertical gradient filled rectangle."""
        x0, y0 = self.pt(x, y)
        x1, y1 = self.pt(x + w, y + h)
        left, right = int(round(min(x0, x1))), int(round(max(x0, x1)))
        ytop, ybot = int(round(y0)), int(round(y1))
        span = max(1, ybot - ytop)
        for yy in range(ytop, ybot):
            c = lerp_color(top, bottom, (yy - ytop) / span)
            pygame.draw.line(self.surf, c, (left, yy), (right - 1, yy))

    def hgrad(self, x, y, w, h, left, right):
        """Horizontal gradient filled rectangle."""
        x0, y0 = self.pt(x, y)
        x1, y1 = self.pt(x + w, y + h)
        xl, xr = int(round(min(x0, x1))), int(round(max(x0, x1)))
        yt, yb = int(round(y0)), int(round(y1))
        span = max(1, xr - xl)
        for xx in range(xl, xr):
            t = (xx - xl) / span
            if self.fx < 0:
                t = 1 - t
            pygame.draw.line(self.surf, lerp_color(left, right, t), (xx, yt), (xx, yb - 1))

    def text(self, string, x, y, size, color, anchor="center", bold=True, mono=False):
        """Text whose height is ``size`` local units. Never mirrored."""
        px_size = max(6, int(round(size * self.s)))
        img = font(px_size, bold=bold, mono=mono).render(string, True, color)
        r = img.get_rect(**{anchor: (int(round(self.pt(x, y)[0])), int(round(self.pt(x, y)[1])))})
        self.surf.blit(img, r)
        return r

    def glow(self, x, y, r, color):
        px, py = self.pt(x, y)
        self.glows.append((px, py, r * self.s, color))


# --------------------------------------------------------------------------
# Supersampled rendering
# --------------------------------------------------------------------------

class Sprite:
    """A rendered image plus an anchor point and glow list (in sprite pixels)."""

    def __init__(self, surface, anchor=(0, 0), glows=None):
        self.surface = surface
        self.anchor = anchor
        self.glows = glows or []

    def blit(self, dest, pos, glow_list=None):
        """Blit so the anchor lands at ``pos``. Optionally collect glows."""
        x = int(round(pos[0] - self.anchor[0]))
        y = int(round(pos[1] - self.anchor[1]))
        dest.blit(self.surface, (x, y))
        if glow_list is not None:
            for gx, gy, gr, gc in self.glows:
                glow_list.append((gx + x, gy + y, gr, gc))
        return x, y


def render_ss(w, h, draw_fn, ss=2, alpha=False, fill=None):
    """Render ``draw_fn(pen)`` at ``ss``x resolution and smoothscale down.

    The pen passed to draw_fn has unit = 1 output pixel, so draw_fn can work
    in output-pixel coordinates. Returns (surface, glows) where glows are in
    output pixels.
    """
    w, h = max(1, int(w)), max(1, int(h))
    flags = pygame.SRCALPHA if alpha else 0
    big = pygame.Surface((w * ss, h * ss), flags)
    if alpha:
        big.fill((0, 0, 0, 0))
    elif fill is not None:
        big.fill(fill)
    pen = Pen(big, 0, 0, ss)
    draw_fn(pen)
    out = pygame.transform.smoothscale(big, (w, h)) if ss != 1 else big
    glows = [(gx / ss, gy / ss, gr / ss, gc) for gx, gy, gr, gc in pen.glows]
    return out, glows


# --------------------------------------------------------------------------
# Lighting
# --------------------------------------------------------------------------

_radial_cache = {}


def radial_sprite(size=256, power=1.6):
    """Greyscale radial falloff: white centre fading to black at the edge."""
    key = (size, power)
    if key not in _radial_cache:
        surf = pygame.Surface((size, size))
        surf.fill(BLACK)
        c = size // 2
        for i in range(c, 0, -1):
            k = (1 - i / c) ** power
            v = int(255 * k)
            pygame.draw.circle(surf, (v, v, v), (c, c), i)
        _radial_cache[key] = surf
    return _radial_cache[key]


def make_light_mask(w, h, ambient, lights):
    """Build a multiply mask.

    ``ambient`` is an RGB colour applied everywhere; ``lights`` is a list of
    (x, y, rx, ry, rgb) radial lights added on top. Multiply a scene by the
    result with ``apply_light``.
    """
    mask = pygame.Surface((int(w), int(h)))
    mask.fill(ambient)
    base = radial_sprite(256, 1.4)
    for x, y, rx, ry, col in lights:
        spr = pygame.transform.smoothscale(base, (max(2, int(rx * 2)), max(2, int(ry * 2))))
        tint = pygame.Surface(spr.get_size())
        tint.fill(col)
        spr = spr.copy()
        spr.blit(tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        mask.blit(spr, (int(x - rx), int(y - ry)), special_flags=pygame.BLEND_RGB_ADD)
    return mask


def apply_light(surf, mask, pos=(0, 0)):
    surf.blit(mask, pos, special_flags=pygame.BLEND_RGB_MULT)


_glow_cache = {}


def glow_sprite(radius, color):
    """Additive glow blob (black background, use BLEND_RGB_ADD)."""
    radius = max(2, int(radius))
    key = (radius, tuple(color[:3]))
    if key not in _glow_cache:
        spr = pygame.transform.smoothscale(radial_sprite(128, 2.2), (radius * 2, radius * 2))
        tint = pygame.Surface(spr.get_size())
        tint.fill(color[:3])
        spr = spr.copy()
        spr.blit(tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        _glow_cache[key] = spr
    return _glow_cache[key]


def add_glows(surf, glows, strength=1.0, offset=(0, 0)):
    """Add glow blobs (as collected by Pen.glow / Sprite.blit) onto surf."""
    for gx, gy, gr, gc in glows:
        col = tuple(int(clamp(c * strength, 0, 255)) for c in gc[:3])
        spr = glow_sprite(gr, col)
        surf.blit(spr, (int(gx - gr + offset[0]), int(gy - gr + offset[1])), special_flags=pygame.BLEND_RGB_ADD)


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

_font_cache = {}
MONO_FONTS = "consolas,dejavusansmono,liberationmono,menlo,couriernew,monospace"


def font(size, bold=False, mono=False):
    key = (int(size), bold, mono)
    f = _font_cache.get(key)
    if f is None:
        if mono:
            f = pygame.font.SysFont(MONO_FONTS, int(size), bold=bold)
        else:
            # pygame's default font (freesansbold) is already bold; synthetic
            # bolding on top of it looks smeared, so `bold` only affects mono.
            f = pygame.font.Font(None, int(size))
        _font_cache[key] = f
    return f


def draw_text(surf, string, pos, size=32, color=WHITE, anchor="topleft", bold=False,
              mono=False, alpha=255, shadow=None):
    """Render text anchored at ``pos``. Returns the drawn rect."""
    f = font(size, bold, mono)
    img = f.render(string, True, color)
    if alpha < 255:
        img.set_alpha(alpha)
    r = img.get_rect(**{anchor: (int(pos[0]), int(pos[1]))})
    if shadow:
        sh = f.render(string, True, shadow)
        if alpha < 255:
            sh.set_alpha(alpha)
        surf.blit(sh, r.move(2, 2))
    surf.blit(img, r)
    return r


# --------------------------------------------------------------------------
# Screen effects
# --------------------------------------------------------------------------

def make_noise_frames(n=6, w=SCREEN_W, h=SCREEN_H, pixel=2, contrast=1.0):
    """TV static frames (opaque greyscale)."""
    lw, lh = w // pixel, h // pixel
    palette = [lerp_color((0, 0, 0), (255, 255, 255), clamp((i / 255 - 0.5) * contrast + 0.5, 0, 1))
               for i in range(256)]
    frames = []
    for _ in range(n):
        data = os.urandom(lw * lh)
        try:
            small = pygame.image.frombuffer(data, (lw, lh), "P").copy()
            small.set_palette(palette)
        except (ValueError, pygame.error):
            rgb = bytes(v for b in data for v in palette[b])
            small = pygame.image.frombuffer(rgb, (lw, lh), "RGB").copy()
        frames.append(pygame.transform.scale(small, (w, h)).convert())
    return frames


def make_scanlines(w=SCREEN_W, h=SCREEN_H, gap=3, alpha=50):
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(0, h, gap):
        pygame.draw.line(surf, (0, 0, 0, alpha), (0, y), (w, y))
    return surf


def make_vignette(w=SCREEN_W, h=SCREEN_H, strength=0.75, center=255):
    """Multiply mask: bright centre, dark edges."""
    surf = pygame.Surface((w, h))
    edge = int(255 * (1 - strength))
    surf.fill((edge, edge, edge))
    spr = pygame.transform.smoothscale(radial_sprite(256, 0.7), (int(w * 1.5), int(h * 1.9)))
    tint = pygame.Surface(spr.get_size())
    tint.fill((center - edge,) * 3)
    spr = spr.copy()
    spr.blit(tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    surf.blit(spr, ((w - spr.get_width()) // 2, (h - spr.get_height()) // 2), special_flags=pygame.BLEND_RGB_ADD)
    return surf


def rand_sign():
    return 1 if random.random() < 0.5 else -1
