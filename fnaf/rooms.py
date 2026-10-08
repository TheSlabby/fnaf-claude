"""Security camera feeds: procedurally drawn rooms, lit and populated.

``CamFeeds.build()`` pre-renders every room once (supersampled art, grime,
light masks, character sprites).  Static parts are *pre-lit*: the room's
light mask (with a cold blue-green CCTV cast and a lifted black level) is
multiplied into the background and into every character / foreground layer
at the fixed spot where it will be shown.  Because lighting is linear per
pixel, compositing pre-lit layers gives exactly the same result as lighting
the composed frame, but ``get()`` then only costs a copy, a few blits and the
eye glows.

Only APIs common to pygame 2.1+ and pygame-ce are used.
"""

import collections
import math
import random

import pygame

from . import characters
from .settings import CAM_H, CAM_W
from .util import Pen, add_glows, clamp, lerp, lerp_color, make_light_mask, make_vignette, shade

W, H = CAM_W, CAM_H
SS = 2                      # supersampling factor for room art
FEED_RECT = pygame.Rect(0, 0, W, H)
U = 0.27                    # metres per character unit (head radius)
CAST = (214, 250, 242)      # multiply: cold blue-green CCTV tint
LIFT = (5, 9, 10)           # raised black level of a cheap camera
CACHE_SIZE = 10

FREDDY, BONNIE, CHICA, FOXY = "Freddy", "Bonnie", "Chica", "Foxy"

TILE_W = (196, 194, 184)
TILE_B = (20, 20, 22)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _display_ready():
    return pygame.display.get_init() and pygame.display.get_surface() is not None


def _opaque(s):
    return s.convert() if _display_ready() else s


def _alpha(s):
    return s.convert_alpha() if _display_ready() else s


def _value_noise(w, h, cell, lo, hi, rng):
    """Smooth greyscale value noise (lo..hi) of size w x h."""
    cell = max(1, int(cell))
    gw, gh = w // cell + 3, h // cell + 3
    n = gw * gh
    table = bytes(lo + (i * (hi - lo + 1)) // 256 for i in range(256))
    if hasattr(rng, "randbytes"):
        data = rng.randbytes(n).translate(table)
    else:   # Python < 3.9
        data = bytes(rng.getrandbits(8) for _ in range(n)).translate(table)
    rgb = bytearray(3 * n)
    rgb[0::3] = data
    rgb[1::3] = data
    rgb[2::3] = data
    small = pygame.image.frombuffer(bytes(rgb), (gw, gh), "RGB")
    if cell == 1:
        return small.subsurface((1, 1, w, h)).copy()
    big = pygame.transform.smoothscale(small, (gw * cell, gh * cell))
    return big.subsurface((cell, cell, w, h)).copy()


def _grime(surf, rng, amount=1.0):
    """Multiply blotchy dirt and fine grain into an opaque surface."""
    w, h = surf.get_size()
    for cell, lo in ((110, 150), (34, 185), (9, 205), (2, 222)):
        lo = int(255 - (255 - lo) * amount)
        surf.blit(_value_noise(w, h, cell, lo, 255, rng), (0, 0), special_flags=pygame.BLEND_RGB_MULT)


def _star(pen, color, x, y, r, rot=0.0, inner=0.45):
    pts = []
    for i in range(10):
        a = rot + math.pi * i / 5 - math.pi / 2
        rr = r if i % 2 == 0 else r * inner
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    pen.poly(color, pts)


def _wobble_line(pen, color, pts, width, rng, jitter=0.6, passes=2):
    """Crayon-ish stroke: a few jittered passes of a polyline."""
    for _ in range(passes):
        jp = [(x + rng.uniform(-jitter, jitter), y + rng.uniform(-jitter, jitter)) for x, y in pts]
        pen.lines(color, jp, width)


def _smoothstep(a, b, x):
    t = clamp((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _circle_pts(x, y, rx, ry, n=18, a0=0.0, a1=2 * math.pi):
    return [(x + rx * math.cos(a0 + (a1 - a0) * i / n), y + ry * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)]


# ---------------------------------------------------------------------------
# a tiny pinhole camera (no pitch/roll: verticals stay vertical)
# ---------------------------------------------------------------------------

class _Cam:
    def __init__(self, f=560.0, cx=720.0, cy=240.0, pos=(0.0, 2.3, 0.0), yaw=0.0):
        self.f, self.cx, self.cy = f, cx, cy
        self.px, self.py, self.pz = pos
        self.c, self.s = math.cos(yaw), math.sin(yaw)

    def depth(self, x, z):
        return (x - self.px) * self.s + (z - self.pz) * self.c

    def p(self, x, y, z):
        dx, dz = x - self.px, z - self.pz
        xc = dx * self.c - dz * self.s
        zc = max(0.05, dx * self.s + dz * self.c)
        return (self.cx + self.f * xc / zc, self.cy - self.f * (y - self.py) / zc)

    def scale(self, x, z):
        """Character scale (px per unit) at floor position (x, z)."""
        return self.f * U / self.depth(x, z)

    def head(self, x, z):
        """Screen position of a character's head centre standing at (x, z)."""
        return self.p(x, 6.0 * U, z)

    def ray(self, sx):
        dxc = (sx - self.cx) / self.f
        return (dxc * self.c + self.s, -dxc * self.s + self.c)

    # -- drawing --------------------------------------------------------------
    def poly(self, pen, color, pts3):
        pen.poly(color, [self.p(*q) for q in pts3])

    def wall_quad(self, pen, color, a, b, y0, y1):
        """Vertical quad between floor points a=(x,z) and b=(x,z), heights y0..y1."""
        self.poly(pen, color, [(a[0], y0, a[1]), (b[0], y0, b[1]), (b[0], y1, b[1]), (a[0], y1, a[1])])

    def checker(self, pen, x0, x1, z0, z1, tile, c1=TILE_W, c2=TILE_B, y=0.0):
        """Checkered floor (horizontal plane at height y). Tiles that shrink to
        a few pixels fade to grey so the distance doesn't shimmer."""
        grey = lerp_color(c1, c2, 0.5)
        nx = int(math.ceil((x1 - x0) / tile - 1e-6))
        nz = int(math.ceil((z1 - z0) / tile - 1e-6))
        for j in range(nz):
            za, zb = z0 + j * tile, min(z1, z0 + (j + 1) * tile)
            for i in range(nx):
                xa, xb = x0 + i * tile, min(x1, x0 + (i + 1) * tile)
                col = c1 if (i + j) % 2 == 0 else c2
                q = [self.p(xa, y, za), self.p(xb, y, za), self.p(xb, y, zb), self.p(xa, y, zb)]
                size = abs(q[2][1] - q[0][1]) + 0.25 * abs(q[1][0] - q[0][0])
                k = clamp((7.0 - size) / 7.0, 0.0, 0.85)
                if k > 0:
                    col = lerp_color(col, grey, k)
                pen.poly(col, q)

    def wall_checker(self, pen, a, b, y0, rows, sq, c1=TILE_W, c2=TILE_B, t0=0.0, t1=None):
        """A checkered band of square tiles (size sq metres) along wall a->b."""
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        ex, ez = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
        t1 = ln if t1 is None else t1
        grey = lerp_color(c1, c2, 0.5)
        n = int(math.ceil((t1 - t0) / sq))
        for i in range(n):
            ta, tb = t0 + i * sq, min(t1, t0 + (i + 1) * sq)
            pa = (a[0] + ex * ta, a[1] + ez * ta)
            pb = (a[0] + ex * tb, a[1] + ez * tb)
            for r in range(rows):
                ya, yb = y0 + r * sq, y0 + (r + 1) * sq
                col = c1 if (i + r) % 2 == 0 else c2
                q = [self.p(pa[0], ya, pa[1]), self.p(pb[0], ya, pb[1]),
                     self.p(pb[0], yb, pb[1]), self.p(pa[0], yb, pa[1])]
                size = abs(q[1][0] - q[0][0]) + 0.3 * abs(q[3][1] - q[0][1])
                k = clamp((6.0 - size) / 6.0, 0.0, 0.85)
                if k > 0:
                    col = lerp_color(col, grey, k)
                pen.poly(col, q)

    def wall_decal(self, big, tex, a, b, ta, tb, y0, y1, origin=(0, 0)):
        """Paste ``tex`` onto the vertical wall a->b (floor points), spanning
        distance ta..tb along the wall and heights y0..y1, with correct
        perspective. ``big`` is a supersampled surface (SS px per feed px)
        whose top-left corner is at feed position ``origin``."""
        ox, oy = origin[0] * SS, origin[1] * SS
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        ex, ez = (b[0] - a[0]) / ln, (b[1] - a[1]) / ln
        pa = (a[0] + ex * ta, a[1] + ez * ta)
        pb = (a[0] + ex * tb, a[1] + ez * tb)
        xa = self.p(pa[0], y0, pa[1])[0] * SS - ox
        xb = self.p(pb[0], y0, pb[1])[0] * SS - ox
        lo, hi = int(max(0, min(xa, xb))), int(min(big.get_width(), max(xa, xb) + 1))
        tw, th = tex.get_size()
        for X in range(lo, hi):
            dx, dz = self.ray((X + ox + 0.5) / SS)
            # solve cam + s*d = a + t*e
            det = dx * (-ez) - dz * (-ex)
            if abs(det) < 1e-9:
                continue
            rx, rz = a[0] - self.px, a[1] - self.pz
            s = (rx * (-ez) - rz * (-ex)) / det
            t = (dx * rz - dz * rx) / det
            if s <= 0.05:
                continue
            u = (t - ta) / (tb - ta)
            if not 0.0 <= u < 1.0:
                continue
            top = (self.cy - self.f * (y1 - self.py) / s) * SS
            bot = (self.cy - self.f * (y0 - self.py) / s) * SS
            hh = int(round(bot - top))
            if hh < 1:
                continue
            col = tex.subsurface((min(tw - 1, int(u * tw)), 0, 1, th))
            big.blit(pygame.transform.scale(col, (1, hh)), (X, int(round(top - oy))))


# ---------------------------------------------------------------------------
# textures: posters, children's drawings, newspaper
# ---------------------------------------------------------------------------

_head_cache = {}


def _head(name, scale, eyes="normal", look=(0.0, 0.0), mouth=0.0):
    key = (name, int(scale), eyes, look, mouth)
    spr = _head_cache.get(key)
    if spr is None:
        spr = characters.render_character(name, int(scale), ss=2, body=False, eyes=eyes, look=look,
                                          mouth=mouth, prop=False)
        _head_cache[key] = spr
    return spr


def _blit_head(pen, name, x, y, r, **kw):
    """Draw a character head (radius r local units) centred at local (x, y)."""
    spr = _head(name, max(4, r * pen.s), **kw)
    px, py = pen.pt(x, y)
    pen.surf.blit(spr.surface, (int(px - spr.anchor[0]), int(py - spr.anchor[1])))


def _poster_tex(kind, tw, th, rng):
    """A poster texture tw x th pixels. Local units: 100 = poster width."""
    surf = pygame.Surface((tw, th))
    pen = Pen(surf, 0, 0, tw / 100.0)
    ph = 100.0 * th / tw
    if kind == "celebrate":
        pen.vgrad(0, 0, 100, ph, (20, 18, 46), (6, 6, 14))
        for _ in range(26):
            _star(pen, rng.choice([(220, 200, 90), (200, 200, 220), (200, 80, 90)]),
                  rng.uniform(4, 96), rng.uniform(22, ph - 4), rng.uniform(1.0, 2.4), rng.uniform(0, 1))
        pen.text("CELEBRATE!", 50, 13, 17, (30, 10, 10))
        pen.text("CELEBRATE!", 50, 12, 17, (236, 60, 52))
        _blit_head(pen, BONNIE, 22, ph * 0.64, 15)
        _blit_head(pen, CHICA, 78, ph * 0.64, 15)
        _blit_head(pen, FREDDY, 50, ph * 0.70, 17)
    elif kind == "golden":
        pen.vgrad(0, 0, 100, ph, (34, 26, 10), (8, 6, 4))
        _blit_head(pen, "Golden", 50, ph * 0.56, 30, eyes="none")
    elif kind in ("freddy", "chica", "bonnie"):
        name, title, bgc = {"freddy": (FREDDY, "LET'S PARTY!", (140, 30, 36)),
                            "chica": (CHICA, "LET'S EAT!!!", (40, 70, 140)),
                            "bonnie": (BONNIE, "ROCK ON!", (150, 110, 30))}[kind]
        pen.vgrad(0, 0, 100, ph, shade(bgc, 1.1), shade(bgc, 0.55))
        for i in range(12):
            a = i * math.pi / 6
            pen.poly(shade(bgc, 1.35), [(50, ph * 0.55), (50 + 90 * math.cos(a), ph * 0.55 + 90 * math.sin(a)),
                                         (50 + 90 * math.cos(a + 0.22), ph * 0.55 + 90 * math.sin(a + 0.22))])
        pen.text(title, 50, 11, 14, (20, 10, 10))
        pen.text(title, 50, 10, 14, (250, 236, 120))
        _blit_head(pen, name, 50, ph * 0.6, 27)
    elif kind == "rules":
        pen.rect((214, 206, 180), 0, 0, 100, ph)
        pen.text("RULES", 50, 9, 13, (150, 30, 30))
        pen.text("FOR SAFETY", 50, 19, 7, (40, 40, 40))
        y = 27
        while y < ph - 8:
            pen.rect((70, 70, 70), 8, y, 3, 3)
            pen.rect((110, 108, 100), 15, y, rng.uniform(45, 78), 2.6)
            y += 7.5
    elif kind == "pizza":
        pen.vgrad(0, 0, 100, ph, (230, 196, 70), (190, 120, 40))
        pen.text("PIZZA!", 50, 12, 16, (150, 30, 20))
        pen.poly((236, 180, 80), [(20, ph * 0.35), (80, ph * 0.35), (50, ph * 0.92)])
        pen.poly((220, 70, 40), [(24, ph * 0.38), (76, ph * 0.38), (50, ph * 0.86)])
        pen.rect((200, 150, 70), 18, ph * 0.31, 64, 6, radius=3)
        for _ in range(6):
            pen.circle((140, 30, 30), rng.uniform(38, 62), rng.uniform(ph * 0.42, ph * 0.66), 3.2)
    elif kind == "paper":
        pen.rect((200, 198, 186), 0, 0, 100, ph)
        y = 10
        while y < ph - 6:
            pen.rect((120, 118, 110), 8, y, rng.uniform(40, 84), 2.4)
            y += 6.5
    # edge wear and frame
    pen.rect((10, 10, 10), 0, 0, 100, ph, width=1.2)
    for _ in range(int(ph // 6)):
        x, y = rng.uniform(0, 100), rng.uniform(0, ph)
        pen.circle(shade(surf.get_at((int(min(tw - 1, x * tw / 100)), int(min(th - 1, y * tw / 100)))), 0.8),
                   x, y, rng.uniform(0.6, 2.2))
    return surf


_KID_THEMES = ["bear", "bunny", "chick", "fox", "house", "family", "sun", "pizza"]


def _kid_drawing(w, h, rng, theme=None, angle=None):
    """A crayon drawing on paper (feed px w x h), rotated. Returns an SS-scale alpha surface."""
    theme = theme or rng.choice(_KID_THEMES)
    surf = pygame.Surface((int(w * SS), int(h * SS)), pygame.SRCALPHA)
    pen = Pen(surf, 0, 0, SS * w / 100.0)          # 100 units = paper width
    ph = 100.0 * h / w
    paper = rng.choice([(222, 220, 206), (230, 226, 200), (212, 214, 216), (226, 214, 190)])
    pen.rect(paper, 0, 0, 100, ph)
    cols = [(200, 40, 40), (40, 90, 200), (230, 190, 30), (40, 150, 60), (120, 60, 30), (150, 70, 170),
            (230, 120, 30), (30, 30, 30)]
    lw = 3.2
    gy = ph * 0.86
    _wobble_line(pen, (60, 160, 60), [(4, gy), (30, gy - 2), (60, gy + 1), (96, gy - 1)], lw, rng)
    if rng.random() < 0.6:   # sun
        sx, sy = rng.choice([(14, 14), (86, 14)])
        _wobble_line(pen, (240, 190, 20), _circle_pts(sx, sy, 8, 8, 12), lw, rng)
        for i in range(8):
            a = i * math.pi / 4
            _wobble_line(pen, (240, 190, 20), [(sx + 11 * math.cos(a), sy + 11 * math.sin(a)),
                                                (sx + 16 * math.cos(a), sy + 16 * math.sin(a))], lw * 0.8, rng)

    def bear(x, y, r, col, ears="round", beak=False):
        _wobble_line(pen, col, _circle_pts(x, y, r, r * 0.95, 16), lw, rng)
        if ears == "round":
            for sx in (-1, 1):
                _wobble_line(pen, col, _circle_pts(x + sx * r * 0.8, y - r * 0.85, r * 0.32, r * 0.32, 10), lw, rng)
        elif ears == "long":
            for sx in (-1, 1):
                _wobble_line(pen, col, _circle_pts(x + sx * r * 0.4, y - r * 1.6, r * 0.22, r * 0.75, 10), lw, rng)
        elif ears == "fox":
            for sx in (-1, 1):
                _wobble_line(pen, col, [(x + sx * r * 0.3, y - r * 0.9), (x + sx * r * 0.85, y - r * 1.6),
                                        (x + sx * r * 0.95, y - r * 0.5)], lw, rng)
        for sx in (-1, 1):
            pen.circle((20, 20, 20), x + sx * r * 0.38, y - r * 0.15, r * 0.12)
        if beak:
            _wobble_line(pen, (240, 140, 20), [(x - r * 0.4, y + r * 0.3), (x + r * 0.4, y + r * 0.3),
                                                (x, y + r * 0.62), (x - r * 0.4, y + r * 0.3)], lw, rng)
        else:
            _wobble_line(pen, (30, 30, 30), _circle_pts(x, y + r * 0.35, r * 0.4, r * 0.2, 8, 0, math.pi), lw * 0.7,
                         rng)
        # stick body
        by = y + r
        _wobble_line(pen, col, [(x, by), (x, by + r * 1.4)], lw, rng)
        _wobble_line(pen, col, [(x - r, by + r * 0.5), (x, by + r * 0.3), (x + r, by + r * 0.5)], lw, rng)
        _wobble_line(pen, col, [(x - r * 0.6, by + r * 2.4), (x, by + r * 1.4), (x + r * 0.6, by + r * 2.4)], lw,
                     rng)

    def person(x, y, r, col):
        _wobble_line(pen, (30, 30, 30), _circle_pts(x, y, r, r, 12), lw * 0.8, rng)
        _wobble_line(pen, col, [(x, y + r), (x, y + r * 3.2)], lw, rng)
        _wobble_line(pen, col, [(x - r * 1.4, y + r * 1.8), (x, y + r * 1.5), (x + r * 1.4, y + r * 1.8)], lw, rng)
        _wobble_line(pen, col, [(x - r, y + r * 4.6), (x, y + r * 3.2), (x + r, y + r * 4.6)], lw, rng)

    if theme == "bear":
        bear(50, ph * 0.38, 14, (130, 70, 30))
    elif theme == "bunny":
        bear(50, ph * 0.42, 13, (90, 70, 190), ears="long")
    elif theme == "chick":
        bear(50, ph * 0.38, 14, (230, 190, 30), ears=None, beak=True)
    elif theme == "fox":
        bear(50, ph * 0.40, 13, (200, 60, 30), ears="fox")
    elif theme == "house":
        c = rng.choice(cols)
        _wobble_line(pen, c, [(25, gy), (25, ph * 0.5), (75, ph * 0.5), (75, gy)], lw, rng)
        _wobble_line(pen, (200, 40, 40), [(20, ph * 0.52), (50, ph * 0.24), (80, ph * 0.52)], lw, rng)
        _wobble_line(pen, (120, 60, 30), [(44, gy), (44, ph * 0.68), (56, ph * 0.68), (56, gy)], lw, rng)
    elif theme == "family":
        for i, x in enumerate((24, 50, 76)):
            person(x, ph * 0.38 + (6 if i == 1 else 0), 6 if i != 1 else 5, rng.choice(cols))
    elif theme == "sun":
        bear(32, ph * 0.40, 10, (130, 70, 30))
        person(72, ph * 0.40, 5.5, rng.choice(cols))
    elif theme == "pizza":
        _wobble_line(pen, (220, 150, 40), [(22, ph * 0.3), (78, ph * 0.3), (50, ph * 0.8), (22, ph * 0.3)], lw, rng)
        for _ in range(5):
            pen.circle((200, 40, 40), rng.uniform(38, 62), rng.uniform(ph * 0.36, ph * 0.55), 3.5)
    # scribbled name
    _wobble_line(pen, rng.choice(cols), [(8 + i * 5, ph - 5 + rng.uniform(-1.5, 1.5)) for i in range(5)], lw * 0.6,
                 rng)
    # tape
    pen.rect((214, 210, 170), 40, -2, 20, 6)
    if angle is None:
        angle = rng.uniform(-10, 10)
    return pygame.transform.rotozoom(surf, angle, 1.0)


def _newspaper(w, h, rng, angle=-4):
    surf = pygame.Surface((int(w * SS), int(h * SS)), pygame.SRCALPHA)
    pen = Pen(surf, 0, 0, SS * w / 100.0)
    ph = 100.0 * h / w
    pen.rect((206, 196, 160), 0, 0, 100, ph)
    pen.text("LOCAL PIZZERIA", 50, 8, 9.5, (24, 22, 20))
    pen.text("UNDER INVESTIGATION", 50, 17, 7, (24, 22, 20))
    pen.rect((40, 38, 34), 6, 22, 88, 0.8)
    pen.rect((90, 86, 76), 54, 26, 40, 30)
    pen.ellipse((40, 36, 32), 74, 44, 9, 12)
    pen.circle((40, 36, 32), 74, 34, 6)
    for col_x in (6, 54):
        y = 26 if col_x == 6 else 60
        while y < ph - 5:
            pen.rect((112, 106, 92), col_x, y, rng.uniform(30, 40), 1.6)
            y += 4.2
    pen.rect((120, 110, 80), 0, 0, 100, ph, width=0.8)
    return pygame.transform.rotozoom(surf, angle, 1.0)


# ---------------------------------------------------------------------------
# props
# ---------------------------------------------------------------------------

def _curtain(pen, x_out, x_in, y_top, y_bot, folds, color, rng=None, stars=None, hem=8.0, nv=16, sub=5,
             lo=0.30, star_rows=9, star_r=7.0):
    """A hanging curtain drawn as shaded vertical folds.

    x_out / x_in are numbers or functions v->x (v = 0 top .. 1 bottom); folds
    compress naturally when the curtain is gathered.
    """
    xo = x_out if callable(x_out) else (lambda v, c=x_out: c)
    xi = x_in if callable(x_in) else (lambda v, c=x_in: c)
    vs = [i / nv for i in range(nv + 1)]
    n = folds * sub

    def bright(u):
        return lo + (1 - lo) * (0.5 + 0.5 * math.cos(2 * math.pi * folds * u)) ** 0.9

    def ybot(u):
        return y_bot + hem * (0.5 + 0.5 * math.cos(2 * math.pi * folds * u))

    for k in range(n):
        u0, u1 = k / n, (k + 1) / n
        col = shade(color, bright((u0 + u1) / 2))
        left = [(lerp(xo(v), xi(v), u0), lerp(y_top, ybot(u0), v)) for v in vs]
        right = [(lerp(xo(v), xi(v), u1), lerp(y_top, ybot(u1), v)) for v in reversed(vs)]
        pen.poly(col, left + right)
    if stars and rng:
        for r in range(star_rows):
            v = (r + 0.6) / (star_rows + 0.4)
            m = folds * 2
            for c in range(m):
                u = (c + (0.5 if r % 2 else 0.0) + 0.25) / m
                if u >= 1:
                    continue
                x = lerp(xo(v), xi(v), u)
                y = lerp(y_top, ybot(u), v)
                _star(pen, shade(stars, bright(u) * 0.95), x, y, star_r, rng.uniform(-0.2, 0.2))


def _valance(pen, x0, x1, y0, depth, n, color, fringe=(196, 150, 58)):
    w = (x1 - x0) / n
    pen.rect(shade(color, 0.6), x0, y0 - 40, x1 - x0, 40 + depth * 0.3)
    for i in range(n):
        a, b = x0 + i * w, x0 + (i + 1) * w
        pts = [(a, y0)] + [(lerp(a, b, t / 16), y0 + depth * 0.35 + depth * 0.65 * math.sin(math.pi * t / 16))
                           for t in range(17)] + [(b, y0)]
        pen.poly(color, [(a, y0 - 2)] + pts[1:-1] + [(b, y0 - 2)])
        for k, sh in ((0.75, 0.62), (0.5, 0.78)):
            arc = [(lerp(a, b, t / 16), y0 + depth * 0.35 * k + depth * 0.65 * k * math.sin(math.pi * t / 16))
                   for t in range(17)]
            pen.lines(shade(color, sh), arc, 3)
        bottom = pts[1:-1]
        pen.lines(fringe, bottom, 4)
        for t in range(0, 17, 1):
            x, y = bottom[t]
            pen.line(shade(fringe, 0.8), (x, y), (x, y + 9), 1.6)
        # tassel between swags
        pen.line(fringe, (b, y0), (b, y0 + depth * 0.6), 2.5)
        pen.ellipse(fringe, b, y0 + depth * 0.62, 5, 9)


def _box(pen, x, y, w, h, col, top=8.0, side=0.0, label=False, tape=True):
    """A cardboard box seen slightly from above (front face + top)."""
    pen.poly(shade(col, 1.15), [(x, y), (x + w, y), (x + w + side, y - top), (x + side, y - top)])
    pen.rect(col, x, y, w, h)
    if side:
        pen.poly(shade(col, 0.7), [(x + w, y), (x + w + side, y - top), (x + w + side, y + h - top), (x + w, y + h)])
    if tape:
        pen.rect(shade(col, 0.82), x + w * 0.42, y, w * 0.16, h * 0.4)
    if label:
        pen.rect((214, 208, 190), x + w * 0.15, y + h * 0.5, w * 0.35, h * 0.25)
        pen.rect((60, 60, 60), x + w * 0.18, y + h * 0.56, w * 0.25, h * 0.04)


def _bottle(pen, x, y, h, col, kind=0):
    """Cleaning bottle standing at (x, y=bottom)."""
    w = h * 0.36
    if kind == 0:     # spray bottle
        pen.rect(col, x - w / 2, y - h * 0.72, w, h * 0.72, radius=w * 0.18)
        pen.rect((230, 230, 230), x - w * 0.2, y - h * 0.86, w * 0.4, h * 0.16)
        pen.rect((230, 230, 230), x - w * 0.5, y - h, w * 0.9, h * 0.15, radius=2)
        pen.rect((240, 240, 230), x - w * 0.35, y - h * 0.5, w * 0.7, h * 0.22)
    elif kind == 1:   # jug
        pen.rect(col, x - w * 0.7, y - h * 0.8, w * 1.4, h * 0.8, radius=w * 0.25)
        pen.rect(shade(col, 0.8), x - w * 0.2, y - h * 0.95, w * 0.4, h * 0.2)
        pen.rect((236, 236, 220), x - w * 0.45, y - h * 0.55, w * 0.9, h * 0.25)
    else:             # can
        pen.rect(col, x - w * 0.6, y - h * 0.7, w * 1.2, h * 0.7)
        pen.ellipse(shade(col, 1.2), x, y - h * 0.7, w * 0.6, w * 0.15)


def _wall_stains(cam, pen, rng, a, b, top, band_y, n, wall, low):
    """Water stains running down a vertical wall a->b (floor points)."""
    for i in range(n):
        t = rng.uniform(0.02, 0.98)
        px, pz = lerp(a[0], b[0], t), lerp(a[1], b[1], t)
        ln = math.hypot(b[0] - a[0], b[1] - a[1])
        w = rng.uniform(0.02, 0.1) / ln
        qx, qz = lerp(a[0], b[0], t + w), lerp(a[1], b[1], t + w)
        mx, mz = lerp(a[0], b[0], t + w * 0.4), lerp(a[1], b[1], t + w * 0.4)
        if i % 3:
            cam.poly(pen, shade(wall, rng.uniform(0.72, 0.9)),
                     [(px, top, pz), (qx, top, qz), (mx, rng.uniform(band_y + 0.4, top - 0.3), mz)])
        else:
            cam.poly(pen, shade(low, rng.uniform(0.7, 0.85)),
                     [(px, band_y, pz), (qx, band_y, qz), (mx, rng.uniform(0.15, band_y - 0.3), mz)])



def _box3(cam, pen, x0, x1, y0, y1, z0, z1, col, label=False):
    """An axis-aligned box in world space: draws the faces visible from the camera."""
    if cam.py > y1:
        cam.poly(pen, shade(col, 1.18), [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)])
    if cam.px > x1:
        cam.poly(pen, shade(col, 0.72), [(x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)])
    elif cam.px < x0:
        cam.poly(pen, shade(col, 0.72), [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)])
    cam.poly(pen, col, [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)])
    xm = (x0 + x1) / 2
    w = (x1 - x0) * 0.08
    if (x1 - x0) > 0.15 and (y1 - y0) > 0.12:
        cam.poly(pen, shade(col, 0.84), [(xm - w, y1, z0), (xm + w, y1, z0), (xm + w, y1 - (y1 - y0) * 0.35, z0),
                                         (xm - w, y1 - (y1 - y0) * 0.35, z0)])
    if label:
        lx0, lx1 = lerp(x0, x1, 0.15), lerp(x0, x1, 0.5)
        ly0, ly1 = lerp(y0, y1, 0.25), lerp(y0, y1, 0.5)
        cam.poly(pen, (210, 204, 186), [(lx0, ly0, z0), (lx1, ly0, z0), (lx1, ly1, z0), (lx0, ly1, z0)])


def _endo_slumped(pen, big, x, y, s):
    """A bare endoskeleton sitting slumped on a table edge.

    (x, y): pelvis centre in feed px; s: px per character unit.
    """
    metal, hi, dark = (126, 126, 134), (178, 178, 188), (40, 40, 46)

    def P(u, v):
        return (x + u * s, y + v * s)

    def limb(a, b, w):
        ax, ay = P(*a)
        bx, by = P(*b)
        pen.thick(dark, (ax, ay), (bx, by), (w + 0.1) * s)
        pen.thick(metal, (ax, ay), (bx, by), w * s)
        pen.line(hi, (ax - w * s * 0.2, ay - w * s * 0.1), (bx - w * s * 0.2, by - w * s * 0.1),
                 max(1.0, w * s * 0.18))

    def joint(p, r):
        cx, cy = P(*p)
        pen.circle(dark, cx, cy, r * s * 1.15)
        pen.circle(hi, cx, cy, r * s)
        pen.circle(dark, cx, cy, r * s * 0.4)

    # legs: thighs toward the camera along the table, shins dangling over the edge
    for sx in (-1, 1):
        hip, knee, ankle = (sx * 0.45, 0.0), (sx * 0.58, 0.95), (sx * 0.62 + 0.05, 2.6)
        limb(knee, ankle, 0.22)
        joint(ankle, 0.14)
        fx, fy = P(ankle[0] + 0.05, ankle[1] + 0.18)
        pen.ellipse(dark, fx, fy, 0.36 * s, 0.17 * s)
        pen.ellipse(metal, fx, fy - 0.02 * s, 0.32 * s, 0.13 * s)
        limb(hip, knee, 0.34)
        joint(knee, 0.2)
    # pelvis
    pen.rect(dark, *P(-0.66, -0.3), 1.32 * s, 0.56 * s, radius=0.15 * s)
    pen.rect(metal, *P(-0.6, -0.26), 1.2 * s, 0.46 * s, radius=0.12 * s)
    # spine, collapsing sideways
    spine = [(0.0, -0.2), (0.2, -0.85), (0.5, -1.5), (0.95, -2.05), (1.3, -2.35)]
    for a, b in zip(spine, spine[1:]):
        limb(a, b, 0.17)
    for p in spine[1:]:
        joint(p, 0.1)
    # arms: left one propped on the table, right one hanging limp
    lsh, rsh = (0.05, -2.75), (2.05, -1.75)
    limb(lsh, (-0.45, -1.35), 0.18)
    limb((-0.45, -1.35), (-0.75, 0.15), 0.15)
    joint((-0.45, -1.35), 0.13)
    # ribcage plate (tilted with the spine)
    rib = [P(0.05, -2.95), P(2.0, -2.05), P(1.65, -1.05), P(0.15, -1.75)]
    pen.poly(dark, rib)
    for k in range(5):
        t = 0.1 + k * 0.2
        a = (lerp(0.07, 0.15, t), lerp(-2.9, -1.78, t))
        b = (lerp(1.97, 1.65, t), lerp(-2.02, -1.08, t))
        pen.line(metal, P(*a), P(*b), max(1.0, 0.11 * s))
    limb(lsh, rsh, 0.17)
    limb(rsh, (2.35, -0.55), 0.18)
    limb((2.35, -0.55), (2.45, 0.75), 0.15)
    joint((2.35, -0.55), 0.13)
    for hand, ang in (((-0.8, 0.2), 1.9), ((2.45, 0.85), 1.6)):
        hx, hy = P(*hand)
        pen.circle(metal, hx, hy, 0.14 * s)
        for k in range(4):
            a = ang + (k - 1.5) * 0.3
            pen.line(metal, (hx, hy), (hx + math.cos(a) * 0.32 * s, hy + math.sin(a) * 0.32 * s),
                     max(1.0, 0.06 * s))
    joint(lsh, 0.15)
    joint(rsh, 0.15)
    # neck and the head hanging off to the side
    hx, hy = 2.35, -2.75
    limb((1.35, -2.4), (hx - 0.4, hy + 0.35), 0.1)
    spr = _head("Endo", s * SS * 0.92, eyes="none")
    img = pygame.transform.rotozoom(spr.surface, -38, 1.0)
    cx, cy = P(hx, hy)
    big.blit(img, (int(cx * SS - img.get_width() / 2), int(cy * SS - img.get_height() / 2)))



# ---------------------------------------------------------------------------
# build context
# ---------------------------------------------------------------------------

class _Item:
    __slots__ = ("surf", "pos", "glows", "cond")

    def __init__(self, surf, pos, glows, cond):
        self.surf, self.pos, self.glows, self.cond = surf, pos, glows, cond


class _Room:
    def __init__(self):
        self.bg = None
        self.items = []
        self.relevant = frozenset()


class _Ctx:
    """Holds the supersampled canvas and the light mask while a room is built."""

    def __init__(self, seed, fill=(0, 0, 0)):
        self.rng = random.Random(seed)
        self.big = pygame.Surface((W * SS, H * SS))
        self.big.fill(fill)
        self.pen = Pen(self.big, 0, 0, SS)
        self.mask = None
        self.albedo = None
        self.room = _Room()

    # -- background -------------------------------------------------------------
    def finish_art(self, grime=1.0):
        self.albedo = pygame.transform.smoothscale(self.big, (W, H))
        self.big = None
        self.pen = None
        if grime:
            _grime(self.albedo, self.rng, grime)

    def light(self, ambient, lights, q=4, vignette=0.45, gain=1.2):
        if gain != 1.0:
            ambient = shade(ambient, gain)
            lights = [(x, y, rx, ry, shade(c, gain)) for x, y, rx, ry, c in lights]
        m = make_light_mask(W // q, H // q, ambient,
                            [(x / q, y / q, rx / q, ry / q, c) for x, y, rx, ry, c in lights])
        if vignette:
            m.blit(make_vignette(W // q, H // q, strength=vignette), (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        m = pygame.transform.smoothscale(m, (W, H))
        m.fill(CAST, special_flags=pygame.BLEND_RGB_MULT)
        self.mask = m

    def bake(self, static_glows=()):
        bg = self.albedo
        bg.blit(self.mask, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        bg.fill(LIFT, special_flags=pygame.BLEND_RGB_ADD)
        if static_glows:
            add_glows(bg, static_glows)
        self.room.bg = _opaque(bg)
        self.albedo = None

    # -- layers -----------------------------------------------------------------
    def layer_canvas(self, rect):
        """An SS-scale alpha canvas covering feed rect; returns (surface, pen)."""
        rect = pygame.Rect(rect)
        surf = pygame.Surface((rect.w * SS, rect.h * SS), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        return surf, Pen(surf, -rect.x * SS, -rect.y * SS, SS), rect

    def add_layer(self, canvas, cond, boost=1.0):
        surf, _pen, rect = canvas
        small = pygame.transform.smoothscale(surf, rect.size)
        self.add_surface(small, rect.topleft, cond, (), boost)

    def add_surface(self, surf, pos, cond, glows=(), boost=1.0, mask=None):
        """Pre-light an alpha surface placed at feed pos; register it as an item."""
        x, y = int(round(pos[0])), int(round(pos[1]))
        r = pygame.Rect(x, y, *surf.get_size()).clip(FEED_RECT)
        if r.w <= 0 or r.h <= 0:
            return None
        s = surf.subsurface(r.move(-x, -y)).copy()
        m = (mask or self.mask).subsurface(r)
        if boost != 1.0:
            m = m.copy()
            if boost > 1.0:
                extra = m.copy()
                k = int(clamp((boost - 1.0) * 255, 0, 255))
                extra.fill((k, k, k), special_flags=pygame.BLEND_RGB_MULT)
                m.blit(extra, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
            else:
                k = int(clamp(boost * 255, 0, 255))
                m.fill((k, k, k), special_flags=pygame.BLEND_RGB_MULT)
        s.blit(m, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        s.fill(LIFT, special_flags=pygame.BLEND_RGB_ADD)
        br = s.get_bounding_rect(min_alpha=1)
        if br.w <= 0 or br.h <= 0:
            return None
        s = _alpha(s.subsurface(br).copy())
        g = [(gx, gy, gr, gc) for gx, gy, gr, gc in glows]
        item = _Item(s, (r.x + br.x, r.y + br.y), g, cond)
        self.room.items.append(item)
        return item

    def shadow(self, center, rx, ry, cond, alpha=120):
        """A soft contact shadow (stacked translucent ellipses) on the floor."""
        w, h = int(rx * 2.6) + 4, int(ry * 2.6) + 4
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        ring = pygame.Surface((w, h), pygame.SRCALPHA)
        n = 8
        for i in range(n):
            k = 1.3 - 0.75 * i / (n - 1)
            ring.fill((0, 0, 0, 0))
            r = pygame.Rect(0, 0, max(2, int(2 * rx * k)), max(2, int(2 * ry * k)))
            r.center = (w // 2, h // 2)
            pygame.draw.ellipse(ring, (0, 0, 0, int(alpha / n)), r)
            surf.blit(ring, (0, 0))
        return self.add_surface(surf, (center[0] - w / 2, center[1] - h / 2), cond)

    def char(self, name, head, scale, cond, boost=1.0, rot=0.0, glow_k=1.0, **kw):
        """Render a character with its head centre at feed position ``head``.

        Props (guitar, mic, cupcake) are off unless asked for: in the original
        the animatronics only hold them on the show stage."""
        kw.setdefault("prop", False)
        spr = characters.render_character(name, max(2, int(round(scale))), ss=2, **kw)
        surf, (ax, ay) = spr.surface, spr.anchor
        glows = list(spr.glows)
        if rot:
            cw, ch = surf.get_size()
            surf = pygame.transform.rotozoom(surf, rot, 1.0)
            nw, nh = surf.get_size()
            a = math.radians(rot)
            ca, sa = math.cos(a), math.sin(a)

            def tr(px, py):
                dx, dy = px - cw / 2, py - ch / 2
                return (nw / 2 + dx * ca + dy * sa, nh / 2 - dx * sa + dy * ca)
            ax, ay = tr(ax, ay)
            glows = [tr(gx, gy) + (gr, gc) for gx, gy, gr, gc in glows]
        x, y = head[0] - ax, head[1] - ay
        g = [(gx + int(round(x)), gy + int(round(y)), gr, tuple(int(clamp(c * glow_k, 0, 255)) for c in gc[:3]))
             for gx, gy, gr, gc in glows]
        return self.add_surface(surf, (x, y), cond, g, boost)


def _has(name):
    return lambda st: name in st[0]


# ---------------------------------------------------------------------------
# the public class
# ---------------------------------------------------------------------------

class CamFeeds:
    """Composes the security camera images. See module docstring."""

    def __init__(self):
        self._rooms = {}
        self._cache = collections.OrderedDict()
        self._black = None
        self._run = None            # Foxy run frames (built with 2A)
        self._run_mask = None

    # -- building ------------------------------------------------------------------
    _ORDER = ["1A", "1B", "1C", "2A", "2B", "3", "4A", "4B", "5", "7"]

    def build(self):
        """Generator: pre-renders everything, yielding between chunks of work."""
        for cam in self._ORDER:
            if cam not in self._rooms:
                for _ in self._build_room(cam):
                    yield
        if self._run is None:
            for _ in self._build_run():
                yield
        self._cache.clear()
        _head_cache.clear()

    def _build_room(self, cam):
        # room generators yield their _Ctx after each chunk; the last one
        # carries the finished room
        last = None
        for last in getattr(self, "_room_" + cam)():  # noqa: B007
            yield
        self._rooms[cam] = last.room

    def _ensure(self, cam):
        if cam not in self._rooms:
            for _ in self._build_room(cam):
                pass
        return self._rooms[cam]

    # -- composing -----------------------------------------------------------------
    def get(self, cam, occupants=frozenset(), foxy_stage=0, golden_poster=False, freddy_stare=False):
        """The fully composed, lit 1440x720 feed for ``cam`` (eye glows applied).

        Occupants that can't appear on this camera are ignored. Results are
        cached (LRU) and shared: treat the returned surface as read-only.
        Works lazily (builds just this room) if build() hasn't run.
        """
        if cam == "6" or cam not in self._ORDER:
            if self._black is None:
                self._black = pygame.Surface((W, H))
                self._black.fill((0, 0, 0))
                self._black = _opaque(self._black)
            return self._black
        room = self._ensure(cam)
        occ = frozenset(occupants) & room.relevant
        st = (occ,
              int(foxy_stage) if cam == "1C" else 0,
              bool(golden_poster) if cam == "2B" else False,
              bool(freddy_stare) and cam == "1A" and occ == frozenset([FREDDY]))
        key = (cam,) + st
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
            return img
        items = [it for it in room.items if it.cond(st)]
        if not items:
            img = room.bg
        else:
            img = room.bg.copy()
            glows = []
            for it in items:
                img.blit(it.surf, it.pos)
                glows.extend(it.glows)
            if glows:
                add_glows(img, glows)
        self._cache[key] = img
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return img

    # ===========================================================================
    # rooms. Each is a generator yielding its _Ctx after every chunk of work.
    # ===========================================================================

    # -- 1A Show Stage ---------------------------------------------------------
    def _room_1A(self):
        C = _Ctx(101, (8, 8, 16))
        C.room.relevant = frozenset([FREDDY, BONNIE, CHICA])
        pen, rng = C.pen, C.rng
        # backdrop
        pen.vgrad(0, 0, W, 560, (16, 16, 40), (30, 26, 58))
        for i in range(9):  # faint vertical panel seams
            x = 120 + i * 150
            pen.line((12, 12, 30), (x, 0), (x, 560), 2)
        for _ in range(85):
            r = rng.uniform(4, 15)
            col = rng.choice([(236, 226, 150), (220, 220, 236), (236, 200, 90), (200, 210, 240)])
            _star(pen, col, rng.uniform(150, 1290), rng.uniform(70, 520), r, rng.uniform(0, 1.2))
        # banner
        pen.rect((90, 20, 30), 470, 110, 500, 56, radius=10)
        pen.rect((200, 160, 70), 470, 110, 500, 56, radius=10, width=3)
        pen.text("FREDDY FAZBEAR'S PIZZA", 720, 139, 34, (236, 210, 120))
        yield C
        # stage floor (planks)
        top, front = 520, 590
        pen.rect((60, 38, 24), 0, top, W, front - top)
        for i in range(1, 6):
            y = lerp(top, front, (i / 6) ** 1.3)
            pen.line((40, 24, 14), (0, y), (W, y), 2)
        for i in range(-12, 13):
            pen.line((44, 28, 16), (720 + i * 60, top), (720 + i * 95, front), 2)
        # speakers
        for sx in (250, 1190):
            pen.rect((22, 22, 24), sx - 60, 390, 120, 190, radius=6)
            for cy, rr in ((440, 34), (520, 26)):
                pen.circle((10, 10, 12), sx, cy, rr)
                pen.circle((46, 46, 50), sx, cy, rr * 0.45)
        # gift boxes
        _box(pen, 1080, 540, 70, 50, (60, 110, 170), 10, 12, tape=False)
        pen.rect((220, 200, 80), 1110, 528, 10, 62)
        _box(pen, 300, 548, 56, 42, (170, 50, 60), 8, 10, tape=False)
        pen.rect((230, 220, 220), 323, 538, 9, 52)
        # curtains + valance
        _curtain(pen, 0, lambda v: 240 - 110 * math.sin(math.pi * clamp((v - 0.15) / 0.85, 0, 1)) ** 0.7,
                 0, 600, 9, (150, 20, 28), hem=10)
        _curtain(pen, W, lambda v: W - 240 + 110 * math.sin(math.pi * clamp((v - 0.15) / 0.85, 0, 1)) ** 0.7,
                 0, 600, 9, (150, 20, 28), hem=10)
        for sx in (128, W - 128):     # tie-backs
            pen.ellipse((200, 160, 60), sx, 400, 26, 10)
        _valance(pen, 0, W, 40, 70, 6, (156, 22, 30))
        yield C
        C.finish_art(0.9)
        yield C
        lights = [(470, 340, 300, 420, (150, 130, 112)), (720, 320, 320, 440, (170, 150, 128)),
                  (975, 340, 300, 420, (150, 130, 112)), (720, 180, 820, 260, (40, 40, 60)),
                  (720, 660, 900, 200, (52, 44, 38)), (110, 330, 230, 420, (70, 40, 40)),
                  (1330, 330, 230, 420, (70, 40, 40))]
        C.light((24, 24, 32), lights)
        C.bake()
        yield C
        # characters (feet at y ~ 610, hidden by the stage front)
        C.char(BONNIE, (462, 338), 45, _has(BONNIE), look=(0.55, 0.3), eyes="normal", prop=True)
        yield C
        C.char(CHICA, (982, 340), 45, _has(CHICA), look=(-0.55, 0.3), eyes="normal", prop=True)
        yield C
        C.char(FREDDY, (720, 330), 47, lambda st: FREDDY in st[0] and not st[3], look=(-0.15, 0.35),
               eyes="normal", prop=True)
        yield C
        C.char(FREDDY, (720, 306), 57, lambda st: st[3], look=(0.0, 0.0), eyes="normal", boost=1.3, prop=True)
        yield C
        # foreground: footlight ledge + stage front
        cv = C.layer_canvas((0, 566, W, 154))
        fp = cv[1]
        frng = random.Random(11)
        fp.rect((34, 22, 40), 0, 604, W, 116)
        x = 0
        while x < W:      # painted plank front
            w = frng.uniform(46, 70)
            fp.rect(shade((40, 26, 46), frng.uniform(0.85, 1.15)), x, 612, w - 3, 108)
            x += w
        for i in range(14):
            _star(fp, (150, 120, 60), 50 + i * 104, 668, 15, frng.uniform(-0.2, 0.2))
        # black and white checker trim under the ledge
        for i in range(0, W, 16):
            for r in range(2):
                fp.rect(TILE_W if (i // 16 + r) % 2 == 0 else TILE_B, i, 606 + r * 8, 16, 8)
        fp.rect((52, 34, 22), 0, 574, W, 32)
        fp.rect((210, 166, 70), 0, 572, W, 5)
        fp.rect((120, 90, 40), 0, 603, W, 3)
        bulbs = []
        for i in range(24):
            bx = 30 + i * 60
            fp.circle((60, 50, 34), bx, 590, 8)
            fp.circle((168, 156, 120), bx - 2, 588, 4)
            bulbs.append((bx, 588, 7, (34, 28, 16)))
        C.add_surface(pygame.transform.smoothscale(cv[0], cv[2].size), cv[2].topleft, lambda st: True,
                      glows=bulbs)
        yield C

    # -- 1B Dining Area --------------------------------------------------------
    def _room_1B(self):
        C = _Ctx(202, (6, 6, 8))
        C.room.relevant = frozenset([FREDDY, BONNIE, CHICA])
        pen, rng = C.pen, C.rng
        cam = _Cam(f=640, cx=720, cy=190, pos=(0.0, 3.4, 0.0))
        X0, X1, Z1, HH = -7.0, 7.0, 18.0, 4.4
        # ceiling, walls
        cam.poly(pen, (24, 24, 28), [(X0, HH, 0.6), (X1, HH, 0.6), (X1, HH, Z1), (X0, HH, Z1)])
        for z in range(2, 18, 2):
            cam.poly(pen, (16, 16, 18), [(X0, HH, z), (X1, HH, z), (X1, HH, z + 0.06), (X0, HH, z + 0.06)])
        for a, b in (((X0, 0.6), (X0, Z1)), ((X1, Z1), (X1, 0.6)), ((X0, Z1), (X1, Z1))):
            cam.wall_quad(pen, (84, 82, 96), a, b, 0, HH)
            cam.wall_quad(pen, (54, 34, 40), a, b, 0, 1.0)
            cam.wall_checker(pen, a, b, 1.0, 2, 0.18)
        # back doorway
        cam.wall_quad(pen, (6, 6, 8), (-1.0, Z1 - 0.01), (1.0, Z1 - 0.01), 0, 2.6)
        yield C
        # posters on back + side walls
        for kind, (a, b), t0, t1, y0 in (("freddy", ((X0, Z1), (X1, Z1)), 2.5, 4.3, 1.7),
                                         ("chica", ((X0, Z1), (X1, Z1)), 9.7, 11.5, 1.7),
                                         ("celebrate", ((X0, 0.6), (X0, Z1)), 6.5, 8.8, 1.55),
                                         ("pizza", ((X1, Z1), (X1, 0.6)), 5.0, 6.8, 1.6),
                                         ("rules", ((X1, Z1), (X1, 0.6)), 9.5, 10.8, 1.7)):
            tex = _poster_tex(kind, 220, int(220 * 1.4), rng)
            cam.wall_decal(C.big, tex, a, b, t0, t1, y0, y0 + (t1 - t0) * 1.4)
        yield C
        cam.checker(pen, X0, X1, 0.6, Z1, 0.7)
        yield C
        # bunting across the ceiling
        for z in (5.0, 10.0, 15.0):
            pts = [cam.p(x / 2.0, HH - 0.2 - 0.6 * (1 - (x / 14.0) ** 2), z) for x in range(-14, 15)]
            pen.lines((60, 60, 60), pts, 1.5)
            for i in range(len(pts) - 1):
                (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                c = [(200, 50, 50), (60, 100, 200), (220, 190, 40), (60, 160, 80)][i % 4]
                pen.poly(c, [(x0, y0), (x1, y1), ((x0 + x1) / 2, (y0 + y1) / 2 + 280 / z)])

        # tables (rows across the room, with a centre aisle)
        def table(pen, z, xa, xb, depth=1.0, top_y=0.78):
            zn, zf = z, z + depth
            cloth = (192, 192, 186)
            cam.poly(pen, (150, 150, 150), [(xa, top_y - 0.42, zn), (xb, top_y - 0.42, zn),
                                             (xb, top_y, zn), (xa, top_y, zn)])
            cam.poly(pen, cloth, [(xa, top_y, zn), (xb, top_y, zn), (xb, top_y, zf), (xa, top_y, zf)])
            for _ in range(int((xb - xa) * 1.5)):       # spills and stains
                sx, sz = rng.uniform(xa + 0.1, xb - 0.1), rng.uniform(zn + 0.1, zf - 0.1)
                px, py = cam.p(sx, top_y, sz)
                r = cam.f * rng.uniform(0.04, 0.1) / cam.depth(sx, sz)
                pen.ellipse(rng.choice([(170, 160, 140), (176, 150, 140), (160, 160, 150)]), px, py, r, r * 0.4)
            # hanging front cloth with folds
            n = max(2, int((xb - xa) / 0.25))
            for i in range(n):
                x0, x1 = lerp(xa, xb, i / n), lerp(xa, xb, (i + 1) / n)
                c = shade(cloth, 0.78 + 0.14 * math.cos(i * 1.9))
                cam.poly(pen, c, [(x0, top_y, zn - 0.01), (x1, top_y, zn - 0.01),
                                  (x1, top_y - 0.38, zn - 0.03), (x0, top_y - 0.38, zn - 0.03)])
            # aisle-side cloth
            ex = xb if xb < 0 else xa
            cam.poly(pen, shade(cloth, 0.6), [(ex, top_y, zn), (ex, top_y, zf), (ex, top_y - 0.38, zf),
                                              (ex, top_y - 0.38, zn)])
            # plates, cups, hats
            x = xa + 0.35
            while x < xb - 0.3:
                for zz in (zn + 0.28, zf - 0.28):
                    px, py = cam.p(x, top_y, zz)
                    d = cam.depth(x, zz)
                    rx = 640 * 0.13 / d
                    ry = rx * (cam.py - top_y) / math.hypot(cam.py - top_y, d)
                    pen.ellipse((236, 236, 236), px, py, rx, ry)
                    pen.ellipse((200, 200, 204), px, py, rx * 0.65, ry * 0.65)
                    if rng.random() < 0.55:
                        hx = x + rng.uniform(-0.25, 0.25)
                        bx, by = cam.p(hx, top_y, zz + 0.1)
                        tx, ty = cam.p(hx, top_y + 0.32, zz + 0.1)
                        hw = 640 * 0.09 / d
                        hc = rng.choice([(200, 50, 60), (60, 90, 200), (60, 170, 90), (220, 180, 40),
                                         (170, 70, 180)])
                        pen.poly(hc, [(bx - hw, by), (bx + hw, by), (tx, ty)])
                        for k in (0.33, 0.66):
                            pen.line(shade(hc, 1.5), (lerp(bx - hw, tx, k), lerp(by, ty, k)),
                                     (lerp(bx + hw, tx, k), lerp(by, ty, k)), max(1.0, hw * 0.3))
                        pen.circle((236, 236, 220), tx, ty, max(1.0, hw * 0.35))
                x += 0.62

        table(pen, 13.0, -5.8, -0.7)
        table(pen, 13.0, 0.7, 5.8)
        table(pen, 8.6, -6.4, -0.7)
        table(pen, 8.6, 0.7, 6.4)
        yield C
        C.finish_art(1.0)
        yield C
        lights = [(445, 400, 340, 360, (130, 124, 116)), (995, 400, 340, 360, (130, 124, 116)),
                  (720, 560, 1000, 260, (52, 50, 48)), (160, 260, 260, 260, (44, 42, 44)),
                  (1280, 260, 260, 260, (44, 42, 44))]
        C.light((14, 14, 17), lights, vignette=0.5)
        C.bake()
        yield C
        hb = cam.head(-2.5, 5.6)
        C.char(BONNIE, hb, cam.scale(-2.5, 5.6), _has(BONNIE), look=(0.4, 0.2))
        yield C
        hc = cam.head(2.5, 5.6)
        C.char(CHICA, hc, cam.scale(2.5, 5.6), _has(CHICA), look=(-0.4, 0.2))
        yield C
        hf = cam.head(-0.15, 15.5)
        C.char(FREDDY, hf, cam.scale(-0.15, 15.5), _has(FREDDY), eyes="glow", glow_k=1.2)
        yield C
        # foreground: the nearest row of tables
        cv = C.layer_canvas((0, 380, W, 340))
        table(cv[1], 4.0, -6.8, -0.7)
        table(cv[1], 4.0, 0.7, 6.8)
        C.add_layer(cv, lambda st: True)
        yield C

    # -- 1C Pirate Cove -------------------------------------------------------
    def _room_1C(self):
        C = _Ctx(303, (10, 8, 10))
        C.room.relevant = frozenset([FOXY])
        pen, rng = C.pen, C.rng
        cam = _Cam(f=640, cx=720, cy=250, pos=(0.0, 2.4, 0.0))
        # wall: dark wooden planks
        pen.vgrad(0, 0, W, 580, (50, 40, 46), (64, 52, 58))
        x = 0
        while x < W:
            w = rng.uniform(38, 60)
            pen.rect(shade((58, 46, 52), rng.uniform(0.85, 1.12)), x, 0, w - 3, 580)
            x += w
        for kind, x0 in (("freddy", 40), ("rules", 1230)):
            tex = _poster_tex(kind, 170 * SS, int(170 * 1.4) * SS, rng)
            C.big.blit(tex, (x0 * SS, 150 * SS))
        cam.checker(pen, -9, 9, 3.0, 12, 0.55)
        pen.rect((26, 20, 22), 0, 568, W, 12)
        # cove frame + interior
        L, R, T, B = 330, 1110, 70, 520
        pen.vgrad(L, T, R - L, B - T, (22, 12, 30), (8, 4, 10))
        for _ in range(16):
            _star(pen, (70, 60, 84), rng.uniform(L + 40, R - 40), rng.uniform(T + 70, B - 60), rng.uniform(5, 11))
        pen.rect((30, 22, 24), L, B - 40, R - L, 40)
        # stage platform
        pen.rect((60, 40, 30), L - 30, B - 6, R - L + 60, 18)
        pen.rect((36, 22, 30), L - 30, B + 12, R - L + 60, 76)
        for i in range(8):
            pen.rect((46, 30, 40), L - 20 + i * 102, B + 20, 92, 58, radius=4)
            _star(pen, (120, 100, 60), L + 26 + i * 102, B + 49, 12)
        pen.rect((196, 150, 60), L - 30, B + 10, R - L + 60, 4)
        # frame posts
        for px in (L - 34, R):
            pen.rect((44, 28, 20), px, T - 40, 34, B - T + 50)
            pen.rect((70, 48, 30), px + 4, T - 40, 6, B - T + 50)
        # sign above
        pen.rect((44, 28, 18), 500, 2, 440, 62, radius=8)
        pen.rect((196, 150, 60), 500, 2, 440, 62, radius=8, width=3)
        pen.text("PIRATE COVE", 720, 34, 44, (236, 206, 120))
        for sx in (530, 910):
            _star(pen, (220, 190, 90), sx, 33, 13)
        yield C
        C.finish_art(1.0)
        yield C
        lights = [(720, 290, 560, 440, (124, 108, 132)), (908, 470, 230, 200, (100, 96, 90)),
                  (720, 30, 300, 80, (60, 54, 40)), (720, 660, 900, 180, (40, 40, 40)),
                  (100, 250, 200, 260, (30, 28, 30)), (1340, 250, 200, 260, (30, 28, 30))]
        C.light((24, 22, 28), lights)
        C.bake()
        yield C
        # stage 1: Foxy peeking through a narrow gap (behind the curtains)
        gc = 712
        C.char(FOXY, (gc - 6, 258), 40, lambda st: st[1] == 1, eyes="glow", look=(0.5, 0.15), glow_k=1.5)
        yield C
        purple = (100, 40, 132)
        gold = (226, 196, 110)
        Lc, Rc, Tc, Bc = L - 4, R + 4, T - 4, B - 6
        mid = (Lc + Rc) / 2

        def lens(v):
            return math.sin(math.pi * clamp((v - 0.12) / 0.47, 0, 1)) ** 1.1

        def tied(v, top, tie, bot, vt=0.62):
            if v < vt:
                return lerp(top, tie, math.sin(0.5 * math.pi * v / vt))
            return lerp(tie, bot, ((v - vt) / (1 - vt)) ** 1.6)
        shapes = {
            0: (lambda v: mid + 6, lambda v: mid - 6),
            1: (lambda v: gc - 6 - 46 * lens(v), lambda v: gc + 6 + 50 * lens(v)),
            2: (lambda v: mid - tied(v, 20, 210, 150), lambda v: mid + tied(v, 30, 150, 110)),
            3: (lambda v: mid - tied(v, 190, 330, 270), lambda v: mid + tied(v, 190, 330, 270)),
        }
        for stage, (lin, rin) in shapes.items():
            cv = C.layer_canvas((Lc - 10, Tc - 10, Rc - Lc + 20, Bc - Tc + 40))
            cp = cv[1]
            srng = random.Random(77)
            _curtain(cp, Lc, lin, Tc, Bc, 7, purple, srng, stars=gold, hem=10)
            _curtain(cp, Rc, rin, Tc, Bc, 7, purple, srng, stars=gold, hem=10)
            if stage >= 2:
                for sx in (-1, 1):
                    f = lin if sx < 0 else rin
                    tx = f(0.62)
                    cp.ellipse((200, 160, 70), tx + sx * 26, Tc + 0.62 * (Bc - Tc), 30, 8)
            _valance(cp, Lc, Rc, Tc + 6, 46, 5, (114, 46, 146), fringe=gold)
            C.add_layer(cv, (lambda s: (lambda st: st[1] == s))(stage))
            yield C
        # stage 2: Foxy stepping out between the curtains (in front of them)
        C.char(FOXY, (676, 238), 46, lambda st: st[1] == 2, eyes="glow", look=(0.25, 0.2), rot=-7, glow_k=1.2)
        yield C
        # out of order sign (foreground; front-left so the camera map never covers it)
        dx = -470
        cv = C.layer_canvas((760 + dx, 380, 300, 340))
        sp = cv[1]
        sp.rect((40, 40, 44), 902 + dx, 520, 10, 180)
        sp.ellipse((30, 30, 34), 907 + dx, 702, 60, 10)
        sp.rect((214, 208, 190), 790 + dx, 410, 236, 136)
        sp.rect((60, 30, 30), 790 + dx, 410, 236, 136, width=4)
        sp.text("SORRY!", 908 + dx, 448, 40, (170, 30, 30))
        sp.text("OUT OF ORDER", 908 + dx, 500, 31, (30, 30, 30))
        C.add_layer(cv, lambda st: True)
        yield C

    # -- halls -------------------------------------------------------------------
    def _hall(self, C, cam, hw, hh, z0, z1, wall, low, ceil, band_y=1.0, tile=0.5, end_door=True):
        pen, rng = C.pen, C.rng
        L, R = (-hw, z0), (-hw, z1)
        Ra, Rb = (hw, z1), (hw, z0)
        cam.poly(pen, ceil, [(-hw, hh, z0), (hw, hh, z0), (hw, hh, z1), (-hw, hh, z1)])
        # ceiling tiles grid
        z = z0
        while z < z1:
            cam.poly(pen, shade(ceil, 0.6), [(-hw, hh, z), (hw, hh, z), (hw, hh, z + 0.04), (-hw, hh, z + 0.04)])
            z += 0.6
        for x in (-hw / 2, 0, hw / 2):
            cam.poly(pen, shade(ceil, 0.6), [(x - 0.02, hh, z0), (x + 0.02, hh, z0), (x + 0.02, hh, z1),
                                             (x - 0.02, hh, z1)])
        for a, b in ((L, R), (Ra, Rb)):
            cam.wall_quad(pen, wall, a, b, 0, hh)
            cam.wall_quad(pen, low, a, b, 0, band_y)
            cam.wall_quad(pen, shade(low, 0.45), a, b, 0, 0.12)
            cam.wall_checker(pen, a, b, band_y, 2, 0.14)
            cam.wall_quad(pen, shade(wall, 0.7), a, b, band_y + 0.28, band_y + 0.32)
            cam.wall_quad(pen, shade(wall, 0.75), a, b, hh - 0.08, hh)
            # water stains running down from the ceiling
            for _ in range(int((z1 - z0) * 1.6)):
                zz = rng.uniform(z0 + 0.3, z1 - 0.5)
                wdt = rng.uniform(0.02, 0.09)
                y_end = rng.uniform(1.4, hh - 0.3)
                x = a[0]
                cam.poly(pen, shade(wall, rng.uniform(0.72, 0.9)),
                         [(x, hh, zz), (x, hh, zz + wdt), (x, y_end + 0.2, zz + wdt * 0.6), (x, y_end, zz + wdt * 0.3)])
            for _ in range(int((z1 - z0) * 1.2)):
                zz = rng.uniform(z0 + 0.3, z1 - 0.5)
                wdt = rng.uniform(0.03, 0.12)
                x = a[0]
                cam.poly(pen, shade(low, rng.uniform(0.7, 0.85)),
                         [(x, band_y, zz), (x, band_y, zz + wdt), (x, rng.uniform(0.15, 0.6), zz + wdt * 0.5)])
        # end wall
        cam.poly(pen, shade(wall, 0.8), [(-hw, 0, z1), (hw, 0, z1), (hw, hh, z1), (-hw, hh, z1)])
        if end_door:
            cam.poly(pen, (40, 34, 30), [(-0.68, 0, z1 - 0.01), (0.68, 0, z1 - 0.01), (0.68, 2.3, z1 - 0.01),
                                          (-0.68, 2.3, z1 - 0.01)])
            cam.poly(pen, (4, 4, 6), [(-0.6, 0, z1 - 0.02), (0.6, 0, z1 - 0.02), (0.6, 2.22, z1 - 0.02),
                                       (-0.6, 2.22, z1 - 0.02)])
        cam.checker(pen, -hw, hw, z0, z1, tile)
        # pipes and a cable tray along the ceiling
        for x, r, col in ((-hw + 0.25, 0.07, (66, 68, 72)), (-hw + 0.45, 0.04, (96, 74, 52))):
            for k in range(2):
                y = hh - 0.12 - k * r
                cam.poly(pen, shade(col, 1 - 0.3 * k), [(x - r, y, z0), (x + r, y, z0), (x + r, y, z1),
                                                        (x - r, y, z1)])
        z = z0 + 0.8
        while z < z1:
            cam.poly(pen, (40, 40, 44), [(-hw + 0.2, hh, z), (-hw + 0.24, hh, z), (-hw + 0.24, hh - 0.2, z),
                                         (-hw + 0.2, hh - 0.2, z)])
            z += 1.6

    def _side_door(self, C, cam, x, za, zb, hgt=2.2, open_=True, col=(84, 62, 46)):
        pen = C.pen
        inset = -0.01 if x > 0 else 0.01
        cam.wall_quad(pen, (44, 36, 30), (x + inset, za - 0.08), (x + inset, zb + 0.08), 0, hgt + 0.08)
        cam.wall_quad(pen, (5, 5, 7) if open_ else col, (x + 2 * inset, za), (x + 2 * inset, zb), 0, hgt)

    def _lamp(self, C, cam, x, z, hh, drop=0.5):
        pen = C.pen
        top = cam.p(x, hh, z)
        sx, sy = cam.p(x, hh - drop, z)
        pen.line((30, 30, 30), top, (sx, sy), 1.5)
        d = cam.depth(x, z)
        w = cam.f * 0.28 / d
        hgt = cam.f * 0.2 / d
        pen.poly((70, 74, 70), [(sx - w * 0.35, sy), (sx + w * 0.35, sy), (sx + w, sy + hgt), (sx - w, sy + hgt)])
        pen.poly((96, 100, 96), [(sx - w * 0.35, sy), (sx - w * 0.1, sy), (sx - w * 0.4, sy + hgt),
                                 (sx - w, sy + hgt)])
        pen.ellipse((250, 240, 200), sx, sy + hgt, w * 0.95, hgt * 0.25)
        return (sx, sy + hgt)

    def _hall_posters(self, C, cam, hw, z0, z1, posters):
        for kind, side, t0, t1, y0 in posters:
            tex = _poster_tex(kind, 300, 420, C.rng)
            a, b = ((-hw + 0.005, z0), (-hw + 0.005, z1)) if side < 0 else ((hw - 0.005, z0), (hw - 0.005, z1))
            cam.wall_decal(C.big, tex, a, b, t0, t1, y0, y0 + (t1 - t0) * 1.4)

    # -- 2A West Hall -----------------------------------------------------------
    def _room_2A(self):
        C = _Ctx(404)
        C.room.relevant = frozenset([BONNIE])
        cam = _Cam(f=690, cx=720, cy=250, pos=(0.0, 2.4, 0.0))
        hw, hh, z0, z1 = 1.6, 3.0, 0.6, 15.0
        self._hall(C, cam, hw, hh, z0, z1, (96, 100, 110), (72, 70, 82), (34, 34, 38))
        yield C
        self._side_door(C, cam, hw, 8.2, 9.3)
        self._side_door(C, cam, -hw, 11.0, 12.0, open_=False)
        self._hall_posters(C, cam, hw, z0, z1, [("freddy", -1, 1.8, 2.7, 1.45), ("rules", -1, 4.6, 5.3, 1.5),
                                                ("celebrate", 1, 2.3, 3.2, 1.45), ("pizza", 1, 5.6, 6.3, 1.5),
                                                ("paper", -1, 7.2, 7.7, 1.6)])
        lamp_pos = self._lamp(C, cam, 0.0, 3.4, hh, drop=0.3)
        self._lamp(C, cam, 0.0, 10.5, hh)
        yield C
        C.finish_art(1.1)
        yield C
        lx, ly = lamp_pos
        fx, fy = cam.p(0, 0, 4.4)
        hb = cam.head(-0.2, 6.2)
        lights = [(lx, ly + 60, 330, 260, (120, 114, 92)), (fx, fy, 380, 120, (100, 94, 78)),
                  (hb[0], hb[1] + 60, 190, 260, (96, 94, 92)),
                  (cam.p(0, 0, 10.5)[0], cam.p(0, 0, 10.5)[1], 120, 40, (40, 38, 32)),
                  (720, 720, 900, 240, (40, 40, 42))]
        C.light((18, 19, 22), lights)
        self._run_mask = pygame.transform.smoothscale(C.mask, (W // 8, H // 8))
        self._run_cam = cam
        C.bake([(lx, ly, 30, (100, 94, 64))])
        yield C
        sc = cam.scale(-0.2, 6.2)
        C.shadow(cam.p(-0.2, 0, 6.2), 1.5 * sc, 0.45 * sc, _has(BONNIE))
        C.char(BONNIE, hb, sc, _has(BONNIE), look=(0.1, 0.15))
        yield C

    # -- 4A East Hall -----------------------------------------------------------
    def _room_4A(self):
        C = _Ctx(808)
        C.room.relevant = frozenset([CHICA, FREDDY])
        rng = C.rng
        cam = _Cam(f=690, cx=720, cy=245, pos=(0.2, 2.4, 0.0))
        hw, hh, z0, z1 = 1.6, 3.0, 0.6, 14.0
        self._hall(C, cam, hw, hh, z0, z1, (90, 102, 98), (52, 60, 76), (30, 32, 34))
        yield C
        # children's drawings on both walls
        for side in (-1, 1):
            a, b = ((-hw + 0.005, z0), (-hw + 0.005, z1)) if side < 0 else ((hw - 0.005, z0), (hw - 0.005, z1))
            t = 0.9
            while t < 10.0:
                for y0 in (1.42, 2.12):
                    if rng.random() < 0.18:
                        continue
                    w = rng.uniform(0.4, 0.55)
                    tex = _kid_drawing(120, 120 * rng.uniform(1.15, 1.35), rng)
                    hgt = w * tex.get_height() / tex.get_width()
                    yy = y0 + rng.uniform(-0.05, 0.08)
                    cam.wall_decal(C.big, tex, a, b, t, t + w, yy, yy + hgt)
                t += rng.uniform(0.55, 0.75)
            yield C
        self._side_door(C, cam, -hw, 10.6, 11.6)
        # fluorescent fixtures
        for z in (3.6, 9.0):
            cam.poly(C.pen, (60, 62, 64), [(-0.5, hh - 0.01, z), (0.5, hh - 0.01, z), (0.5, hh - 0.01, z + 0.25),
                                           (-0.5, hh - 0.01, z + 0.25)])
            cam.poly(C.pen, (210, 220, 210), [(-0.45, hh - 0.02, z + 0.06), (0.45, hh - 0.02, z + 0.06),
                                              (0.45, hh - 0.02, z + 0.19), (-0.45, hh - 0.02, z + 0.19)])
        C.finish_art(1.15)
        yield C
        hc = cam.head(-0.3, 5.2)
        hf = cam.head(0.75, 10.0)
        tx, ty = cam.p(0, hh, 3.7)
        lights = [(tx, ty + 80, 420, 330, (100, 106, 100)),
                  (hc[0], hc[1] + 60, 210, 280, (110, 108, 100)),
                  (cam.p(0, 0, 5.5)[0], cam.p(0, 0, 5.5)[1], 360, 120, (76, 76, 70)),
                  (720, 720, 900, 240, (36, 38, 40))]
        C.light((16, 18, 20), lights)
        tx2, ty2 = cam.p(0, hh, 9.1)
        C.bake([(tx, ty, 34, (60, 66, 60)), (tx2, ty2, 16, (40, 44, 40))])
        yield C
        sf, sc = cam.scale(0.75, 10.0), cam.scale(-0.3, 5.2)
        C.shadow(cam.p(0.75, 0, 10.0), 1.5 * sf, 0.4 * sf, _has(FREDDY))
        C.char(FREDDY, hf, sf, _has(FREDDY), eyes="glow", glow_k=1.3, boost=0.7)
        yield C
        C.shadow(cam.p(-0.3, 0, 5.2), 1.5 * sc, 0.45 * sc, _has(CHICA))
        C.char(CHICA, hc, sc, _has(CHICA), look=(0.0, 0.2))
        yield C

    # -- 2B W. Hall Corner ------------------------------------------------------
    def _room_2B(self):
        C = _Ctx(505)
        C.room.relevant = frozenset([BONNIE])
        pen, rng = C.pen, C.rng
        cam = _Cam(f=620, cx=720, cy=250, pos=(0.0, 2.2, 0.0))
        corner = (0.55, 3.3)
        la, rb = (-4.5, 0.9), (5.0, 1.0)
        for a, b in ((la, corner), (corner, rb)):
            cam.wall_quad(pen, (96, 100, 110), a, b, 0, 3.2)
            cam.wall_quad(pen, (72, 70, 82), a, b, 0, 1.0)
            cam.wall_quad(pen, (34, 32, 38), a, b, 0, 0.12)
            cam.wall_checker(pen, a, b, 1.0, 2, 0.14)
            cam.wall_quad(pen, (66, 68, 76), a, b, 1.28, 1.32)
            _wall_stains(cam, pen, rng, a, b, 3.2, 1.0, 26, (96, 100, 110), (72, 70, 82))
        cam.poly(pen, (30, 30, 34), [(la[0], 3.2, la[1]), (corner[0], 3.2, corner[1]), (rb[0], 3.2, rb[1]),
                                     (0, 3.2, 0.3)])
        # floor
        cam.checker(pen, -5, 5, 0.8, 3.4, 0.5)
        # re-draw walls' bottom edge over floor overshoot
        yield C
        posters = [("rules", (la, corner), 3.4, 4.1, 1.5), ("celebrate", (corner, rb), 0.5, 1.75, 1.35),
                   ("paper", (corner, rb), 2.15, 2.65, 1.75), ("pizza", (la, corner), 2.2, 3.0, 1.5)]
        for kind, (a, b), t0, t1, y0 in posters:
            tex = _poster_tex(kind, 260, int(260 * 1.4), rng)
            cam.wall_decal(C.big, tex, a, b, t0, t1, y0, y0 + (t1 - t0) * 1.4)
        yield C
        C.finish_art(1.0)
        yield C
        px, py = cam.p(corner[0] + (rb[0] - corner[0]) * 0.12, 2.2, corner[1] + (rb[1] - corner[1]) * 0.12)
        lights = [(px + 60, py, 420, 380, (130, 126, 112)), (420, 330, 320, 380, (120, 116, 112)),
                  (720, 700, 900, 220, (30, 30, 32))]
        C.light((20, 20, 24), lights)
        C.bake()
        yield C
        # golden poster patch
        x0 = int(cam.p(corner[0] + (rb[0] - corner[0]) * 0.1, 0, corner[1] + (rb[1] - corner[1]) * 0.1)[0])
        cv = C.layer_canvas((x0 - 20, 0, W - x0 + 20, H))
        tex = _poster_tex("golden", 260, int(260 * 1.4), random.Random(5))
        cam.wall_decal(cv[0], tex, corner, rb, 0.5, 1.75, 1.35, 1.35 + 1.25 * 1.4, origin=cv[2].topleft)
        C.add_layer(cv, lambda st: st[2])
        yield C
        C.char(BONNIE, (410, 345), 98, _has(BONNIE), eyes="pinpoint", rot=-10)
        yield C

    # -- 4B E. Hall Corner ------------------------------------------------------
    def _room_4B(self):
        C = _Ctx(909)
        C.room.relevant = frozenset([CHICA, FREDDY])
        pen, rng = C.pen, C.rng
        cam = _Cam(f=600, cx=720, cy=215, pos=(0.0, 2.1, 0.0), yaw=0.12)
        a, b = (-4.0, 2.75), (4.5, 2.95)
        cam.wall_quad(pen, (90, 102, 98), a, b, 0, 3.2)
        cam.wall_quad(pen, (52, 60, 76), a, b, 0, 1.0)
        cam.wall_quad(pen, (30, 32, 38), a, b, 0, 0.12)
        cam.wall_checker(pen, a, b, 1.0, 2, 0.14)
        _wall_stains(cam, pen, rng, a, b, 3.2, 1.0, 30, (90, 102, 98), (52, 60, 76))
        cam.checker(pen, -5, 5, 0.9, 3.0, 0.5)
        cam.poly(pen, (28, 30, 32), [(a[0], 3.2, a[1]), (b[0], 3.2, b[1]), (b[0], 3.2, 0.5), (a[0], 3.2, 0.5)])
        _box3(cam, pen, 1.1, 1.7, 0, 0.12, 2.3, 2.75, (40, 40, 44))     # floor vent / mat
        yield C
        t = 0.6
        while t < 8.2:
            for y0 in (1.42, 2.1):
                w = rng.uniform(0.38, 0.5)
                tex = _kid_drawing(120, 120 * rng.uniform(1.1, 1.3), rng)
                hgt = w * tex.get_height() / tex.get_width()
                yy = y0 + rng.uniform(-0.06, 0.06)
                if not (2.6 < t < 3.4 and y0 > 2):
                    cam.wall_decal(C.big, tex, a, b, t, t + w, yy, yy + hgt)
            t += rng.uniform(0.5, 0.62)
        tex = _newspaper(160, 190, rng)
        cam.wall_decal(C.big, tex, a, b, 2.65, 3.45, 1.9, 1.9 + 0.8 * tex.get_height() / tex.get_width())
        yield C
        C.finish_art(1.0)
        yield C
        lights = [(1010, 330, 330, 400, (120, 112, 100)), (720, 250, 600, 300, (50, 52, 50)),
                  (380, 340, 260, 300, (36, 36, 38))]
        C.light((16, 17, 19), lights)
        C.bake()
        yield C
        C.char(FREDDY, (390, 340), 72, _has(FREDDY), eyes="glow", glow_k=1.3, boost=0.75, rot=4)
        yield C
        C.char(CHICA, (1010, 330), 70, _has(CHICA), eyes="pinpoint", mouth=0.3, rot=-5)
        yield C

    # -- 3 Supply Closet --------------------------------------------------------
    def _room_3(self):
        C = _Ctx(606)
        C.room.relevant = frozenset([BONNIE])
        pen, rng = C.pen, C.rng
        cam = _Cam(f=600, cx=720, cy=220, pos=(0.0, 2.1, 0.0))
        hw, hh, zb = 1.45, 2.6, 3.7
        cam.poly(pen, (30, 30, 30), [(-hw, hh, 0.5), (hw, hh, 0.5), (hw, hh, zb), (-hw, hh, zb)])
        for a, b in (((-hw, 0.4), (-hw, zb)), ((hw, zb), (hw, 0.4)), ((-hw, zb), (hw, zb))):
            cam.wall_quad(pen, (84, 88, 80), a, b, 0, hh)
            cam.wall_quad(pen, (60, 64, 58), a, b, 0, 0.9)
            cam.wall_quad(pen, (40, 42, 38), a, b, 0, 0.1)
            for _ in range(10):
                t = rng.uniform(0.05, 0.95)
                px, pz = lerp(a[0], b[0], t), lerp(a[1], b[1], t)
                d = 0.05 if a[0] == b[0] else 0.0
                dx = 0.0 if a[0] == b[0] else rng.uniform(0.02, 0.08)
                yb = rng.uniform(0.8, 2.0)
                cam.poly(pen, shade((84, 88, 80), rng.uniform(0.7, 0.88)),
                         [(px, hh, pz), (px + dx, hh, pz + d), (px + dx * 0.5, yb, pz + d * 0.5)])
        cam.checker(pen, -hw, hw, 0.4, zb, 0.3, c1=(150, 150, 138), c2=(58, 58, 56))
        yield C
        palette = [(150, 110, 70), (130, 96, 60), (170, 130, 84), (120, 100, 70)]
        bottle_cols = [(40, 90, 190), (220, 220, 210), (230, 180, 40), (60, 150, 70), (190, 50, 50)]

        def shelf_back(x0, x1, z, levels=(0.3, 0.8, 1.3, 1.8, 2.3)):
            for y in levels:
                cam.poly(pen, (118, 120, 124), [(x0, y, z), (x1, y, z), (x1, y, z - 0.45), (x0, y, z - 0.45)])
                x = x0 + 0.04
                while x < x1 - 0.12 and y < 2.2:
                    if rng.random() < 0.5:
                        w = rng.uniform(0.22, 0.36)
                        h = rng.uniform(0.18, min(0.42, 2.25 - y))
                        if x + w > x1 - 0.02:
                            break
                        _box3(cam, pen, x, x + w, y, y + h, z - 0.4, z - 0.05, rng.choice(palette),
                              label=rng.random() < 0.5)
                        x += w + 0.03
                    else:
                        bx, by = cam.p(x + 0.06, y, z - 0.25)
                        s = cam.f / cam.depth(x, z - 0.25)
                        _bottle(pen, bx, by, rng.uniform(0.18, 0.3) * s, rng.choice(bottle_cols),
                                rng.randint(0, 2))
                        x += 0.14
                cam.poly(pen, (78, 80, 84), [(x0, y, z - 0.45), (x1, y, z - 0.45), (x1, y - 0.05, z - 0.45),
                                             (x0, y - 0.05, z - 0.45)])
            for x in (x0, x1):
                for zz in (z, z - 0.45):
                    cam.poly(pen, (100, 102, 108), [(x - 0.02, 0, zz), (x + 0.02, 0, zz), (x + 0.02, 2.45, zz),
                                                    (x - 0.02, 2.45, zz)])

        def shelf_side(side, z0, z1, levels=(0.3, 0.8, 1.3, 1.8, 2.3)):
            xw = side * hw
            xi = side * (hw - 0.45)
            for y in levels:
                cam.poly(pen, (118, 120, 124), [(xw, y, z0), (xi, y, z0), (xi, y, z1), (xw, y, z1)])
                z = z1 - 0.05
                while z > z0 + 0.3 and y < 2.2:
                    d = rng.uniform(0.25, 0.4)
                    h = rng.uniform(0.18, min(0.42, 2.25 - y))
                    if rng.random() < 0.7:
                        xa, xb = (xw - side * 0.05, xw - side * 0.4)
                        _box3(cam, pen, min(xa, xb), max(xa, xb), y, y + h, z - d, z, rng.choice(palette))
                    else:
                        bx, by = cam.p(xi + side * 0.12, y, z - 0.1)
                        s = cam.f / cam.depth(xi, z - 0.1)
                        _bottle(pen, bx, by, rng.uniform(0.18, 0.3) * s, rng.choice(bottle_cols),
                                rng.randint(0, 2))
                    z -= d + 0.04
                cam.poly(pen, (78, 80, 84), [(xi, y, z0), (xi, y, z1), (xi, y - 0.05, z1), (xi, y - 0.05, z0)])
            for zz in (z0, z1):
                cam.poly(pen, (100, 102, 108), [(xi - 0.02, 0, zz), (xi + 0.02, 0, zz), (xi + 0.02, 2.45, zz),
                                                (xi - 0.02, 2.45, zz)])

        shelf_back(-1.4, -0.55, zb - 0.02)
        shelf_back(0.55, 1.4, zb - 0.02)
        yield C
        shelf_side(-1, 1.2, zb - 0.5)
        shelf_side(1, 1.2, zb - 0.5)
        yield C
        # breaker box + clipboard on the back wall between the shelves
        bx0, by0 = cam.p(-0.25, 1.95, zb - 0.01)
        bx1, by1 = cam.p(0.2, 1.4, zb - 0.01)
        pen.rect((110, 112, 106), bx0, by0, bx1 - bx0, by1 - by0)
        pen.rect((70, 72, 68), bx0, by0, bx1 - bx0, by1 - by0, width=2)
        pen.rect((200, 160, 40), bx0 + 6, by0 + 6, 24, 10)
        cx0, cy0 = cam.p(0.28, 1.7, zb - 0.01)
        pen.rect((120, 90, 60), cx0, cy0, 40, 54)
        pen.rect((214, 210, 196), cx0 + 4, cy0 + 8, 32, 42)
        for i in range(5):
            pen.line((100, 100, 100), (cx0 + 8, cy0 + 16 + i * 7), (cx0 + 30, cy0 + 16 + i * 7), 1.2)
        # bare bulb
        bx, by = cam.p(0, hh - 0.32, 2.5)
        pen.line((20, 20, 20), cam.p(0, hh, 2.5), (bx, by), 2)
        pen.rect((90, 80, 60), bx - 6, by - 4, 12, 10)
        pen.circle((250, 244, 210), bx, by + 14, 11)
        # mop bucket + mop (front right), broom (left)
        kx, ky = cam.p(0.78, 0, 3.0)
        s = cam.f / cam.depth(0.78, 3.0)
        pen.poly((196, 170, 40), [(kx - 0.24 * s, ky - 0.36 * s), (kx + 0.24 * s, ky - 0.36 * s),
                                  (kx + 0.2 * s, ky), (kx - 0.2 * s, ky)])
        pen.rect((150, 130, 30), kx - 0.24 * s, ky - 0.36 * s, 0.48 * s, 0.05 * s)
        pen.ellipse((40, 44, 30), kx, ky - 0.36 * s, 0.22 * s, 0.05 * s)
        pen.rect((80, 80, 84), kx - 0.12 * s, ky - 0.5 * s, 0.24 * s, 0.16 * s)
        for wx in (-0.16, 0.16):
            pen.circle((30, 30, 30), kx + wx * s, ky, 0.035 * s)
        pen.line((160, 120, 70), (kx - 0.02 * s, ky - 0.42 * s), cam.p(1.25, 1.75, 3.1), 6)
        pen.poly((190, 186, 170), [(kx - 0.1 * s, ky - 0.36 * s), (kx + 0.06 * s, ky - 0.36 * s),
                                   (kx + 0.02 * s, ky - 0.52 * s), (kx - 0.06 * s, ky - 0.52 * s)])
        b0 = cam.p(-0.95, 0, 3.05)
        b1 = cam.p(-1.25, 1.55, 3.3)
        pen.line((150, 110, 60), b0, b1, 5)
        pen.poly((150, 120, 60), [(b0[0] - 34, b0[1]), (b0[0] + 34, b0[1]), (b0[0] + 14, b0[1] - 50),
                                  (b0[0] - 14, b0[1] - 50)])
        for i in range(9):
            pen.line((120, 96, 50), (b0[0] - 30 + i * 7.5, b0[1]), (b0[0] - 12 + i * 3, b0[1] - 46), 1.2)
        C.finish_art(1.15)
        yield C
        hb = cam.head(0.0, 2.55)
        lights = [(bx, by + 90, 520, 400, (160, 150, 120)), (hb[0], hb[1] + 70, 240, 330, (92, 90, 84)),
                  (720, 720, 800, 200, (34, 34, 34))]
        C.light((17, 17, 19), lights)
        C.bake([(bx, by + 14, 24, (96, 90, 60))])
        yield C
        C.char(BONNIE, hb, cam.scale(0.0, 2.55), _has(BONNIE), eyes="pinpoint", look=(0, 0))
        yield C

    # -- 5 Backstage -------------------------------------------------------------
    def _room_5(self):
        C = _Ctx(1010)
        C.room.relevant = frozenset([BONNIE])
        pen, rng = C.pen, C.rng
        cam = _Cam(f=600, cx=720, cy=220, pos=(0.0, 2.2, 0.0))
        hw, hh, zb = 2.6, 2.9, 4.0
        cam.poly(pen, (24, 24, 26), [(-hw, hh, 0.5), (hw, hh, 0.5), (hw, hh, zb), (-hw, hh, zb)])
        for a, b in (((-hw, 0.4), (-hw, zb)), ((hw, zb), (hw, 0.4)), ((-hw, zb), (hw, zb))):
            cam.wall_quad(pen, (74, 66, 74), a, b, 0, hh)
            cam.wall_quad(pen, (46, 40, 46), a, b, 0, 0.9)
            for _ in range(14):
                t = rng.uniform(0.03, 0.97)
                px, pz = lerp(a[0], b[0], t), lerp(a[1], b[1], t)
                d = 0.06 if a[0] == b[0] else 0.0
                dx = 0.0 if a[0] == b[0] else rng.uniform(0.02, 0.1)
                yb = rng.uniform(0.9, 2.2)
                cam.poly(pen, shade((74, 66, 74), rng.uniform(0.7, 0.88)),
                         [(px, hh, pz), (px + dx, hh, pz + d), (px + dx * 0.5, yb, pz + d * 0.5)])
        cam.checker(pen, -hw, hw, 0.4, zb, 0.5)
        # shelves on the back wall
        for y in (1.25, 2.05):
            cam.poly(pen, (96, 74, 52), [(-2.5, y, zb), (2.5, y, zb), (2.5, y, zb - 0.4), (-2.5, y, zb - 0.4)])
            cam.poly(pen, (62, 46, 32), [(-2.5, y, zb - 0.4), (2.5, y, zb - 0.4), (2.5, y - 0.06, zb - 0.4),
                                         (-2.5, y - 0.06, zb - 0.4)])
            for x in (-2.3, 0.0, 2.3):
                cam.poly(pen, (50, 40, 30), [(x - 0.03, y - 0.06, zb), (x + 0.03, y - 0.06, zb),
                                             (x + 0.03, y - 0.3, zb), (x - 0.03, y - 0.3, zb)])
        # boxes on the floor (left)
        _box3(cam, pen, -2.5, -1.9, 0, 0.5, 2.6, 3.2, (130, 96, 60), label=True)
        _box3(cam, pen, -2.45, -2.0, 0.5, 0.85, 2.7, 3.15, (150, 110, 70))
        _box3(cam, pen, -1.85, -1.35, 0, 0.4, 3.0, 3.5, (120, 90, 60))
        yield C
        # spare heads on the shelves
        heads = [("Endo", "none"), (FREDDY, "none"), (CHICA, "none"), ("Endo", "none"), (BONNIE, "none"),
                 (FREDDY, "none"), ("Endo", "none"), (BONNIE, "none"), (CHICA, "none")]
        i = 0
        for y, xs in ((1.25, (-2.0, -0.95, 0.1, 1.15, 2.15)), (2.05, (-1.95, -0.85, 0.25, 1.3, 2.2))):
            for x in xs:
                name, eyes = heads[i % len(heads)]
                i += 1
                hx, hy = cam.p(x, y + 0.27, zb - 0.2)
                sc = cam.f * U * 0.85 / cam.depth(x, zb - 0.2) * SS
                spr = _head(name, sc, eyes=eyes)
                C.big.blit(spr.surface, (hx * SS - spr.anchor[0], hy * SS - spr.anchor[1]))
            yield C
        # workbench with a slumped endoskeleton (right)
        tx0, tx1, tz0, tz1, ty = 0.5, 2.5, 1.9, 2.8, 0.85
        for lx in (tx0 + 0.08, tx1 - 0.08):
            for lz in (tz0 + 0.08, tz1 - 0.08):
                _box3(cam, pen, lx - 0.04, lx + 0.04, 0, ty - 0.15, lz - 0.04, lz + 0.04, (60, 44, 30))
        _box3(cam, pen, tx0, tx1, ty - 0.15, ty, tz0, tz1, (126, 96, 64))
        cam.poly(pen, (90, 68, 46), [(tx0, ty - 0.15, tz0), (tx1, ty - 0.15, tz0), (tx1, ty - 0.11, tz0),
                                     (tx0, ty - 0.11, tz0)])
        px, py = cam.p(1.2, ty + 0.05, 2.15)
        s = cam.f * U / cam.depth(1.2, 2.15) * 0.82
        _endo_slumped(pen, C.big, px, py, s)
        # tools on the bench
        wx, wy = cam.p(0.75, ty, 2.0)
        pen.line((150, 150, 156), (wx, wy), (wx + 60, wy - 8), 5)
        pen.circle((150, 150, 156), wx + 60, wy - 8, 9, width=4)
        sx, sy = cam.p(2.0, ty, 2.0)
        pen.line((180, 40, 40), (sx - 40, sy), (sx, sy - 4), 6)
        pen.line((170, 170, 176), (sx, sy - 4), (sx + 36, sy - 8), 2)
        # hanging work lamp over the bench
        lx, ly = cam.p(1.9, hh - 0.4, 2.6)
        pen.line((24, 24, 24), cam.p(1.9, hh, 2.6), (lx, ly), 2)
        pen.poly((60, 64, 60), [(lx - 20, ly), (lx + 20, ly), (lx + 44, ly + 34), (lx - 44, ly + 34)])
        pen.ellipse((250, 240, 200), lx, ly + 34, 40, 8)
        yield C
        C.finish_art(1.15)
        yield C
        hb = cam.head(-0.55, 2.4)
        lights = [(lx - 60, ly + 260, 440, 380, (136, 124, 104)), (hb[0], hb[1] + 70, 300, 380, (108, 104, 98)),
                  (720, 220, 700, 200, (40, 38, 40)), (300, 560, 300, 200, (40, 38, 36))]
        C.light((15, 15, 17), lights)
        C.bake([(lx, ly + 34, 30, (90, 84, 60))])
        yield C
        C.char(BONNIE, hb, cam.scale(-0.55, 2.4), _has(BONNIE), eyes="glow", look=(0, 0), glow_k=0.9)
        yield C

    # -- 7 Restrooms -------------------------------------------------------------
    def _room_7(self):
        C = _Ctx(707)
        C.room.relevant = frozenset([CHICA, FREDDY])
        pen = C.pen
        cam = _Cam(f=660, cx=720, cy=250, pos=(0.0, 2.4, 0.0))
        hw, hh, z0, z1 = 1.5, 2.9, 0.6, 8.0
        self._hall(C, cam, hw, hh, z0, z1, (104, 108, 104), (78, 92, 104), (32, 32, 34), tile=0.4,
                   end_door=False, band_y=1.1)
        yield C
        # restroom doors with signs
        for side, za, zb, label in ((-1, 3.0, 4.0, "BOYS"), (1, 4.6, 5.6, "GIRLS")):
            x = side * hw
            self._side_door(C, cam, x, za, zb, hgt=2.15, open_=False, col=(108, 78, 54))
            hx, hy = cam.p(x - side * 0.02, 1.0, zb - 0.12 if side < 0 else za + 0.12)
            pen.circle((170, 170, 160), hx, hy, 3.5)
            # kick plate
            cam.wall_quad(pen, (130, 128, 120), (x - side * 0.025, za + 0.05), (x - side * 0.025, zb - 0.05), 0.05,
                          0.3)
            tex = pygame.Surface((160, 220))
            tp = Pen(tex, 0, 0, 1.6)
            tp.rect((206, 206, 200), 0, 0, 100, 137)
            col = (40, 70, 160) if side < 0 else (170, 40, 70)
            tp.circle(col, 50, 24, 12)
            if side < 0:
                tp.rect(col, 34, 40, 32, 48, radius=4)
                tp.rect(col, 36, 86, 11, 36)
                tp.rect(col, 53, 86, 11, 36)
            else:
                tp.poly(col, [(50, 38), (76, 102), (24, 102)])
                tp.rect(col, 38, 100, 9, 22)
                tp.rect(col, 53, 100, 9, 22)
            tp.text(label, 50, 130, 14, (20, 20, 20))
            tp.rect((60, 60, 60), 0, 0, 100, 137, width=2)
            cam.wall_decal(C.big, tex, (x - side * 0.03, z0), (x - side * 0.03, z1), za - z0 + 0.25,
                           zb - z0 - 0.25, 1.3, 1.3 + 0.5 * 1.37)
        # end wall: sink, mirror, hand dryer, sign
        mx0, my0 = cam.p(-0.7, 2.0, z1 - 0.01)
        mx1, my1 = cam.p(0.3, 1.2, z1 - 0.01)
        pen.rect((60, 66, 70), mx0, my0, mx1 - mx0, my1 - my0)
        pen.rect((120, 124, 126), mx0, my0, mx1 - mx0, my1 - my0, width=2)
        pen.line((96, 104, 108), (mx0 + 10, my1 - 6), (mx0 + 40, my0 + 6), 2)
        sx, sy = cam.p(-0.2, 0.85, z1 - 0.3)
        pen.rect((180, 180, 176), sx - 34, sy - 6, 68, 14, radius=4)
        pen.rect((150, 150, 146), sx - 6, sy + 8, 12, 40)
        dx, dy = cam.p(0.9, 1.3, z1 - 0.01)
        pen.rect((170, 170, 170), dx - 16, dy - 12, 32, 26, radius=5)
        sx, sy = cam.p(0, 2.55, z1 - 0.01)
        pen.rect((30, 60, 30), sx - 50, sy - 10, 100, 20)
        pen.text("RESTROOMS", sx, sy, 14, (200, 230, 200))
        yield C
        C.finish_art(1.25)
        yield C
        hc = cam.head(-0.5, 3.5)
        hf = cam.head(0.62, 6.2)
        lights = [(hc[0], hc[1] + 80, 280, 340, (124, 120, 110)), (hf[0], hf[1] + 30, 170, 230, (70, 70, 68)),
                  (720, 180, 360, 160, (60, 62, 60)), (720, 720, 900, 220, (38, 38, 40)),
                  (cam.p(0, 0, 4)[0], cam.p(0, 0, 4)[1], 420, 160, (70, 70, 64))]
        C.light((18, 19, 20), lights)
        C.bake()
        yield C
        sf, sc = cam.scale(0.62, 6.2), cam.scale(-0.5, 3.5)
        C.shadow(cam.p(0.62, 0, 6.2), 1.5 * sf, 0.45 * sf, _has(FREDDY))
        C.char(FREDDY, hf, sf, _has(FREDDY), eyes="glow", glow_k=1.2, boost=0.9)
        yield C
        C.shadow(cam.p(-0.5, 0, 3.5), 1.5 * sc, 0.5 * sc, _has(CHICA))
        C.char(CHICA, hc, sc, _has(CHICA), look=(0.3, 0.2))
        yield C

    # ===========================================================================
    # Foxy running down the West Hall
    # ===========================================================================
    RUN_SCALE = 128

    def _build_run(self):
        if "2A" not in self._rooms:
            for _ in self._build_room("2A"):
                yield
        frames = []
        for step in (0, 1):
            spr = characters.render_character(FOXY, self.RUN_SCALE, ss=2, pose="run", step=step, eyes="glow")
            s = spr.surface.copy()
            s.fill((170, 176, 172), special_flags=pygame.BLEND_RGB_MULT)
            s.fill(CAST, special_flags=pygame.BLEND_RGB_MULT)
            mips = [_alpha(s)]
            w, h = s.get_size()
            for k in (2, 4):
                mips.append(_alpha(pygame.transform.smoothscale(s, (max(1, w // k), max(1, h // k)))))
            frames.append((mips, spr.anchor, spr.glows))
            yield
        self._run = frames

    def draw_foxy_run(self, dest, x_offset, progress):
        """Draw Foxy sprinting toward the camera over the 2A feed."""
        if self._run is None:
            for _ in self._build_run():
                pass
        p = clamp(progress, 0.0, 1.0)
        cam = self._run_cam
        z = lerp(13.0, 0.45, p ** 1.15)
        x = -0.6 * _smoothstep(0.55, 1.0, p) ** 2           # veers off to the left...
        lift = 0.8 * _smoothstep(0.7, 1.0, p)               # ...and leaps at the lens
        scale = cam.scale(x, z)
        hx, hy = cam.p(x, 6.0 * U + lift, z)
        step = int(p * 22) % 2
        mips, (ax, ay), glows = self._run[step]
        k = scale / self.RUN_SCALE
        lvl = 2 if k < 0.25 else 1 if k < 0.5 else 0
        src = mips[lvl]
        sk = k * (2 ** lvl)
        sw, sh = src.get_size()
        # visible part of the scaled sprite
        left = hx + x_offset - ax * k
        top = hy - ay * k
        dw, dh = dest.get_size()
        vx0, vy0 = max(0.0, -left), max(0.0, -top)
        vx1, vy1 = min(sw * sk, dw - left), min(sh * sk, dh - top)
        if vx1 <= vx0 or vy1 <= vy0:
            return
        sr = pygame.Rect(int(vx0 / sk), int(vy0 / sk), int(math.ceil((vx1 - vx0) / sk)) + 1,
                         int(math.ceil((vy1 - vy0) / sk)) + 1).clip(src.get_rect())
        if sr.w <= 0 or sr.h <= 0:
            return
        part = pygame.transform.scale(src.subsurface(sr), (max(1, int(sr.w * sk)), max(1, int(sr.h * sk))))
        # light: sample the hall's light where he is
        mx = int(clamp((hx) / 8, 0, self._run_mask.get_width() - 1))
        my = int(clamp((hy + 2 * scale) / 8, 0, self._run_mask.get_height() - 1))
        c = self._run_mask.get_at((mx, my))
        near = clamp((p - 0.6) / 0.4, 0, 1)
        lc = tuple(int(clamp(c[i] * 1.6 + 40 * near + 30, 0, 255)) for i in range(3))
        part.fill(lc, special_flags=pygame.BLEND_RGB_MULT)
        dest.blit(part, (int(left + sr.x * sk), int(top + sr.y * sk)))
        if glows:
            add_glows(dest, [(left + gx * k, top + gy * k, max(2, gr * k), gc) for gx, gy, gr, gc in glows])
