"""The security office: a wide panorama seen through the 1280x720 screen.

The office is ``OFFICE_W`` x 720 (1920x720) and is viewed through the screen
at office x = ``scroll`` (0 .. OFFICE_W - SCREEN_W). Everything is
pre-rendered by ``Office.build()`` (a generator that yields between chunks of
work so a loading screen can update); ``Office.draw()`` then only blits
prepared pieces.

Geometry: one-point perspective with the vanishing point at (VPX, VPY). The
back wall is a flat plane at depth ZWALL; world units are roughly metres
(eye at the origin, +X right, +Y down, +Z into the screen), so a world point
(X, Y, Z) lands at office pixel (VPX + FOCAL*X/Z, VPY + FOCAL*Y/Z).

Public API:
    DOORWAYS  {"L"/"R": Rect}             door openings (office coords)
    BUTTONS   {(side, "door"/"light"): Rect}  click targets (office coords)
    NOSE_RECT Rect                         Freddy's nose on the CELEBRATE! poster
    Office().build() / Office().draw(...)
"""

import math
import random

import pygame

from . import characters
from .settings import OFFICE_W, SCREEN_H, SCREEN_W
from .util import Pen, add_glows, clamp, lerp, lerp_color, make_light_mask, render_ss, shade

W, H = OFFICE_W, SCREEN_H
_SS = 2  # supersampling of the big static canvas

# Perspective.
VPX, VPY = W // 2, 300
FOCAL = 600.0
ZWALL = 2.4

# Horizontal structure of the back wall (office y).
CEIL_Y = 70
BAND_TOP = 392          # black/white checker band: 3 rows of 14 px
BAND_SQ = 14
BAND_BOT = BAND_TOP + 3 * BAND_SQ
LOWER_TOP = BAND_BOT + 5
BASE_TOP = 596
FLOOR_Y = 610
FLOOR_WY = (FLOOR_Y - VPY) * ZWALL / FOCAL    # floor plane, world Y (1.24)
CEIL_WY = (CEIL_Y - VPY) * ZWALL / FOCAL      # ceiling plane, world Y (-0.92)

# ---------------------------------------------------------------------------
# Public geometry
# ---------------------------------------------------------------------------

DOORWAYS = {
    "L": pygame.Rect(140, 130, 280, 480),
    "R": pygame.Rect(W - 420, 130, 280, 480),
}

_PANEL = pygame.Rect(20, 250, 90, 246)            # left panel; right is mirrored
_BTN_C = {"door": (65, 326), "light": (65, 430)}  # button centres (left side)
_BTN_RAD = 25

BUTTONS = {}
for _side in "LR":
    for _kind, (_bx, _by) in _BTN_C.items():
        _r = pygame.Rect(0, 0, 68, 68)
        _r.center = (_bx if _side == "L" else W - _bx, _by)
        BUTTONS[(_side, _kind)] = _r
del _side, _kind, _bx, _by, _r

POSTER = pygame.Rect(462, 140, 228, 284)
# name -> (x, y, scale) of the head centre on the poster.
_POSTER_HEADS = {"Bonnie": (517, 320, 26), "Chica": (635, 320, 26), "Freddy": (576, 300, 33)}
_NOSE_LOCAL = (0.0, 0.32)  # Freddy's nose relative to the head centre, in head units
NOSE_RECT = pygame.Rect(0, 0, 28, 22)
NOSE_RECT.center = (int(_POSTER_HEADS["Freddy"][0] + _NOSE_LOCAL[0] * _POSTER_HEADS["Freddy"][2]),
                    int(_POSTER_HEADS["Freddy"][1] + _NOSE_LOCAL[1] * _POSTER_HEADS["Freddy"][2]))

# Desk (a box seen from the front; top at world Y=0.48 between Z 1.45 .. 2.15).
DESK_TOP_WY = 0.48
DESK_FAR_Z, DESK_NEAR_Z = 2.15, 1.45
DESK_HALF_W = 0.95

FAN_C = (1012, 372)
FAN_R = 60
FAN_RECT = pygame.Rect(FAN_C[0] - 70, FAN_C[1] - 70, 140, 140)
FAN_FRAMES = 8          # frames over 120 degrees (three blades)
FAN_FPS = 30.0

# Animatronics standing right outside a doorway: head centre (office coords)
# and scale. The real figure would be smaller; they are shown big and close.
DOOR_CHAR_SCALE = 64
DOOR_CHAR_HEAD = {"L": (282, 252), "R": (W - 282, 252)}

GOLDEN_SCALE = 80
GOLDEN_HEAD = (VPX, 452)

_HALL_Z_IN = ZWALL + 0.14   # back face of the wall (door jamb depth)
_HALL_Z_FAR = 4.4           # the hallway's far wall
_HALL_CEIL_WY = -0.98

# Spill-light regions in front of a lit doorway (left side; right mirrored).
_SPILL_RECTS_L = [pygame.Rect(104, 86, 352, FLOOR_Y - 86), pygame.Rect(0, FLOOR_Y, 520, H - FLOOR_Y)]
_SPILL_LEVELS = (1.0, 0.62, 0.3)

# Palette.
WALL_A = (60, 70, 80)        # top of the upper wall
WALL_B = (100, 112, 122)     # just above the checker band
LOW_A = (66, 78, 80)
LOW_B = (46, 56, 58)
TILE_W = (196, 196, 186)
TILE_K = (22, 22, 25)
STEEL = (86, 90, 96)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _proj(X, Y, Z):
    return (VPX + FOCAL * X / Z, VPY + FOCAL * Y / Z)


def _wx(x, z=ZWALL):
    """Office x at depth z -> world X."""
    return (x - VPX) * z / FOCAL


def _mirror_rect(r):
    return pygame.Rect(W - r.right, r.y, r.w, r.h)


def _side_rect(r, side):
    return r if side == "L" else _mirror_rect(r)


def _cv(surf, alpha=False):
    """convert()/convert_alpha() when a display exists (faster blits)."""
    if pygame.display.get_init() and pygame.display.get_surface() is not None:
        try:
            return surf.convert_alpha() if alpha else surf.convert()
        except pygame.error:
            pass
    return surf


def _const(size, color):
    s = pygame.Surface(size)
    s.fill(color)
    return s


def _mult(surf, mask, pos=(0, 0), area=None):
    surf.blit(mask, pos, area, special_flags=pygame.BLEND_RGB_MULT)


def _rand_bytes(rng, n):
    fn = getattr(rng, "randbytes", None)
    if fn is not None:
        return fn(n)
    return bytes(rng.getrandbits(8) for _ in range(n))


def _noise(w, h, lo, hi, rng):
    """Opaque grey noise with values in lo..hi."""
    w, h = max(2, int(w)), max(2, int(h))
    data = _rand_bytes(rng, w * h)
    table = bytes(int(lo + (hi - lo) * i / 255.0) for i in range(256))
    g = data.translate(table)
    rgb = bytearray(w * h * 3)
    rgb[0::3] = g
    rgb[1::3] = g
    rgb[2::3] = g
    return pygame.image.frombuffer(bytes(rgb), (w, h), "RGB").copy()


def _stains(w, h, cell, lo, hi, rng):
    small = _noise(w / cell, h / cell, lo, hi, rng)
    return pygame.transform.smoothscale(small, (w, h))


def _boost(mask, k):
    """Scale a mask's brightness by k (any positive factor, clamped at 255)."""
    out = mask.copy()
    while k >= 2.0:
        out.blit(out, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
        k /= 2.0
    if k > 1.0:
        extra = out.copy()
        v = int(255 * (k - 1.0))
        _mult(extra, _const(extra.get_size(), (v, v, v)))
        out.blit(extra, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    elif k < 1.0:
        v = int(255 * k)
        _mult(out, _const(out.get_size(), (v, v, v)))
    return out


def _lit_sprite(surf, mask_crop):
    """RGB-multiply an alpha sprite by a mask of the same size (alpha kept)."""
    out = surf.copy()
    out.blit(mask_crop, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    return out


def _silhouette(surf, alpha):
    out = surf.copy()
    out.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)
    out.fill((255, 255, 255, int(alpha)), special_flags=pygame.BLEND_RGBA_MULT)
    return out


def _grad_surface(w, h, c0, c1, vertical=True):
    n = 24
    col = pygame.Surface((1, n) if vertical else (n, 1))
    for i in range(n):
        c = lerp_color(c0, c1, i / float(n - 1))
        col.set_at((0, i) if vertical else (i, 0), c)
    return pygame.transform.smoothscale(col, (w, h))


def _pix_rect(p, x, y, w, h):
    x0, y0 = p.pt(x, y)
    x1, y1 = p.pt(x + w, y + h)
    left, top = int(round(min(x0, x1))), int(round(min(y0, y1)))
    return pygame.Rect(left, top, int(round(max(x0, x1))) - left, int(round(max(y0, y1))) - top)


def _vgrad(p, x, y, w, h, top, bottom):
    """Fast vertical gradient rectangle (same semantics as Pen.vgrad)."""
    r = _pix_rect(p, x, y, w, h)
    if r.w > 0 and r.h > 0:
        p.surf.blit(_grad_surface(r.w, r.h, top[:3], bottom[:3]), r.topleft)


def _hgrad(p, x, y, w, h, left, right):
    """Fast horizontal gradient rectangle (mirrors with the pen, like Pen.hgrad)."""
    r = _pix_rect(p, x, y, w, h)
    if p.fx < 0:
        left, right = right, left
    if r.w > 0 and r.h > 0:
        p.surf.blit(_grad_surface(r.w, r.h, left[:3], right[:3], vertical=False), r.topleft)


def _cyl_h(p, x0, x1, yc, r, base):
    """Horizontal pipe with cylindrical shading."""
    _vgrad(p, x0, yc - r, x1 - x0, r * 0.55, shade(base, 0.55), shade(base, 1.45))
    _vgrad(p, x0, yc - r * 0.45, x1 - x0, r * 1.45, shade(base, 1.45), shade(base, 0.35))


def _cyl_v(p, x, y0, y1, r, base):
    """Vertical pipe with cylindrical shading."""
    _hgrad(p, x - r, y0, r * 0.8, y1 - y0, shade(base, 0.45), shade(base, 1.35))
    _hgrad(p, x - r * 0.2, y0, r * 1.2, y1 - y0, shade(base, 1.35), shade(base, 0.4))


def _bolt(p, x, y, r=3.0, col=(110, 112, 116)):
    p.circle(shade(col, 0.35), x + 0.6, y + 0.8, r)
    p.circle(col, x, y, r)
    p.circle(shade(col, 1.5), x - r * 0.3, y - r * 0.3, r * 0.4)


def _upper_col(y):
    return lerp_color(WALL_A, WALL_B, (y - CEIL_Y) / float(BAND_TOP - 6 - CEIL_Y))


def _lower_col(y):
    return lerp_color(LOW_A, LOW_B, (y - LOWER_TOP) / float(BASE_TOP - LOWER_TOP))


def _catenary(xa, ya, xb, yb, sag, n=24):
    pts = []
    for i in range(n + 1):
        u = i / float(n)
        pts.append((lerp(xa, xb, u), lerp(ya, yb, u) + sag * 4 * u * (1 - u)))
    return pts


# ---------------------------------------------------------------------------
# Static scene: ceiling, walls, floor
# ---------------------------------------------------------------------------

def _draw_ceiling(p, rng):
    p.rect((10, 10, 12), 0, 0, W, CEIL_Y + 2)
    zs = [ZWALL, 2.12, 1.86, 1.6]
    tw = 0.62
    xmin, xmax = _wx(-300, zs[-1]), _wx(W + 300, zs[-1])
    for k in range(len(zs) - 1):
        za, zb = zs[k], zs[k + 1]
        for j in range(int(math.floor(xmin / tw)), int(math.ceil(xmax / tw))):
            X0, X1 = j * tw, (j + 1) * tw
            quad = [_proj(X0, CEIL_WY, za), _proj(X1, CEIL_WY, za), _proj(X1, CEIL_WY, zb), _proj(X0, CEIL_WY, zb)]
            v = rng.randint(58, 74)
            col = (v, v, v + 4)
            roll = rng.random()
            if roll < 0.07:
                col = (4, 4, 5)                       # missing tile
            elif roll < 0.25:
                col = (v + 14, v + 4, v - 14)         # water-stained
            p.poly(col, quad)
            p.poly((24, 24, 26), quad, width=3)
    # Shadow line where ceiling meets wall.
    p.rect((8, 8, 9), 0, CEIL_Y - 2, W, 4)


def _draw_walls(p, rng):
    top = BAND_TOP - 6
    _vgrad(p, 0, CEIL_Y, W, top - CEIL_Y, WALL_A, WALL_B)
    # Faint panel seams.
    for x in range(96, W, 192):
        _vgrad(p, x, CEIL_Y, 2, top - CEIL_Y, shade(WALL_A, 0.8), shade(WALL_B, 0.82))
        _vgrad(p, x + 2, CEIL_Y, 1, top - CEIL_Y, shade(WALL_A, 1.08), shade(WALL_B, 1.06))
    # Water / grime streaks running down from the ceiling.
    for _ in range(110):
        x = rng.uniform(0, W)
        y0 = CEIL_Y + rng.uniform(0, 30) if rng.random() < 0.7 else rng.uniform(CEIL_Y, top - 60)
        ln = rng.uniform(25, 300)
        y1 = min(top, y0 + ln)
        w = rng.uniform(1.5, 10)
        k = rng.uniform(0.66, 0.9)
        _vgrad(p, x, y0, w, y1 - y0, shade(_upper_col(y0), k), _upper_col(y1))


def _draw_walls_lower(p, rng):
    top = BAND_TOP - 6
    # Checker band with dark trims.
    p.rect((34, 36, 40), 0, top, W, 6)
    p.rect((70, 74, 80), 0, top, W, 1.5)
    for row in range(3):
        for col in range(W // BAND_SQ + 1):
            c = TILE_W if (row + col) % 2 else TILE_K
            if c is TILE_W and rng.random() < 0.08:
                c = shade(TILE_W, rng.uniform(0.6, 0.85))   # chipped / dirty tile
            p.rect(c, col * BAND_SQ, BAND_TOP + row * BAND_SQ, BAND_SQ, BAND_SQ)
    p.rect((34, 36, 40), 0, BAND_BOT, W, 5)
    p.rect((64, 68, 72), 0, BAND_BOT + 4, W, 1)
    # Lower wall: vertical boards.
    _vgrad(p, 0, LOWER_TOP, W, BASE_TOP - LOWER_TOP, LOW_A, LOW_B)
    for x in range(0, W, 48):
        _vgrad(p, x, LOWER_TOP, 2, BASE_TOP - LOWER_TOP, shade(LOW_A, 0.72), shade(LOW_B, 0.72))
    # Scuffs near the floor.
    for _ in range(60):
        x = rng.uniform(0, W)
        y = rng.uniform(BASE_TOP - 60, BASE_TOP - 4)
        c = shade(_lower_col(y), rng.uniform(0.55, 0.8))
        p.line(c, (x, y), (x + rng.uniform(6, 30), y + rng.uniform(-3, 3)), rng.uniform(1, 2.5))
    # Baseboard.
    p.rect((26, 26, 28), 0, BASE_TOP, W, FLOOR_Y - BASE_TOP)
    p.rect((52, 52, 56), 0, BASE_TOP, W, 2)


def _draw_floor_tiles(p, x_lo, x_hi, z_far, z_near, tile=0.26, light=(122, 122, 114), dark=(24, 24, 26),
                      rng=None):
    zs = [z_far]
    while zs[-1] > z_near:
        zs.append(max(z_near, zs[-1] - tile))
    xmin = min(_wx(x_lo, zs[0]), _wx(x_lo, zs[-1]))
    xmax = max(_wx(x_hi, zs[0]), _wx(x_hi, zs[-1]))
    for k in range(len(zs) - 1):
        za, zb = zs[k], zs[k + 1]
        kk = int(round((ZWALL - za) / tile))
        for j in range(int(math.floor(xmin / tile)) - 1, int(math.ceil(xmax / tile)) + 1):
            X0, X1 = j * tile, (j + 1) * tile
            quad = [_proj(X0, FLOOR_WY, za), _proj(X1, FLOOR_WY, za), _proj(X1, FLOOR_WY, zb),
                    _proj(X0, FLOOR_WY, zb)]
            c = light if (j + kk) % 2 == 0 else dark
            if rng is not None and c is light:
                c = shade(c, rng.uniform(0.7, 1.0))
            p.poly(c, quad)


def _draw_floor(p, rng):
    p.rect((14, 14, 16), 0, FLOOR_Y, W, H - FLOOR_Y)
    _draw_floor_tiles(p, -100, W + 100, ZWALL, 1.2, rng=rng)
    # Dirt in the corner where floor meets wall.
    _vgrad(p, 0, FLOOR_Y, W, 6, (14, 14, 15), (40, 40, 40))


def _draw_pipes(p, rng):
    # Two pipes along the top of the wall, behind the door housings.
    _cyl_h(p, 0, W, 81, 8, (78, 86, 76))
    _cyl_h(p, 0, W, 97, 4, (104, 72, 52))
    for x in range(40, W, 210):
        p.rect((40, 42, 44), x - 3, CEIL_Y, 6, 22)          # hanger
        p.rect((58, 64, 58), x - 7, 71, 14, 20)              # coupling
        p.rect((96, 104, 92), x - 7, 73, 14, 3)
    for x in range(140, W, 330):
        p.rect((34, 30, 28), x - 2, 92, 5, 10)               # clamp on the copper pipe


# ---------------------------------------------------------------------------
# Doors and panels (drawn for the left side; a mirrored pen draws the right)
# ---------------------------------------------------------------------------

def _hazard(p, x, y, w, h, stripe=16, slant=None, yellow=(226, 178, 30), black=(22, 22, 22)):
    """Black/yellow diagonal stripes clipped to a rectangle."""
    slant = h if slant is None else slant
    p.rect(black, x, y, w, h)
    # Clip in pixel space of the pen's surface.
    x0, y0 = p.pt(x, y)
    x1, y1 = p.pt(x + w, y + h)
    clip = pygame.Rect(int(min(x0, x1)), int(min(y0, y1)), int(abs(x1 - x0)) + 1, int(abs(y1 - y0)) + 1)
    old = p.surf.get_clip()
    p.surf.set_clip(clip.clip(old))
    sx = x - slant - stripe * 2
    while sx < x + w + stripe:
        p.poly(yellow, [(sx, y + h), (sx + stripe, y + h), (sx + stripe + slant, y), (sx + slant, y)])
        sx += stripe * 2
    p.surf.set_clip(old)


def _jamb_polys():
    """Inner faces visible inside the left doorway (office coords)."""
    r = DOORWAYS["L"]
    xl, xr = r.x, r.right
    xl_in = _proj(_wx(xl), 0, _HALL_Z_IN)[0]
    xr_in = _proj(_wx(xr), 0, _HALL_Z_IN)[0]
    ytop_in = _proj(0, (r.y - VPY) * ZWALL / FOCAL, _HALL_Z_IN)[1]
    ybot_in = _proj(0, FLOOR_WY, _HALL_Z_IN)[1]
    left = [(xl, r.y), (xl_in, ytop_in), (xl_in, ybot_in), (xl, r.bottom)]
    header = [(xl, r.y), (xr, r.y), (min(xr, xr_in), ytop_in), (xl_in, ytop_in)]
    sill = [(xl, r.bottom), (xr, r.bottom), (min(xr, xr_in), ybot_in), (xl_in, ybot_in)]
    return left, header, sill


def _draw_doorframe(p, rng):
    r = DOORWAYS["L"]
    x0, x1 = r.x - 28, r.right + 28
    ytop = r.y - 42
    p.rect((26, 28, 32), x0 - 3, ytop - 2, x1 - x0 + 6, FLOOR_Y - ytop + 2, radius=3)
    # The opening itself is a black void in the static image.
    p.rect((3, 3, 4), r.x, r.y, r.w, r.h)
    # Posts.
    for a, b in ((x0, r.x), (r.right, x1)):
        p.rect(shade(STEEL, 0.5), a, ytop, b - a, FLOOR_Y - ytop)
        _hgrad(p, a + 2, ytop, b - a - 4, FLOOR_Y - ytop, shade(STEEL, 1.15), shade(STEEL, 0.8))
        p.rect(shade(STEEL, 1.45), a + 2, ytop, 2, FLOOR_Y - ytop)
        for y in range(ytop + 64, FLOOR_Y - 20, 66):
            _bolt(p, (a + b) / 2.0, y, 3.2)
        # Dents and scratches.
        for _ in range(6):
            y = rng.uniform(ytop + 50, FLOOR_Y - 10)
            p.line(shade(STEEL, 0.55), (a + 4, y), (b - 4, y + rng.uniform(-6, 6)), 1)
    # Shutter tracks next to the opening.
    p.rect((14, 14, 16), r.x - 6, r.y, 6, r.h)
    p.rect((14, 14, 16), r.right, r.y, 6, r.h)
    p.rect(shade(STEEL, 0.9), r.x - 6, r.y, 1.5, r.h)
    p.rect(shade(STEEL, 0.9), r.right + 4.5, r.y, 1.5, r.h)
    # Header housing that hides the raised shutter.
    hx0, hx1 = x0 - 8, x1 + 8
    p.rect((16, 16, 18), hx0 + 3, ytop + 3, hx1 - hx0, r.y - ytop + 2)
    _vgrad(p, hx0, ytop - 2, hx1 - hx0, r.y - ytop - 6, shade(STEEL, 1.25), shade(STEEL, 0.75))
    p.rect(shade(STEEL, 1.7), hx0, ytop - 2, hx1 - hx0, 2)
    for x in range(int(hx0) + 14, int(hx1) - 8, 42):
        _bolt(p, x, ytop + 6, 2.6)
    # The bottom edge of the raised door peeks out of the slot.
    _hazard(p, r.x - 6, r.y - 12, r.w + 12, 9, stripe=10, slant=9)
    p.rect((8, 8, 9), r.x - 6, r.y - 3, r.w + 12, 3)
    p.rect((8, 8, 9), hx0, r.y - 12, r.x - 6 - hx0, 12)
    p.rect((8, 8, 9), r.right + 6, r.y - 12, hx1 - r.right - 6, 12)
    # Floor sill plate in front of the doorway (perspective).
    zs, zn = ZWALL, ZWALL - 0.12
    X0, X1 = _wx(r.x - 10), _wx(r.right + 10)
    sill = [_proj(X0, FLOOR_WY, zs), _proj(X1, FLOOR_WY, zs), _proj(X1, FLOOR_WY, zn), _proj(X0, FLOOR_WY, zn)]
    p.poly((70, 72, 74), sill)
    p.poly((30, 30, 32), sill, width=1.5)
    for i in range(14):
        u = (i + 0.5) / 14
        a = (lerp(sill[0][0], sill[1][0], u), lerp(sill[0][1], sill[1][1], u))
        b = (lerp(sill[3][0], sill[2][0], u), lerp(sill[3][1], sill[2][1], u))
        p.line((96, 98, 100), (lerp(a[0], b[0], 0.3), lerp(a[1], b[1], 0.3)),
               (lerp(a[0], b[0], 0.7), lerp(a[1], b[1], 0.7)), 1.2)


def _draw_panel(p):
    r = _PANEL
    cx = r.centerx
    # Conduit running from the panel up to the ceiling, plus a junction box.
    _cyl_v(p, cx, CEIL_Y, r.y, 6, (78, 80, 84))
    for y in (CEIL_Y + 40, CEIL_Y + 110):
        p.rect((40, 40, 44), cx - 9, y, 18, 6)
        _bolt(p, cx, y + 3, 1.6)
    # Plate.
    p.rect((14, 14, 16), r.x + 4, r.y + 6, r.w, r.h, radius=6)
    p.rect((40, 42, 46), r.x, r.y, r.w, r.h, radius=6)
    _vgrad(p, r.x + 3, r.y + 3, r.w - 6, r.h - 6, (92, 95, 102), (62, 64, 70))
    p.rect((120, 124, 130), r.x + 3, r.y + 3, r.w - 6, 2)
    for sx, sy in ((r.x + 9, r.y + 9), (r.right - 9, r.y + 9), (r.x + 9, r.bottom - 9), (r.right - 9, r.bottom - 9)):
        _bolt(p, sx, sy, 3)
    for kind, (bx, by) in _BTN_C.items():
        p.rect((18, 18, 20), bx - 33, by - 33, 66, 66, radius=6)
        _vgrad(p, bx - 31, by - 31, 62, 62, (52, 53, 58), (36, 37, 40))
        p.circle((8, 8, 9), bx, by, _BTN_RAD + 4)
        p.circle((70, 72, 76), bx, by, _BTN_RAD + 4, width=1.5)
        label = "DOOR" if kind == "door" else "LIGHT"
        p.text(label, bx, by - 45, 15, (236, 236, 226))


def _draw_cap(p, kind, lit):
    """A round button cap centred at (0, 0) (radius _BTN_RAD)."""
    R = _BTN_RAD
    if kind == "door":
        if lit:
            rim, body, hi, core = (150, 20, 14), (255, 46, 34), (255, 170, 150), (255, 228, 214)
        else:
            rim, body, hi, core = (52, 8, 8), (118, 20, 18), (170, 64, 56), None
    else:
        if lit:
            rim, body, hi, core = (170, 170, 160), (250, 250, 240), (255, 255, 255), (255, 255, 255)
        else:
            rim, body, hi, core = (50, 50, 50), (104, 104, 100), (156, 156, 150), None
    p.circle(rim, 0, 0, R)
    p.circle(body, 0, 0.6, R - 2.5)
    if core:
        p.circle(lerp_color(body, core, 0.5), 0, 0.6, R * 0.7)
        p.circle(core, 0, 0.6, R * 0.38)
    p.ellipse(hi, -R * 0.28, -R * 0.38, R * 0.36, R * 0.2)


def _shadow_mask():
    """Soft ambient-occlusion / contact shadows for the background (multiply)."""
    k = 8
    low = pygame.Surface((W // k, H // k))
    low.fill((255, 255, 255))
    q = Pen(low, 0, 0, 1.0 / k)
    m = Pen(low, W / float(k), 0, 1.0 / k, flip=True)
    # Under the ceiling and along the floor.
    _vgrad(q, 0, CEIL_Y, W, 70, (120, 120, 120), (255, 255, 255))
    _vgrad(q, 0, BASE_TOP - 40, W, 40, (255, 255, 255), (170, 170, 170))
    for pen in (q, m):
        d = DOORWAYS["L"]
        pen.rect((110, 110, 110), d.x - 52, d.y - 60, d.w + 104, FLOOR_Y - d.y + 60)
        pen.rect((120, 120, 120), _PANEL.x - 12, _PANEL.y - 8, _PANEL.w + 28, _PANEL.h + 26)
    q.rect((120, 120, 120), POSTER.x + 2, POSTER.y + 8, POSTER.w + 12, POSTER.h + 10)
    for x, y, w, h, ang, subj in _DRAWINGS:
        q.rect((180, 180, 180), x - w / 2 + 2, y - h / 2 + 6, w + 8, h + 8)
    # Behind the desk and its clutter.
    q.rect((150, 150, 150), 620, 404, 680, 50)
    q.rect((130, 130, 130), 700, 250, 170, 220)
    q.rect((140, 140, 140), 856, 396, 110, 80)
    q.circle((170, 170, 170), FAN_C[0] - 8, FAN_C[1] + 10, FAN_R)
    q.rect((160, 160, 160), 1096, 410, 220, 70)
    # Extra blur: shrink further, then scale back up in two bilinear steps.
    tiny = pygame.transform.smoothscale(low, (W // (k * 3), H // (k * 3)))
    mid = pygame.transform.smoothscale(tiny, (W // 4, H // 4))
    return pygame.transform.smoothscale(mid, (W * _SS, H * _SS))


def _draw_outlets(p):
    """Wall outlets with power cords snaking to the desk."""
    for x, y, cord in ((520, 548, [(520, 560), (532, 594), (548, 606), (575, 612)]),
                       (1384, 552, [(1384, 564), (1376, 596), (1366, 607), (1348, 611)])):
        p.rect((40, 38, 34), x - 9, y - 13, 18, 26, radius=2)
        p.rect((150, 142, 120), x - 8, y - 12, 16, 24, radius=2)
        for dy in (-6, 5):
            p.rect((40, 36, 30), x - 3, y + dy - 2, 2, 4)
            p.rect((40, 36, 30), x + 1, y + dy - 2, 2, 4)
        p.rect((24, 24, 24), x - 5, y + 2, 10, 8, radius=2)
        p.lines((18, 18, 20), cord, 3)


def _draw_vent(p):
    x, y, w, h = 1040, 124, 104, 46
    p.rect((20, 20, 22), x - 3, y - 3, w + 6, h + 6, radius=3)
    p.rect((82, 86, 90), x, y, w, h, radius=2)
    for i in range(7):
        yy = y + 5 + i * 5.6
        p.rect((24, 24, 26), x + 6, yy, w - 12, 3)
        p.rect((120, 124, 128), x + 6, yy + 3, w - 12, 1)
    for sx in (x + 4, x + w - 4):
        for sy in (y + 4, y + h - 4):
            _bolt(p, sx, sy, 1.6)
    # Dust streak below.
    _vgrad(p, x + 20, y + h + 3, w - 40, 60, (60, 66, 74), _upper_col(y + h + 63))


# ---------------------------------------------------------------------------
# Poster and kids' drawings
# ---------------------------------------------------------------------------

def _draw_poster(p, big, rng):
    r = POSTER
    # Shadow and paper.
    _vgrad(p, r.x, r.y, r.w, r.h, (44, 36, 86), (20, 16, 40))
    # Spotlight rays behind the heads (clipped to the poster).
    cx, cy = r.centerx, r.y + 175
    old_clip = big.get_clip()
    big.set_clip(pygame.Rect(r.x * _SS, r.y * _SS, r.w * _SS, r.h * _SS))
    for i in range(16):
        a0 = i * math.tau / 16
        a1 = a0 + math.tau / 32
        p.poly((56, 46, 104), [(cx, cy), (cx + math.cos(a0) * 300, cy + math.sin(a0) * 300),
                               (cx + math.cos(a1) * 300, cy + math.sin(a1) * 300)])
    big.set_clip(old_clip)
    # Confetti.
    cols = [(240, 70, 80), (250, 210, 60), (90, 200, 240), (120, 230, 120), (250, 140, 220)]
    for _ in range(70):
        x = rng.uniform(r.x + 6, r.right - 6)
        y = rng.uniform(r.y + 6, r.bottom - 50)
        c = rng.choice(cols)
        if rng.random() < 0.5:
            p.circle(c, x, y, rng.uniform(1.5, 3))
        else:
            a = rng.uniform(0, math.pi)
            dx, dy = math.cos(a) * 4, math.sin(a) * 4
            p.line(c, (x - dx, y - dy), (x + dx, y + dy), 2.5)
    # Title.
    p.text("CELEBRATE!", r.centerx + 2, r.y + 34, 40, (190, 30, 40))
    p.text("CELEBRATE!", r.centerx, r.y + 32, 40, (252, 246, 228))
    # Heads (head-only character art), Freddy in front.
    for name in ("Bonnie", "Chica", "Freddy"):
        x, y, sc = _POSTER_HEADS[name]
        spr = characters.render_character(name, sc * _SS, ss=1, body=False, eyes="normal", look=(0.0, 0.25))
        spr.blit(big, (x * _SS, y * _SS))
    # Banner.
    p.rect((150, 24, 30), r.x + 10, r.bottom - 42, r.w - 20, 28)
    p.rect((210, 50, 56), r.x + 10, r.bottom - 42, r.w - 20, 3)
    p.text("FREDDY FAZBEAR'S PIZZA", r.centerx, r.bottom - 28, 15, (252, 224, 110))
    # Torn corner and tape.
    p.poly(_upper_col(r.bottom), [(r.right - 22, r.bottom + 1), (r.right + 1, r.bottom + 1), (r.right + 1, r.bottom - 26)])
    p.poly((180, 176, 160), [(r.right - 22, r.bottom), (r.right, r.bottom - 26), (r.right - 14, r.bottom - 12)])
    for tx, ty, a in ((r.x + 2, r.y + 2, -35), (r.right - 2, r.y + 2, 35), (r.x + 2, r.bottom - 2, 35)):
        _tape(p, tx, ty, a)


def _tape(p, x, y, angle_deg):
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    w, h = 13, 5
    pts = [(x + ca * dx - sa * dy, y + sa * dx + ca * dy) for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))]
    p.poly((196, 186, 150), pts)


def _crayon(p, color, pts, width, rng, passes=2):
    for k in range(passes):
        j = width * 0.35
        p.lines(color, [(x + rng.uniform(-j, j), y + rng.uniform(-j, j)) for x, y in pts], width * (1 - 0.25 * k))


def _crayon_circle(p, color, cx, cy, rx, ry, width, rng, fill=None):
    n = 22
    pts = [(cx + math.cos(i / n * math.tau) * rx * rng.uniform(0.94, 1.06),
            cy + math.sin(i / n * math.tau) * ry * rng.uniform(0.94, 1.06)) for i in range(n + 2)]
    if fill:
        # Scribbled fill.
        for k in range(int(ry * 2 / (width * 0.9))):
            yy = cy - ry + (k + 0.5) * width * 0.9
            dy = (yy - cy) / ry
            if abs(dy) >= 1:
                continue
            dx = rx * math.sqrt(1 - dy * dy) * 0.92
            p.line(fill, (cx - dx, yy + rng.uniform(-1, 1)), (cx + dx, yy + rng.uniform(-1, 1)), width * 0.7)
    _crayon(p, color, pts, width, rng)


def _drawing_surface(w, h, subject, seed):
    """A crayon drawing on paper, rendered at _SS x size (alpha)."""
    rng = random.Random(seed)

    def draw(pen):
        q = pen.sub(0, 0, _SS)
        paper = rng.choice([(232, 226, 204), (226, 222, 210), (236, 224, 196)])
        q.rect(paper, 0, 0, w, h)
        q.rect(shade(paper, 0.85), 0, h - 3, w, 3)
        cx = w * rng.uniform(0.42, 0.58)
        cy = h * 0.42
        s = min(w, h) / 70.0
        # Sun in a corner.
        if rng.random() < 0.7:
            sx, sy = (w - 12, 12) if rng.random() < 0.5 else (12, 12)
            _crayon_circle(q, (240, 190, 30), sx, sy, 6, 6, 2, rng, fill=(250, 214, 60))
            for i in range(7):
                a = i / 7 * math.tau
                _crayon(q, (240, 190, 30), [(sx + math.cos(a) * 8, sy + math.sin(a) * 8),
                                            (sx + math.cos(a) * 12, sy + math.sin(a) * 12)], 1.6, rng, 1)
        # Grass.
        _crayon(q, (60, 170, 60), [(2 + i * (w - 4) / 8.0, h - 8 + rng.uniform(-2, 2)) for i in range(9)], 2.2, rng)
        if subject == "kid":
            col, fill = (40, 40, 40), None
        else:
            col, fill = {"Freddy": ((110, 60, 30), (160, 100, 50)), "Bonnie": ((80, 60, 170), (130, 110, 220)),
                         "Chica": ((220, 170, 20), (250, 220, 70)), "Foxy": ((190, 50, 30), (230, 100, 60))}[subject]
        hr = 11 * s
        # Ears / hat / beak.
        if subject == "Bonnie":
            for ex in (-0.45, 0.45):
                _crayon_circle(q, col, cx + ex * hr, cy - hr * 1.9, hr * 0.28, hr * 0.9, 2, rng, fill=fill)
        elif subject in ("Freddy", "Foxy"):
            for ex in (-0.8, 0.8):
                _crayon_circle(q, col, cx + ex * hr, cy - hr * 0.85, hr * 0.32, hr * 0.32, 2, rng, fill=fill)
        _crayon_circle(q, col, cx, cy, hr, hr * 0.92, 2.2, rng, fill=fill)
        if subject == "Freddy":
            q.rect((30, 30, 30), cx - hr * 0.45, cy - hr * 1.55, hr * 0.9, hr * 0.6)
            q.rect((30, 30, 30), cx - hr * 0.7, cy - hr * 1.0, hr * 1.4, 2)
        if subject == "Chica":
            q.poly((240, 120, 30), [(cx - hr * 0.4, cy + hr * 0.1), (cx + hr * 0.4, cy + hr * 0.1), (cx, cy + hr * 0.55)])
        if subject == "Foxy":
            q.circle((30, 30, 30), cx + hr * 0.35, cy - hr * 0.2, hr * 0.22)
        # Eyes and smile.
        for ex in (-0.35, 0.35):
            if subject == "Foxy" and ex > 0:
                continue
            q.circle((20, 20, 20), cx + ex * hr, cy - hr * 0.2, max(1.2, hr * 0.12))
        if subject != "Chica":
            _crayon(q, (180, 30, 30), [(cx - hr * 0.45, cy + hr * 0.3), (cx, cy + hr * 0.55),
                                       (cx + hr * 0.45, cy + hr * 0.3)], 1.6, rng)
        # Stick body.
        by = cy + hr
        _crayon(q, col, [(cx, by), (cx, by + hr * 1.3)], 2.2, rng)
        _crayon(q, col, [(cx - hr, by + hr * 0.5), (cx, by + hr * 0.3), (cx + hr, by + hr * 0.5)], 2, rng)
        _crayon(q, col, [(cx - hr * 0.6, by + hr * 2.2), (cx, by + hr * 1.3), (cx + hr * 0.6, by + hr * 2.2)], 2, rng)
        # A little kid holding its hand.
        kx = cx + hr * (1.9 if cx < w / 2 else -1.9)
        ky = cy + hr * 0.9
        _crayon_circle(q, (40, 40, 40), kx, ky, hr * 0.42, hr * 0.42, 1.6, rng)
        _crayon(q, (40, 40, 40), [(kx, ky + hr * 0.42), (kx, ky + hr * 1.3)], 1.6, rng)
        _crayon(q, (40, 40, 40), [(kx - hr * 0.45, ky + hr * 2.0), (kx, ky + hr * 1.3), (kx + hr * 0.45, ky + hr * 2.0)],
                1.6, rng)
        _crayon(q, (40, 40, 40), [(kx, ky + hr * 0.7), (cx + (hr if kx > cx else -hr), by + hr * 0.5)], 1.6, rng)
        if rng.random() < 0.6:
            words = rng.choice(["I LOVE FREDDY", "MY FRIEND", "FUN!", "BEST DAY", "PIZZA!"])
            q.text(words, w / 2, h - 16, 7.5, rng.choice([(200, 40, 40), (40, 60, 200), (40, 140, 40)]), bold=False)

    surf, _ = render_ss(w * _SS, h * _SS, draw, ss=2, alpha=True)
    return surf


_DRAWINGS = [
    # (centre x, centre y, w, h, angle, subject)
    (1268, 176, 76, 96, -6, "Freddy"),
    (1360, 166, 86, 66, 4, "Chica"),
    (1424, 220, 62, 82, 8, "Foxy"),
    (1296, 292, 90, 70, 3, "Bonnie"),
    (1394, 312, 74, 92, -5, "Freddy"),
    (762, 186, 72, 56, -4, "Bonnie"),
]


def _draw_drawings(p, big):
    for i, (x, y, w, h, ang, subj) in enumerate(_DRAWINGS):
        surf = _drawing_surface(w, h, subj, 100 + i)
        rot = pygame.transform.rotozoom(surf, ang, 1.0)
        shadow = _silhouette(rot, 110)
        big.blit(shadow, (x * _SS - rot.get_width() // 2 + 6, y * _SS - rot.get_height() // 2 + 8))
        big.blit(rot, (x * _SS - rot.get_width() // 2, y * _SS - rot.get_height() // 2))
        a = math.radians(-ang)
        for dx in (-w / 2 + 4, w / 2 - 4):
            tx = x + dx * math.cos(a) - (-h / 2) * math.sin(a)
            ty = y + dx * math.sin(a) + (-h / 2) * math.cos(a)
            _tape(p, tx, ty, ang * -1 + (30 if dx < 0 else -30))


# ---------------------------------------------------------------------------
# Desk and clutter
# ---------------------------------------------------------------------------

def _desk_pt(X, Z, Y=DESK_TOP_WY):
    return _proj(X, Y, Z)


def _draw_desk(p, rng):
    far_l, far_r = _desk_pt(-DESK_HALF_W, DESK_FAR_Z), _desk_pt(DESK_HALF_W, DESK_FAR_Z)
    near_l, near_r = _desk_pt(-DESK_HALF_W, DESK_NEAR_Z), _desk_pt(DESK_HALF_W, DESK_NEAR_Z)
    # Shadow on the wall/floor behind the desk.
    p.poly((20, 22, 24), [(far_l[0] - 10, far_l[1] - 4), (far_r[0] + 10, far_r[1] - 4),
                          (near_r[0] + 10, H), (near_l[0] - 10, H)])
    top = [far_l, far_r, near_r, near_l]
    p.poly((86, 80, 72), top)
    # Laminate grain converging to the vanishing point.
    for i in range(1, 26):
        u = i / 26.0
        a = (lerp(far_l[0], far_r[0], u), far_l[1])
        b = (lerp(near_l[0], near_r[0], u), near_l[1])
        p.line(shade((86, 80, 72), rng.uniform(0.85, 0.97)), a, b, 1)
    # Front edge (the side facing the player) with an apron, pedestals and a knee hole.
    ny = near_l[1]
    xl, xr = near_l[0], near_r[0]
    _vgrad(p, xl, ny, xr - xl, 10, (128, 120, 108), (82, 76, 68))
    apron_b = ny + 42
    _vgrad(p, xl, ny + 10, xr - xl, apron_b - ny - 10, (64, 60, 56), (48, 45, 43))
    p.rect((30, 28, 27), xl, apron_b - 2, xr - xl, 2)
    kx0, kx1 = VPX - 160, VPX + 160
    # Knee hole: back panel, side faces and floor seen in perspective.
    def far(x, y):
        return (VPX + (x - VPX) * DESK_NEAR_Z / DESK_FAR_Z, VPY + (y - VPY) * DESK_NEAR_Z / DESK_FAR_Z)
    floor_far = VPY + FOCAL * FLOOR_WY / DESK_FAR_Z
    bx0, bx1 = far(kx0, 0)[0], far(kx1, 0)[0]
    p.rect((8, 8, 9), kx0, apron_b, kx1 - kx0, H - apron_b)
    _vgrad(p, bx0, apron_b, bx1 - bx0, floor_far - apron_b, (30, 28, 27), (20, 19, 19))
    p.poly((13, 13, 14), [(bx0, floor_far), (bx1, floor_far), (kx1 + 30, H), (kx0 - 30, H)])
    for x_near, x_far in ((kx0, bx0), (kx1, bx1)):
        p.poly((22, 21, 21), [(x_near, apron_b), (x_far, apron_b), (x_far, floor_far), (x_near, H)])
    # Pedestals with drawers.
    for x0, x1 in ((xl, kx0), (kx1, xr)):
        _vgrad(p, x0, apron_b, x1 - x0, H - apron_b, (58, 54, 51), (34, 32, 31))
        p.rect((84, 80, 74), x0, apron_b, 2, H - apron_b)
        p.rect((22, 21, 20), x1 - 2, apron_b, 2, H - apron_b)
        for dy0, dy1 in ((apron_b + 8, apron_b + 78), (apron_b + 84, H + 10)):
            p.rect((24, 23, 22), x0 + 10, dy0, x1 - x0 - 20, dy1 - dy0, radius=3)
            _vgrad(p, x0 + 12, dy0 + 2, x1 - x0 - 24, dy1 - dy0 - 4, (64, 60, 56), (44, 41, 39))
            hx = (x0 + x1) / 2.0
            hy = dy0 + 22
            p.rect((22, 22, 22), hx - 26, hy - 3, 52, 9, radius=3)
            p.rect((118, 116, 110), hx - 25, hy - 4, 50, 7, radius=3)
            p.rect((170, 168, 160), hx - 22, hy - 3, 44, 2)
    # Scuffs and an old sticker.
    for _ in range(18):
        x = rng.uniform(xl + 10, xr - 10)
        if kx0 - 10 < x < kx1 + 10:
            continue
        y = rng.uniform(ny + 14, H - 10)
        p.line((76, 72, 66), (x, y), (x + rng.uniform(-16, 16), y + rng.uniform(-4, 4)), 1)
    sx, sy = xr - 120, ny + 16
    p.rect((180, 40, 40), sx, sy, 64, 18, radius=3)
    p.rect((236, 220, 120), sx + 2, sy + 2, 60, 2)
    p.text("FAZBEAR", sx + 32, sy + 10, 11, (250, 236, 200))


def _box_faces(p, x, y, w, h, f, col):
    """Side faces of a box whose front face is (x, y, w, h); back = f toward the VP."""
    def back(px, py):
        return (VPX + (px - VPX) * f, VPY + (py - VPY) * f)
    tl, tr, br, bl = (x, y), (x + w, y), (x + w, y + h), (x, y + h)
    p.poly(shade(col, 0.55), [tr, br, back(*br), back(*tr)])
    p.poly(shade(col, 0.55), [tl, bl, back(*bl), back(*tl)])
    p.poly(shade(col, 1.12), [tl, tr, back(*tr), back(*tl)])


def _draw_crt(p, x, y, w, h, rng, case=(150, 144, 128)):
    _box_faces(p, x + w * 0.12, y + h * 0.1, w * 0.76, h * 0.8, 0.8, shade(case, 0.8))   # tube housing
    _box_faces(p, x, y, w, h, 0.93, case)
    p.rect(shade(case, 0.6), x, y, w, h, radius=6)
    _vgrad(p, x + 1.5, y + 1.5, w - 3, h - 3, shade(case, 1.08), shade(case, 0.82))
    sx, sy, sw, sh = x + w * 0.1, y + h * 0.1, w * 0.8, h * 0.66
    p.rect(shade(case, 0.45), sx - 4, sy - 4, sw + 8, sh + 8, radius=8)
    p.rect((14, 20, 18), sx, sy, sw, sh, radius=10)
    _vgrad(p, sx + 4, sy + 4, sw - 8, sh - 8, (34, 48, 44), (18, 26, 24))
    for yy in range(int(sy + 6), int(sy + sh - 6), 3):
        p.line((26, 38, 34), (sx + 6, yy), (sx + sw - 6, yy), 1)
    p.ellipse((70, 90, 86), sx + sw * 0.3, sy + sh * 0.25, sw * 0.18, sh * 0.08)
    # Knobs / power LED.
    by = y + h * 0.88
    for i in range(3):
        p.circle(shade(case, 0.4), x + w * 0.62 + i * w * 0.09, by, max(1.5, w * 0.025))
    p.circle((30, 120, 40), x + w * 0.15, by, max(1.5, w * 0.018))
    return (sx + sw / 2, sy + sh / 2, max(sw, sh) * 0.75)


def _draw_monitors(p, rng):
    glows = []
    glows.append(_draw_crt(p, 708, 336, 154, 134, rng))
    glows.append(_draw_crt(p, 866, 404, 88, 70, rng, case=(70, 70, 72)))
    glows.append(_draw_crt(p, 726, 242, 120, 94, rng, case=(132, 128, 116)))
    # Cables hanging down the front of the desk.
    near_y = _desk_pt(0, DESK_NEAR_Z)[1]
    for x0, c in ((760, (24, 24, 26)), (800, (40, 34, 30)), (900, (22, 22, 24))):
        p.lines(c, [(x0, 470), (x0 - 30, near_y + 2), (x0 - 46, near_y + 60), (x0 - 50, H)], 3)
    return glows


def _draw_fan_static(p):
    cx, cy = FAN_C
    base_y = 476
    # Base and neck.
    p.ellipse((26, 26, 28), cx, base_y + 4, 46, 11)
    p.ellipse((70, 72, 76), cx, base_y, 42, 10)
    p.ellipse((104, 106, 110), cx, base_y - 3, 38, 8)
    p.rect((60, 62, 66), cx - 7, cy + 30, 14, base_y - cy - 32)
    p.rect((120, 122, 126), cx - 3, cy + 30, 3, base_y - cy - 32)
    # Motor housing and back cage (behind the spinning blades).
    p.circle((24, 24, 26), cx, cy, FAN_R + 2)
    p.circle((50, 52, 56), cx, cy, FAN_R, width=2)
    for i in range(12):
        a = i / 12 * math.tau
        p.line((44, 46, 50), (cx + math.cos(a) * 14, cy + math.sin(a) * 14),
               (cx + math.cos(a) * FAN_R, cy + math.sin(a) * FAN_R), 1.2)
    p.circle((40, 42, 46), cx, cy, 26)
    p.circle((56, 58, 62), cx, cy, 26, width=2)


def _draw_fan_blades(pen, angle, cage=True):
    """Blades + front cage, centred at (0, 0) of ``pen`` (1 unit = 1 px)."""
    R = FAN_R - 5
    blade = (78, 92, 104)

    def blade_poly(a):
        pts_l, pts_t = [], []
        for i in range(9):
            r = lerp(9, R, i / 8.0)
            hw = math.radians(5 + 13 * (i / 8.0) ** 0.8)
            pts_l.append((math.cos(a + hw) * r, math.sin(a + hw) * r))
            pts_t.append((math.cos(a - hw * 0.8) * r, math.sin(a - hw * 0.8) * r))
        tip = []
        hw = math.radians(18)
        for k in range(6):
            aa = a + hw - (hw * 1.8) * k / 5.0
            tip.append((math.cos(aa) * (R + 2), math.sin(aa) * (R + 2)))
        return pts_l + tip + pts_t[::-1]

    for ghost, alpha in ((-0.28, 26), (-0.19, 50), (-0.1, 96), (0.0, 236)):
        for k in range(3):
            a = angle + ghost + k * math.tau / 3
            pen.poly(blade + (alpha,), blade_poly(a))
    for k in range(3):
        a = angle + k * math.tau / 3
        pen.lines((140, 156, 168, 235), blade_poly(a)[:9], 1.6)
    if cage:
        cc = (156, 156, 150, 255)
        for i in range(18):
            a = i / 18 * math.tau
            pen.line(cc, (math.cos(a) * 13, math.sin(a) * 13), (math.cos(a) * FAN_R, math.sin(a) * FAN_R), 1.3)
        pen.circle(cc, 0, 0, FAN_R, width=3)
        pen.circle((190, 190, 184, 255), 0, -0.6, FAN_R - 1, width=1)
        for rr in (24, 38, 50):
            pen.circle(cc, 0, 0, rr, width=1.3)
        pen.circle((120, 120, 116, 255), 0, 0, 13)
        pen.circle((196, 196, 190, 255), 0, 0, 11)
        pen.circle((60, 64, 70, 255), 0, 0, 5)
        pen.ellipse((240, 240, 236, 255), -3.5, -4, 3.5, 2.2)


def _fan_sprite(angle):
    w = FAN_RECT.w

    def draw(pen):
        _draw_fan_blades(pen.sub(w / 2.0, w / 2.0), angle)

    surf, _ = render_ss(w, w, draw, ss=3, alpha=True)
    return surf


def _draw_papers(p, rng):
    sheets = [
        # (X centre, Z centre, half width, half depth, rotation)
        (-0.02, 1.85, 0.14, 0.11, 0.25),
        (0.06, 1.72, 0.13, 0.1, -0.15),
        (0.42, 1.95, 0.12, 0.09, 0.4),
        (-0.3, 1.62, 0.12, 0.1, -0.3),
    ]
    for X, Z, hw, hd, rot in sheets:
        ca, sa = math.cos(rot), math.sin(rot)
        corners = []
        for dx, dz in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)):
            corners.append(_desk_pt(X + dx * ca - dz * sa, Z + dx * sa + dz * ca, DESK_TOP_WY - 0.002))
        paper = rng.choice([(200, 196, 180), (190, 186, 166), (206, 200, 176)])
        p.poly(paper, corners)
        # Lines of "text".
        for i in range(1, 8):
            u = i / 8.0
            a = (lerp(corners[0][0], corners[3][0], u), lerp(corners[0][1], corners[3][1], u))
            b = (lerp(corners[1][0], corners[2][0], u), lerp(corners[1][1], corners[2][1], u))
            m = rng.uniform(0.55, 0.9)
            p.line((130, 128, 120), (lerp(a[0], b[0], 0.1), lerp(a[1], b[1], 0.1)),
                   (lerp(a[0], b[0], m), lerp(a[1], b[1], m)), 1)


def _draw_cup(p):
    cx, by = 1122, 490
    h, wt, wb = 60, 21, 15
    top = by - h
    p.ellipse((24, 22, 22), cx + 6, by + 1, wb + 8, 4)
    p.poly((214, 210, 200), [(cx - wt, top), (cx + wt, top), (cx + wb, by), (cx - wb, by)])
    p.poly((248, 246, 238), [(cx - wt + 5, top), (cx - wt + 12, top), (cx - wb + 8, by), (cx - wb + 3, by)])
    p.poly((190, 30, 36), [(cx - wt * 0.93, top + 18), (cx + wt * 0.93, top + 18), (cx + wt * 0.82, top + 32),
                           (cx - wt * 0.82, top + 32)])
    p.arc((40, 90, 190), cx - 4, top + 44, 10, 5, 200, 340, 2.2)
    p.ellipse((150, 146, 140), cx, top, wt + 1.5, 5)
    p.ellipse((232, 230, 224), cx, top - 2, wt + 1, 5)
    p.ellipse((250, 250, 246), cx - 4, top - 3, wt * 0.5, 2)
    # Straw.
    p.thick((236, 236, 230), (cx + 4, top - 2), (cx + 14, top - 40), 4.5)
    for i in range(4):
        u0, u1 = i / 4.0 + 0.04, i / 4.0 + 0.14
        p.thick((200, 40, 40), (cx + 4 + 10 * u0, top - 2 - 38 * u0), (cx + 4 + 10 * u1, top - 2 - 38 * u1), 4.5)


def _draw_plush(p):
    """Mr. Cupcake, Chica's pink cupcake, sitting on the desk."""
    cx, by = 1210, 488
    p.ellipse((24, 22, 22), cx + 5, by + 1, 30, 5)
    wb, wt, wh = 20, 27, 26
    p.poly((96, 150, 196), [(cx - wt, by - wh), (cx + wt, by - wh), (cx + wb, by), (cx - wb, by)])
    for i in range(-3, 4):
        x0 = cx + i * wt / 3.6
        x1 = cx + i * wb / 3.6
        p.line((60, 100, 150), (x0, by - wh), (x1, by), 2)
    fy = by - wh - 12
    p.ellipse((200, 90, 130), cx, fy + 4, 33, 18)
    p.ellipse((246, 132, 176), cx, fy, 31, 19)
    p.ellipse((255, 186, 214), cx - 10, fy - 9, 11, 5)
    # Big eyes and a toothy grin.
    for ex in (-11, 11):
        p.circle((250, 250, 248), cx + ex, fy - 1, 8)
        p.circle((20, 20, 24), cx + ex + 1, fy, 4)
        p.circle((255, 255, 255), cx + ex - 1, fy - 3, 1.5)
    p.ellipse((90, 20, 40), cx, fy + 11, 14, 5)
    for i in range(6):
        x = cx - 12 + i * 4.8
        p.poly((250, 250, 244), [(x, fy + 7), (x + 4.4, fy + 7), (x + 2.2, fy + 12)])
    # Candle.
    p.rect((240, 240, 236), cx - 3, fy - 38, 6, 20)
    p.rect((90, 150, 220), cx - 3, fy - 32, 6, 3)
    p.rect((90, 150, 220), cx - 3, fy - 24, 6, 3)
    p.ellipse((250, 180, 40), cx, fy - 43, 4.5, 7)
    p.ellipse((255, 236, 140), cx, fy - 41, 2.2, 4)


def _draw_phone(p):
    cx, by = 1290, 482
    p.ellipse((22, 20, 20), cx + 4, by + 2, 34, 6)
    p.poly((34, 34, 36), [(cx - 26, by - 26), (cx + 26, by - 26), (cx + 32, by), (cx - 32, by)])
    p.poly((54, 54, 58), [(cx - 26, by - 26), (cx + 26, by - 26), (cx + 22, by - 30), (cx - 22, by - 30)])
    p.circle((70, 70, 74), cx, by - 11, 9)
    p.circle((26, 26, 28), cx, by - 11, 4)
    for i in range(10):
        a = math.radians(-60 + i * 30)
        p.circle((140, 140, 140), cx + math.cos(a) * 6.5, by - 11 + math.sin(a) * 6.5, 1)
    # Handset.
    p.rect((26, 26, 28), cx - 34, by - 42, 68, 12, radius=6)
    p.rect((60, 60, 64), cx - 30, by - 42, 60, 3, radius=2)
    p.rect((26, 26, 28), cx - 38, by - 38, 14, 10, radius=4)
    p.rect((26, 26, 28), cx + 24, by - 38, 14, 10, radius=4)
    # Curly cord.
    pts = [(cx - 32 - i * 2.2, by - 30 + i * 1.6 + math.sin(i * 1.3) * 3) for i in range(14)]
    p.lines((20, 20, 22), pts, 2)


def _draw_lamp_and_wires(p, rng):
    # Hanging wires (catenaries) and loose dangling ends.
    wires = [
        (300, 18, 560, 6, 34, (20, 20, 22), 3),
        (470, 4, 780, 16, 48, (56, 18, 16), 2.4),
        (620, 22, 900, 4, 40, (22, 22, 24), 3.4),
        (840, 8, 1100, 12, 62, (26, 26, 30), 3),
        (930, 4, 1190, 20, 30, (40, 40, 80), 2),
        (1090, 10, 1380, 6, 54, (20, 20, 22), 3.2),
        (1300, 20, 1560, 8, 36, (60, 50, 20), 2.4),
        (1500, 6, 1760, 18, 44, (22, 22, 24), 3),
        (60, 8, 250, 24, 30, (24, 24, 26), 2.6),
        (1680, 14, 1900, 6, 28, (24, 24, 26), 2.6),
    ]
    for xa, ya, xb, yb, sag, col, wd in wires:
        p.lines(col, _catenary(xa, ya, xb, yb, sag), wd)
        p.lines(shade(col, 1.8), [(x, y - wd * 0.3) for x, y in _catenary(xa, ya, xb, yb, sag)], 1)
    dangles = [(560, 6, 150, 14), (1146, 10, 120, -10), (1420, 4, 175, 18), (690, 14, 96, -8), (1630, 8, 130, 12),
               (360, 4, 92, -6), (1250, 6, 70, 6)]
    for x, y0, ln, curl in dangles:
        pts = [(x + curl * math.sin(u * 2.2) * u, y0 + ln * u) for u in (i / 16.0 for i in range(17))]
        col = rng.choice([(20, 20, 22), (60, 18, 16), (24, 24, 40)])
        p.lines(col, pts, 2.6)
        ex, ey = pts[-1]
        for dx in (-3, 0, 3):
            p.line((170, 100, 50), (ex, ey), (ex + dx, ey + 6), 1)
    # The office lamp hanging over the desk.
    cx = VPX
    p.line((18, 18, 20), (cx, 0), (cx, 26), 3)
    p.poly((36, 52, 42), [(cx - 13, 24), (cx + 13, 24), (cx + 48, 54), (cx - 48, 54)])
    p.poly((60, 84, 66), [(cx - 9, 24), (cx - 1, 24), (cx - 18, 54), (cx - 36, 54)])
    p.rect((28, 30, 30), cx - 15, 18, 30, 8, radius=3)
    p.ellipse((26, 34, 28), cx, 54, 48, 6)
    p.ellipse((255, 238, 196), cx, 55, 40, 4.5)
    p.ellipse((255, 252, 236), cx, 55, 20, 3)


# ---------------------------------------------------------------------------
# The hallway outside a doorway
# ---------------------------------------------------------------------------

def _draw_hall(p, rng):
    """Hallway seen through the left doorway (office coords)."""
    r = DOORWAYS["L"]
    zf = _HALL_Z_FAR
    yc = VPY + FOCAL * _HALL_CEIL_WY / zf
    yf = VPY + FOCAL * FLOOR_WY / zf
    k = ZWALL / zf
    # Ceiling.
    p.rect((26, 26, 28), r.x, r.y, r.w, yc - r.y + 1)
    for X in (-3.6, -3.0, -2.4, -1.8):
        p.line((14, 14, 16), _proj(X, _HALL_CEIL_WY, 2.5), _proj(X, _HALL_CEIL_WY, zf), 2)
    # Far wall, styled like the office walls (scaled by distance).
    band_top = VPY + (BAND_TOP - VPY) * k
    band_sq = BAND_SQ * k
    _vgrad(p, r.x, yc, r.w, band_top - 3 - yc, shade(WALL_A, 0.9), WALL_B)
    p.rect((34, 36, 40), r.x, band_top - 3, r.w, 3)
    for row in range(3):
        for col in range(int(r.w / band_sq) + 2):
            c = TILE_W if (row + col) % 2 else TILE_K
            p.rect(c, r.x + col * band_sq, band_top + row * band_sq, band_sq + 0.5, band_sq + 0.5)
    lt = band_top + 3 * band_sq
    p.rect((34, 36, 40), r.x, lt, r.w, 3)
    base_top = VPY + (BASE_TOP - VPY) * k
    _vgrad(p, r.x, lt + 3, r.w, base_top - lt - 3, LOW_A, LOW_B)
    for x in range(r.x, r.right, 26):
        p.rect(shade(LOW_B, 0.75), x, lt + 3, 1.5, base_top - lt - 3)
    p.rect((26, 26, 28), r.x, base_top, r.w, yf - base_top)
    # Streaks.
    for _ in range(14):
        x = rng.uniform(r.x, r.right)
        y0 = yc + rng.uniform(0, 30)
        y1 = min(band_top - 3, y0 + rng.uniform(30, 140))
        _vgrad(p, x, y0, rng.uniform(1, 5), y1 - y0, shade(_upper_col(y0 + 80), 0.7), _upper_col(y1 + 80))
    # A faded flyer on the far wall.
    fx, fy = r.x + 92, yc + 34
    p.rect((30, 30, 32), fx + 2, fy + 3, 58, 76)
    p.rect((196, 192, 172), fx, fy, 58, 76)
    p.rect((170, 40, 40), fx + 5, fy + 6, 48, 9)
    for i in range(6):
        p.rect((120, 118, 110), fx + 6, fy + 22 + i * 8, rng.uniform(26, 46), 2)
    # Floor.
    p.rect((16, 16, 18), r.x, yf, r.w, r.bottom - yf)
    _draw_floor_tiles(p, r.x - 200, r.right + 200, zf, ZWALL - 0.01, rng=rng)


def _quad_strips(p, quad, c_near, c_far, n=8):
    """Fill quad (near edge = quad[0]->quad[3], far edge = quad[1]->quad[2]) with a gradient."""
    a0, b0, b1, a1 = quad
    for i in range(n):
        u0, u1 = i / float(n), (i + 1) / float(n)
        pts = [(lerp(a0[0], b0[0], u0), lerp(a0[1], b0[1], u0)), (lerp(a0[0], b0[0], u1), lerp(a0[1], b0[1], u1)),
               (lerp(a1[0], b1[0], u1), lerp(a1[1], b1[1], u1)), (lerp(a1[0], b1[0], u0), lerp(a1[1], b1[1], u0))]
        p.poly(lerp_color(c_near, c_far, (i + 0.5) / n), pts)


def _draw_jambs(p):
    left, header, sill = _jamb_polys()
    # left = [near top, far top, far bottom, near bottom]
    _quad_strips(p, [left[0], left[1], left[2], left[3]], (74, 78, 84), (36, 38, 42))
    p.poly((22, 22, 25), header)
    p.poly((84, 86, 88), sill)
    # Shutter guide groove on the jamb near the front edge.
    a, b = left[0], left[3]
    p.line((14, 14, 16), (a[0] + 6, a[1] + 1), (b[0] + 6, b[1] - 2), 3)
    p.line((104, 108, 114), (a[0] + 9.5, a[1] + 2), (b[0] + 9.5, b[1] - 3), 1)
    for y in range(int(a[1]) + 40, int(b[1]) - 20, 70):
        _bolt(p, a[0] + 22, y, 2.2, (96, 100, 106))
    # Diamond plate on the sill.
    for i in range(18):
        u = (i + 0.5) / 18
        x = lerp(sill[0][0], sill[1][0], u)
        p.line((120, 122, 124), (x - 3, sill[0][1] - 5), (x + 3, sill[0][1] - 10), 1.2)
    # Edge highlight where the jamb meets the office wall.
    p.line((120, 124, 130), left[0], left[3], 2)


def _hall_light_mask(w, h):
    """Harsh light from a fixture just outside, above the doorway."""
    return make_light_mask(w, h, (5, 5, 7), [
        (w * 0.56, -50, w * 1.05, h * 1.02, (255, 252, 240)),
        (w * 0.55, h * 0.06, w * 0.6, h * 0.5, (120, 118, 110)),
        (w * 0.52, h * 0.95, w * 0.75, h * 0.3, (120, 116, 104)),
    ])


# ---------------------------------------------------------------------------
# Shutter
# ---------------------------------------------------------------------------

def _draw_shutter(p, w, h, rng):
    """Heavy corrugated steel door (local coords 0..w, 0..h)."""
    p.rect((40, 42, 46), 0, 0, w, h)
    slat = 30
    bottom_band = 50
    y = 0
    while y < h - bottom_band:
        _vgrad(p, 0, y, w, 3, (150, 154, 160), (124, 128, 134))       # lit lip
        _vgrad(p, 0, y + 3, w, 17, (112, 116, 122), (82, 86, 92))     # slat face
        _vgrad(p, 0, y + 20, w, 4, (70, 72, 78), (50, 52, 56))
        _vgrad(p, 0, y + 24, w, 6, (26, 27, 30), (16, 16, 18))        # deep groove
        y += slat
    # Side rails.
    for x0 in (0, w - 12):
        _hgrad(p, x0, 0, 12, h, (44, 46, 50), (84, 88, 94))
        p.rect((18, 18, 20), x0 + (11 if x0 == 0 else 0), 0, 1.5, h)
    # Rivets on each slat.
    y = 11
    while y < h - bottom_band:
        for x in (22, w / 2.0, w - 22):
            _bolt(p, x, y, 2.6, (120, 124, 130))
        y += slat
    # Scratches, dents and rust runs.
    for _ in range(46):
        x = rng.uniform(14, w - 14)
        yy = rng.uniform(4, h - bottom_band)
        p.line((62, 64, 68), (x, yy), (x + rng.uniform(-24, 24), yy + rng.uniform(-3, 3)), 1)
    for _ in range(9):
        x = rng.uniform(14, w - 14)
        yy = rng.uniform(4, h - bottom_band - 20)
        p.ellipse((104, 74, 52), x, yy, rng.uniform(4, 10), rng.uniform(2, 4))
        p.ellipse((120, 88, 60), x - 1, yy - 0.5, rng.uniform(2, 5), rng.uniform(1, 2))
    # Hazard band and rubber lip at the bottom edge.
    hb = h - bottom_band
    p.rect((20, 20, 22), 0, hb, w, 4)
    _hazard(p, 0, hb + 4, w, bottom_band - 16, stripe=20, yellow=(214, 168, 28))
    p.rect((120, 96, 30), 0, hb + 4, w, 1.5)
    for _ in range(10):
        x = rng.uniform(0, w)
        p.line((40, 36, 30), (x, hb + 6), (x + rng.uniform(-10, 10), h - 14), rng.uniform(1, 3))
    p.rect((44, 44, 46), 0, h - 12, w, 2)
    p.rect((12, 12, 13), 0, h - 10, w, 10)


def _sprite_blit(surface, spr, dest, pos, glow_list=None):
    """Blit ``surface`` using sprite ``spr``'s anchor and glows."""
    x = int(round(pos[0] - spr.anchor[0]))
    y = int(round(pos[1] - spr.anchor[1]))
    dest.blit(surface, (x, y))
    if glow_list is not None:
        for gx, gy, gr, gc in spr.glows:
            glow_list.append((gx + x, gy + y, gr, gc))


# ---------------------------------------------------------------------------
# The Office
# ---------------------------------------------------------------------------

class Office:
    """Pre-rendered office panorama. See module docstring."""

    def __init__(self):
        self._built = False
        self.base = None
        self.dark_base = None
        self.fan_frames = []
        self.interiors = {}      # (side, who or None) -> surface (lit doorway)
        self.int_dark = {}
        self.shutters = {}
        self.dark_shutters = {}
        self.caps = {}           # (side, kind, lit) -> (surface, pos)
        self.cap_glows = {}
        self.spill = {}          # side -> [ [(surf, pos), ...] per level ]
        self.show = {}           # freddy_lit -> surface
        self.golden = None       # (lit sprite, dark sprite, pos)
        self._hall_raw = {}
        self._jambs_raw = {}
        self._hall_masks = {}
        self._office_mask = None

    # -- building ---------------------------------------------------------------
    def build(self):
        """Generator: pre-render everything, yielding after each chunk of work
        (~5-50 ms each) so a loading screen can update. Safe to abandon; draw()
        finishes the job lazily if needed."""
        if self._built:
            return
        for _ in self._build_steps():
            yield
            if self._built:      # finished elsewhere (e.g. a lazy draw())
                return

    def _build_steps(self):
        rng = random.Random(1987)
        big = pygame.Surface((W * _SS, H * _SS))
        big.fill((0, 0, 0))
        p = Pen(big, 0, 0, _SS)
        m = Pen(big, W * _SS, 0, _SS, flip=True)   # mirrors office x -> W - x
        _draw_ceiling(p, rng)
        yield
        _draw_walls(p, rng)
        yield
        _draw_walls_lower(p, rng)
        yield
        _draw_floor(p, rng)
        _draw_vent(p)
        yield
        _mult(big, _shadow_mask())
        _draw_pipes(p, rng)
        yield
        _draw_poster(p, big, rng)
        yield
        _draw_drawings(p, big)
        yield
        for pen in (p, m):
            _draw_doorframe(pen, rng)
            _draw_panel(pen)
        yield
        _draw_outlets(p)
        _draw_desk(p, rng)
        _draw_papers(p, rng)
        screen_glows = _draw_monitors(p, rng)
        yield
        _draw_fan_static(p)
        _draw_cup(p)
        _draw_plush(p)
        _draw_phone(p)
        _draw_lamp_and_wires(p, rng)
        yield
        raw = pygame.transform.smoothscale(big, (W, H))
        del big, p, m
        yield
        # Grime: large stains, medium blotches and fine grain.
        _mult(raw, _stains(W, H, 46, 150, 255, rng))
        _mult(raw, _stains(W, H, 9, 196, 255, rng))
        yield
        _mult(raw, _noise(W, H, 214, 255, rng))
        yield

        # Lighting.
        mask = make_light_mask(W, H, (22, 24, 30), [
            (VPX, 300, 1120, 680, (196, 186, 166)),
            (VPX, 470, 600, 260, (52, 46, 36)),
            (VPX, 40, 260, 140, (110, 96, 70)),
            (_PANEL.centerx, _PANEL.centery, 130, 210, (66, 68, 78)),
            (W - _PANEL.centerx, _PANEL.centery, 130, 210, (66, 68, 78)),
        ])
        self._office_mask = mask
        yield
        base = raw.copy()
        _mult(base, mask)
        add_glows(base, [(VPX, 58, 90, (120, 96, 60)), (VPX, 56, 34, (255, 226, 170))])
        add_glows(base, [(x, y, r, (14, 30, 26)) for x, y, r in screen_glows])
        yield
        # Fan frames: lit background crop + lit blades.
        fan_bg = base.subsurface(FAN_RECT).copy()
        fan_mask = _boost(mask.subsurface(FAN_RECT).copy(), 1.1)
        frames = []
        raw_frame0 = None
        for i in range(FAN_FRAMES):
            spr = _fan_sprite(i * (math.tau / 3) / FAN_FRAMES)
            if i == 0:
                raw_frame0 = spr
            f = fan_bg.copy()
            f.blit(_lit_sprite(spr, fan_mask), (0, 0))
            frames.append(_cv(f))
            if i % 3 == 2:
                yield
        self.fan_frames = frames
        base.blit(frames[0], FAN_RECT.topleft)
        # Button caps: unlit ones are part of the image; lit ones are overlays.
        for side in "LR":
            for kind in ("door", "light"):
                for lit in (False, True):
                    self.caps[(side, kind, lit)] = self._make_cap(side, kind, lit)
                bx, by = BUTTONS[(side, kind)].center
                col = (230, 34, 22) if kind == "door" else (235, 235, 222)
                self.cap_glows[(side, kind)] = [(bx, by, 150, shade(col, 0.5)), (bx, by, 70, shade(col, 0.75)),
                                                (bx, by, 38, col)]
                surf, pos = self.caps[(side, kind, False)]
                base.blit(surf, pos)
        yield
        # Power-out version: nearly black, faint shapes.
        dark_mask = make_light_mask(W, H, (6, 7, 10), [(VPX, 330, 1000, 560, (11, 11, 15))])
        raw.blit(raw_frame0, FAN_RECT.topleft)
        dark = raw.copy()
        _mult(dark, dark_mask)
        for side in "LR":
            for kind in ("door", "light"):
                surf, pos = self.caps[(side, kind, False)]
                dark.blit(_lit_sprite(surf, _const(surf.get_size(), (30, 30, 34))), pos)
        self.dark_base = _cv(dark)
        yield
        # Light spilling out of a lit doorway onto the frame and floor.
        for side in "LR":
            levels = []
            for k in _SPILL_LEVELS:
                parts = []
                for rr in _SPILL_RECTS_L:
                    rect = _side_rect(rr, side)
                    dx = DOORWAYS[side].centerx
                    fx = dx - (34 if side == "L" else -34)
                    lights = [(fx - rect.x, FLOOR_Y + 14 - rect.y, 260, 95, shade((255, 250, 232), k)),
                              (dx - rect.x, 300 - rect.y, 200, 360, shade((150, 148, 138), k))]
                    smask = make_light_mask(rect.w, rect.h, (0, 0, 0), lights)
                    add = raw.subsurface(rect).copy()
                    _mult(add, smask)
                    img = base.subsurface(rect).copy()
                    img.blit(add, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
                    parts.append((_cv(img), rect.topleft))
                levels.append(parts)
            self.spill[side] = levels
            yield
        self.base = _cv(base)
        del raw
        yield
        # Doorway interiors, shutters, the Freddy show, Golden Freddy.
        for side in "LR":
            self._prepare_hall(side)
            yield
            self.interiors[(side, None)] = self._make_interior(side, None)
            self.int_dark[side] = self._make_dark_interior(side)
            self._make_shutter(side)
            yield
        self.interiors[("L", "Bonnie")] = self._make_interior("L", "Bonnie")
        yield
        self.interiors[("R", "Chica")] = self._make_interior("R", "Chica")
        yield
        self._make_show()
        yield
        self._make_golden()
        self._built = True
        yield

    def _ensure(self):
        if not self._built:
            for _ in self._build_steps():
                pass

    # -- pieces -------------------------------------------------------------------
    def _make_cap(self, side, kind, lit):
        size = _BTN_RAD * 2 + 6

        def draw(pen):
            _draw_cap(pen.sub(size / 2.0, size / 2.0), kind, lit)

        surf, _ = render_ss(size, size, draw, ss=3, alpha=True)
        bx, by = BUTTONS[(side, kind)].center
        return _cv(surf, True), (bx - size // 2, by - size // 2)

    def _local_pen(self, pen, side):
        """Pen drawing in left-door office coords onto a doorway-local surface."""
        r = DOORWAYS["L"]
        if side == "L":
            return pen.sub(-r.x, -r.y)
        # Right side: office x -> W - x, then shift by the right doorway's x.
        return pen.sub(r.right, -r.y, 1.0, flip=True)

    def _prepare_hall(self, side):
        r = DOORWAYS[side]
        rng = random.Random(77 if side == "L" else 78)
        self._hall_raw[side], _ = render_ss(r.w, r.h, lambda pen: _draw_hall(self._local_pen(pen, side), rng), ss=2)
        self._jambs_raw[side], _ = render_ss(r.w, r.h, lambda pen: _draw_jambs(self._local_pen(pen, side)), ss=2,
                                             alpha=True)
        mask = _hall_light_mask(r.w, r.h)
        if side == "R":
            mask = pygame.transform.flip(mask, True, False)
        self._hall_masks[side] = mask

    def _char_sprite(self, name, eyes="normal"):
        return characters.render_character(name, DOOR_CHAR_SCALE, eyes=eyes, look=(0.0, 0.1), prop=False)

    def _toplit(self, spr):
        """Copy of the sprite surface lit from above (darker toward the feet)."""
        surf = spr.surface.copy()
        w, h = surf.get_size()
        grad = pygame.Surface((w, h))
        ay = spr.anchor[1]
        for yy in range(h):
            u = (yy - ay) / (DOOR_CHAR_SCALE * 5.0)
            v = int(255 * clamp(1.0 - 0.62 * u, 0.32, 1.0))
            pygame.draw.line(grad, (v, v, v), (0, yy), (w, yy))
        surf.blit(grad, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        return surf

    def _make_interior(self, side, who):
        """Lit doorway, optionally with an animatronic standing right outside."""
        if side not in self._hall_raw:
            self._prepare_hall(side)
        r = DOORWAYS[side]
        surf = self._hall_raw[side].copy()
        glows = []
        if who:
            spr = self._char_sprite(who)
            hx, hy = DOOR_CHAR_HEAD[side]
            # Soft shadow cast on the far wall / floor behind the figure.
            sil = _silhouette(spr.surface, 120)
            sil = pygame.transform.smoothscale(sil, (int(sil.get_width() * 1.08), int(sil.get_height() * 1.08)))
            surf.blit(sil, (hx - r.x - spr.anchor[0] * 1.08 + (14 if side == "L" else -14),
                            hy - r.y - spr.anchor[1] * 1.08 + 46))
            _sprite_blit(self._toplit(spr), spr, surf, (hx - r.x, hy - r.y), glows)
        surf.blit(self._jambs_raw[side], (0, 0))
        _mult(surf, self._hall_masks[side])
        if glows:
            add_glows(surf, glows, 0.8)
        return _cv(surf)

    def _make_dark_interior(self, side):
        r = DOORWAYS[side]
        surf = _const((r.w, r.h), (2, 2, 3))
        jam = self._jambs_raw[side].copy()
        lvl = self._office_mask.subsurface(r).copy() if self._office_mask else _const((r.w, r.h), (50, 50, 56))
        jam.blit(lvl, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        jam.blit(_const((r.w, r.h), (150, 150, 160)), (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        surf.blit(jam, (0, 0))
        return _cv(surf)

    def _make_shutter(self, side):
        r = DOORWAYS[side]
        rng = random.Random(5)
        surf, _ = render_ss(r.w, r.h, lambda pen: _draw_shutter(pen, r.w, r.h, rng), ss=2)
        if side == "R":
            surf = pygame.transform.flip(surf, True, False)
        _mult(surf, _stains(r.w, r.h, 18, 140, 255, rng))
        _mult(surf, _noise(r.w, r.h, 200, 255, rng))
        light = self._office_mask.subsurface(r).copy() if self._office_mask else _const((r.w, r.h), (60, 60, 64))
        light = _boost(light, 1.9)
        # Shadow under the housing at the top of the doorway.
        top_shadow = pygame.Surface((r.w, r.h))
        for yy in range(r.h):
            v = int(255 * clamp(0.55 + yy / 140.0, 0.0, 1.0))
            pygame.draw.line(top_shadow, (v, v, v), (0, yy), (r.w, yy))
        _mult(light, top_shadow)
        lit = surf.copy()
        _mult(lit, light)
        dark = surf.copy()
        _mult(dark, _const((r.w, r.h), (14, 15, 19)))
        self.shutters[side] = _cv(lit)
        self.dark_shutters[side] = _cv(dark)

    def _make_show(self):
        """Power-out: Freddy in the left doorway, face faintly lit / unlit."""
        r = DOORWAYS["L"]
        spr = self._char_sprite("Freddy", eyes="glow")
        hx, hy = DOOR_CHAR_HEAD["L"]
        ax, ay = hx - r.x, hy - r.y
        for lit in (False, True):
            surf = _const((r.w, r.h), (0, 0, 0))
            glows = []
            body = spr.surface.copy()
            if lit:
                bw, bh = body.get_size()
                ox, oy = spr.anchor
                fmask = make_light_mask(bw, bh, (3, 3, 4), [
                    (ox, oy + DOOR_CHAR_SCALE * 0.15, DOOR_CHAR_SCALE * 1.5, DOOR_CHAR_SCALE * 1.7, (92, 84, 78)),
                ])
                body.blit(fmask, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
            else:
                body.blit(_const(body.get_size(), (5, 5, 7)), (0, 0), special_flags=pygame.BLEND_RGB_MULT)
            _sprite_blit(body, spr, surf, (ax, ay), glows if lit else None)
            if lit:
                if not glows:
                    s = DOOR_CHAR_SCALE
                    glows = [(ax + ex * s, ay - 0.2 * s, 0.3 * s, (200, 200, 230)) for ex in (-0.4, 0.4)]
                add_glows(surf, glows, 1.0)
            self.show[lit] = _cv(surf)

    def _make_golden(self):
        spr = characters.render_character("Golden", GOLDEN_SCALE, eyes="none", pose="slump")
        x = int(round(GOLDEN_HEAD[0] - spr.anchor[0]))
        y = int(round(GOLDEN_HEAD[1] - spr.anchor[1]))
        sw, sh = spr.surface.get_size()
        mask = pygame.Surface((sw, sh))
        mask.fill((40, 40, 44))
        if self._office_mask is not None:
            mask.blit(self._office_mask, (-x, -y))
        mask = _boost(mask, 0.6)
        # Darker toward the floor, in the desk's shadow.
        grad = pygame.Surface((sw, sh))
        for yy in range(sh):
            v = int(255 * clamp(1.0 - max(0, (y + yy) - 520) / 300.0, 0.35, 1.0))
            pygame.draw.line(grad, (v, v, v), (0, yy), (sw, yy))
        _mult(mask, grad)
        lit = _lit_sprite(spr.surface, mask)
        dark = _lit_sprite(spr.surface, _const((sw, sh), (16, 16, 20)))
        self.golden = (_cv(lit, True), _cv(dark, True), (x, y))

    # -- drawing ------------------------------------------------------------------
    def draw(self, dest, scroll, *, t, door_pos, door_closed, lights, at_door, power_out=None,
             freddy_lit=False, golden=False, flicker=False):
        """Draw office x in [scroll, scroll + 1280) onto ``dest`` at (0, 0).

        t: seconds (fan animation); door_pos: {"L"/"R": 0 open .. 1 shut};
        door_closed / lights: {"L"/"R": bool} (button glows, lit doorway);
        at_door: {"L"/"R": name or None}, shown only in a lit doorway;
        power_out: None | "dark" | "show" | "black"; freddy_lit: Freddy's
        face/eyes during "show"; golden: Golden Freddy slumped by the desk;
        flicker: lit doorways are drawn dark this frame.
        """
        self._ensure()
        sx = int(clamp(int(round(scroll)), 0, W - SCREEN_W))
        if power_out == "black":
            dest.fill((0, 0, 0))
            return
        dark = power_out in ("dark", "show")
        dest.blit(self.dark_base if dark else self.base, (0, 0), pygame.Rect(sx, 0, SCREEN_W, SCREEN_H))
        if not dark:
            fi = int(t * FAN_FPS) % FAN_FRAMES
            dest.blit(self.fan_frames[fi], (FAN_RECT.x - sx, FAN_RECT.y))
        for side in "LR":
            r = DOORWAYS[side]
            pos = clamp(float(door_pos.get(side, 0.0) or 0.0), 0.0, 1.0)
            sh = int(round(pos * r.h))
            lit_door = (not dark) and bool(lights.get(side)) and not flicker
            if lit_door and sh < r.h - 4:
                level = 0 if pos < 0.34 else 1 if pos < 0.67 else 2
                for img, (px, py) in self.spill[side][level]:
                    dest.blit(img, (px - sx, py))
            if sh < r.h:
                img = None
                if dark:
                    if power_out == "show" and side == "L":
                        img = self.show[bool(freddy_lit)]
                elif lit_door:
                    who = at_door.get(side) if at_door else None
                    key = (side, who or None)
                    img = self.interiors.get(key)
                    if img is None:
                        img = self.interiors[key] = self._make_interior(side, who)
                else:
                    img = self.int_dark[side]
                if img is not None:
                    dest.blit(img, (r.x - sx, r.y))
            if sh > 0:
                sh_img = self.dark_shutters[side] if dark else self.shutters[side]
                dest.blit(sh_img, (r.x - sx, r.y), pygame.Rect(0, r.h - sh, r.w, sh))
            if not dark:
                for kind, on in (("door", door_closed.get(side)), ("light", lights.get(side))):
                    if on:
                        surf, (px, py) = self.caps[(side, kind, True)]
                        dest.blit(surf, (px - sx, py))
                        add_glows(dest, self.cap_glows[(side, kind)], 1.0, (-sx, 0))
        if golden and self.golden:
            lit, dk, (gx, gy) = self.golden
            dest.blit(dk if dark else lit, (gx - sx, gy))
