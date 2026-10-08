"""Pre-rendered full-screen images for the interstitial screens.

    newspaper()      the "HELP WANTED" ad shown when starting a new game
    paycheck(kind)   end cards: "week", "overtime", "fired" (pink slip) and
                     "custom" (shift log with a SHIFT COMPLETE stamp)
    game_over()      the guard, stuffed into a Freddy suit, backstage

Each returns an opaque SCREEN_W x SCREEN_H surface (converted to the
display format when a video mode is set). They are meant to be called once
at load time.

Everything is drawn from pygame primitives and system fonts. Greyscale,
tone curves and the newspaper's halftone screen are done on raw pixel bytes
with ``bytes.translate`` and big-integer addition (no numpy), so the whole
module only needs APIs common to pygame 2.1+ and pygame-ce.
"""

import math
import random

import pygame

from . import characters
from .settings import SCREEN_H, SCREEN_W
from .util import Pen, clamp, lerp, lerp_color, make_light_mask, shade

W, H = SCREEN_W, SCREEN_H
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

_tobytes = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
_frombytes = getattr(pygame.image, "frombytes", None) or pygame.image.fromstring


# ===========================================================================
# Small helpers
# ===========================================================================

def _display_ready():
    return pygame.display.get_init() and pygame.display.get_surface() is not None


def _finish(surf):
    """Opaque, display-format copy of ``surf`` (when a display exists)."""
    if _display_ready():
        return surf.convert()
    out = pygame.Surface(surf.get_size())
    out.blit(surf, (0, 0))
    return out


def _alpha_copy(surf):
    out = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    out.fill((0, 0, 0, 0))
    out.blit(surf, (0, 0))
    return out


def _soften(surf, k):
    """Cheap blur: shrink by ``k`` and scale back up."""
    w, h = surf.get_size()
    small = pygame.transform.smoothscale(surf, (max(1, int(w * k)), max(1, int(h * k))))
    return pygame.transform.smoothscale(small, (w, h))


def _value_noise(w, h, cell, lo, hi, rng):
    """Smooth greyscale value noise (lo..hi) of size w x h."""
    cell = max(1, int(cell))
    gw, gh = w // cell + 3, h // cell + 3
    n = gw * gh
    table = bytes(lo + (i * (hi - lo + 1)) // 256 for i in range(256))
    if hasattr(rng, "randbytes"):
        data = rng.randbytes(n).translate(table)
    else:  # Python < 3.9
        data = bytes(rng.getrandbits(8) for _ in range(n)).translate(table)
    rgb = bytearray(3 * n)
    rgb[0::3] = data
    rgb[1::3] = data
    rgb[2::3] = data
    small = _frombytes(bytes(rgb), (gw, gh), "RGB")
    if cell == 1:
        return small.subsurface((1, 1, w, h)).copy()
    big = pygame.transform.smoothscale(small, (gw * cell, gh * cell))
    return big.subsurface((cell, cell, w, h)).copy()


def _multiply_noise(surf, rng, octaves):
    """Multiply value noise into ``surf``; octaves = [(cell, lo), ...]."""
    w, h = surf.get_size()
    for cell, lo in octaves:
        surf.blit(_value_noise(w, h, cell, lo, 255, rng), (0, 0), special_flags=pygame.BLEND_RGB_MULT)


def _light(surf, ambient, lights, q=4):
    """Multiply a low-res radial light mask (see util.make_light_mask) into surf."""
    w, h = surf.get_size()
    m = make_light_mask(w // q, h // q, ambient, [(x / q, y / q, rx / q, ry / q, c) for x, y, rx, ry, c in lights])
    m = pygame.transform.smoothscale(m, (w, h))
    surf.blit(m, (0, 0), special_flags=pygame.BLEND_RGB_MULT)


# ---------------------------------------------------------------------------
# Greyscale and halftone on raw bytes
# ---------------------------------------------------------------------------

# Rec.601 luma weights in 1/256ths; the three products never sum above 255,
# so the per-pixel sum can be done as one big-integer addition (no carries).
_LR = bytes((i * 77) >> 8 for i in range(256))
_LG = bytes((i * 150) >> 8 for i in range(256))
_LB = bytes((i * 29) >> 8 for i in range(256))
_HALF = bytes(i >> 1 for i in range(256))
_INV_HALF = bytes((255 - i) >> 1 for i in range(256))


def _add_bytes(*parts):
    """Element-wise sum of equal-length byte strings (caller guarantees <= 255)."""
    n = len(parts[0])
    total = 0
    for p in parts:
        total += int.from_bytes(p, "big")
    return total.to_bytes(n, "big")


def _luma(surf):
    """Greyscale bytes (one per pixel) of an RGB surface."""
    data = _tobytes(surf, "RGB")
    return _add_bytes(data[0::3].translate(_LR), data[1::3].translate(_LG), data[2::3].translate(_LB))


def _curve(fn):
    return bytes(int(clamp(round(fn(i / 255.0) * 255), 0, 255)) for i in range(256))


def _mix_bytes(a, b, t):
    """lerp(a, b, t) per byte, t in 0..1."""
    ta = bytes(int(i * (1 - t)) for i in range(256))
    tb = bytes(int(i * t) for i in range(256))
    return _add_bytes(a.translate(ta), b.translate(tb))


def _grey_surface(lum, size, dark=(0, 0, 0), light=(255, 255, 255)):
    """Surface from greyscale bytes, mapping 0..255 onto dark..light."""
    rgb = bytearray(3 * len(lum))
    for ch in range(3):
        table = bytes(int(lerp(dark[ch], light[ch], i / 255.0)) for i in range(256))
        rgb[ch::3] = lum.translate(table)
    return _frombytes(bytes(rgb), size, "RGB")


def _halftone(lum, w, h, period=10, soft=2.5):
    """Threshold greyscale bytes against a 45-degree round-dot screen.

    The screen f = cos(2pi(x+y)/P) + cos(2pi(x-y)/P) is periodic in x and y
    with period P, so one P x P tile (repeated with bytes ops) covers the
    image. Dots grow with darkness, as in newspaper printing.
    """
    rows = []
    for y in range(period):
        row = bytearray(period)
        for x in range(period):
            f = math.cos(2 * math.pi * (x + y) / period) + math.cos(2 * math.pi * (x - y) / period)
            row[x] = int(clamp((f + 2) / 4 * 255, 0, 255))
        rows.append((bytes(row) * (w // period + 2))[:w])
    screen = b"".join(rows[y % period] for y in range(h))
    v = _add_bytes(lum.translate(_INV_HALF), screen.translate(_HALF))   # 0..254
    thresh = bytes(int(clamp(255 - (i - 127) * 255 / (2 * soft) - 127.5, 0, 255)) for i in range(256))
    return v.translate(thresh)


# ---------------------------------------------------------------------------
# Fonts and text
# ---------------------------------------------------------------------------

_FACES = {
    "serif": ("timesnewroman,times,liberationserif,nimbusroman,dejavuserif,freeserif,georgia", None),
    "sans": ("arial,helvetica,liberationsans,nimbussans,freesans,dejavusans", None),
    "mono": ("couriernew,courier,liberationmono,nimbusmono,freemono,dejavusansmono", None),
    # handwriting: Windows / macOS fonts first, otherwise an italic serif
    "hand": ("segoeprint,inkfree,bradleyhanditc,noteworthy,markerfelt,bradleyhand,chalkboard,comicsansms",
             "serif"),
}
_face_ok = {}
_font_cache = {}


def _ensure_fonts():
    if not pygame.font.get_init():
        pygame.font.init()


def _font(face, size, bold=False, italic=False):
    size = max(6, int(round(size)))
    key = (face, size, bold, italic)
    f = _font_cache.get(key)
    if f is not None:
        return f
    names, fallback = _FACES[face]
    if face not in _face_ok:
        try:
            _face_ok[face] = pygame.font.match_font(names) is not None
        except Exception:  # broken font registry
            _face_ok[face] = False
    if _face_ok[face]:
        f = pygame.font.SysFont(names, size, bold=bold, italic=italic)
    elif fallback:
        f = _font(fallback, size, bold, True if face == "hand" else italic)
    else:
        f = pygame.font.Font(None, int(size * 1.3))
        f.set_italic(italic)
    _font_cache[key] = f
    return f


def _text(surf, s, pos, face, size, color, bold=False, italic=False, anchor="topleft", squeeze=1.0,
          fit=None, scale=1):
    """Blit a line of text. ``scale`` multiplies size and position (supersampled canvases).

    ``squeeze`` narrows the glyphs; ``fit`` caps the width (in unscaled px).
    """
    f = _font(face, size * scale, bold, italic)
    img = f.render(s, True, color)
    sq = squeeze
    if fit is not None and img.get_width() * sq > fit * scale:
        sq = fit * scale / img.get_width()
    if sq != 1.0:
        img = pygame.transform.smoothscale(img, (max(1, int(img.get_width() * sq)), img.get_height()))
    r = img.get_rect(**{anchor: (int(round(pos[0] * scale)), int(round(pos[1] * scale)))})
    surf.blit(img, r)
    return r


def _para(surf, runs, x, y, w, face, size, color, lead=None, justify=True, indent=0, bottom=None,
          align="left", scale=1):
    """Typeset a paragraph. ``runs`` is a string or [(text, bold, italic), ...].

    Returns (y after the last line, overflowed) - lines that do not fit
    above ``bottom`` are dropped and ``overflowed`` is True.
    """
    if isinstance(runs, str):
        runs = [(runs, False, False)]
    S = scale
    words = []
    for text, b, it in runs:
        f = _font(face, size * S, b, it)
        for word in text.split():
            words.append((word, f, f.size(word)[0]))
    space = _font(face, size * S).size(" ")[0]
    lead = (lead or size * 1.16) * S
    x, y, w, indent = x * S, y * S, w * S, indent * S
    bottom = None if bottom is None else bottom * S
    lines, cur, cur_w, avail = [], [], 0, w - indent
    for word in words:
        if cur and cur_w + space + word[2] > avail:
            lines.append(cur)
            cur, cur_w, avail = [word], word[2], w
        else:
            cur_w += (space if cur else 0) + word[2]
            cur.append(word)
    if cur:
        lines.append(cur)
    for i, line in enumerate(lines):
        if bottom is not None and y + lead > bottom + 0.5:
            return y / S, True
        x0 = x + (indent if i == 0 else 0)
        lw = w - (indent if i == 0 else 0)
        total = sum(ww for _, _, ww in line)
        n = len(line)
        gap = space
        if justify and i < len(lines) - 1 and n > 1:
            gap = min((lw - total) / (n - 1), space * 2.6)
        used = total + gap * (n - 1)
        cx = x0 + {"left": 0, "center": (lw - used) / 2, "right": lw - used}[align]
        for word, f, ww in line:
            surf.blit(f.render(word, True, color), (int(round(cx)), int(round(y))))
            cx += ww + gap
        y += lead
    return y / S, False


# ---------------------------------------------------------------------------
# Paper, creases, ink
# ---------------------------------------------------------------------------

def _paper(w, h, base, rng, edge=(160, 130, 80), edge_w=46, blotch=1.0):
    """Paper texture: base colour, fibres, blotches and darker, yellowed edges."""
    surf = pygame.Surface((w, h))
    surf.fill(base)
    _multiply_noise(surf, rng, [(int(130 * blotch) + 1, 222), (36, 232), (7, 238), (2, 236), (1, 244)])
    # yellowed / browned edges (stronger towards the corners)
    border = pygame.Surface((w, h))
    border.fill(WHITE)
    for i in range(edge_w):
        t = (i / edge_w) ** 0.55
        pygame.draw.rect(border, lerp_color(edge, WHITE, t), (i, i, w - 2 * i, h - 2 * i), 1)
    border = _soften(border, 0.25)
    blot = _value_noise(w, h, 60, 120, 255, rng)
    border.blit(blot, (0, 0), special_flags=pygame.BLEND_RGB_ADD)   # breaks up the straight band
    surf.blit(border, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    return surf


def _crease(surf, a, b, rng, strength=1.0, jitter=1.2, spread=10):
    """A fold line from a to b: a dark valley with a lit ridge beside it."""
    (x0, y0), (x1, y1) = a, b
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 2:
        return
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    nx, ny = -uy, ux
    n = max(2, int(length / 18))
    pts = []
    off = 0.0
    for i in range(n + 1):
        t = i / n
        off = clamp(off + rng.uniform(-jitter, jitter), -3, 3)
        pts.append((x0 + (x1 - x0) * t + nx * off, y0 + (y1 - y0) * t + ny * off))
    lay = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    lay.fill((0, 0, 0, 0))
    # broad shading: one side of the fold faces away from the light
    for d in range(1, spread + 1):
        a_ = int(30 * strength * (1 - d / (spread + 1)) ** 1.5)
        if a_ > 0:
            pygame.draw.lines(lay, (40, 30, 20, a_), False, [(px + nx * d, py + ny * d) for px, py in pts], 1)
    for d, col, al in ((-3, WHITE, 16), (-2, WHITE, 34), (-1, WHITE, 52), (0, (30, 22, 14), 96),
                       (1, (30, 22, 14), 60), (2, (30, 22, 14), 24)):
        pygame.draw.lines(lay, col + (int(al * strength),), False, [(px + nx * d, py + ny * d) for px, py in pts], 1)
    surf.blit(lay, (0, 0))


def _wrinkles(surf, rect, rng, count, strength=0.6):
    """Short creases scattered near the edges of rect."""
    x, y, w, h = rect
    for _ in range(count):
        side = rng.randrange(4)
        if side == 0:
            px, py = rng.uniform(x, x + w), y + rng.uniform(0, 60)
        elif side == 1:
            px, py = rng.uniform(x, x + w), y + h - rng.uniform(0, 60)
        elif side == 2:
            px, py = x + rng.uniform(0, 60), rng.uniform(y, y + h)
        else:
            px, py = x + w - rng.uniform(0, 60), rng.uniform(y, y + h)
        ang = rng.uniform(0, math.pi)
        ln = rng.uniform(20, 70)
        a = (px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2)
        b = (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)
        _crease(surf, a, b, rng, strength * rng.uniform(0.4, 1.0), jitter=0.8, spread=4)


def _ragged_edges(surf, rect, rng, color=BLACK, depth=2.2, step=5):
    """Nibble tiny chips out of a paper's edges (drawn in the background colour)."""
    x, y, w, h = rect
    for edge in range(4):
        n = int((w if edge < 2 else h) / step)
        for i in range(n):
            if rng.random() < 0.5:
                continue
            t = i * step + rng.uniform(0, step)
            d = rng.uniform(0.4, 1.0) * depth
            s = rng.uniform(2, 6)
            if edge == 0:
                pts = [(x + t - s, y - 1), (x + t + s, y - 1), (x + t, y + d)]
            elif edge == 1:
                pts = [(x + t - s, y + h), (x + t + s, y + h), (x + t, y + h - 1 - d)]
            elif edge == 2:
                pts = [(x - 1, y + t - s), (x - 1, y + t + s), (x + d, y + t)]
            else:
                pts = [(x + w, y + t - s), (x + w, y + t + s), (x + w - 1 - d, y + t)]
            pygame.draw.polygon(surf, color, pts)


def _ink_stroke(surf, color, pts, width, taper=True):
    """Pen stroke through pts: thicker on down-strokes, round joints, tapered ends."""
    n = len(pts)
    if n < 2:
        return
    for i in range(n - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        d = math.hypot(x1 - x0, y1 - y0) or 1e-6
        k = 0.55 + 0.6 * abs(y1 - y0) / d
        if taper:
            k *= min(1.0, 0.35 + 0.65 * min(i, n - 2 - i) / 6.0)
        r = max(0.6, width * k / 2)
        pygame.draw.line(surf, color, (x0, y0), (x1, y1), max(1, int(round(2 * r))))
        pygame.draw.circle(surf, color, (int(round(x1)), int(round(y1))), int(round(r)))


def _signature(surf, x, y, w, h, rng, color, width):
    """A loopy, slanted cursive scribble filling the box (x, y, w, h); y is the baseline."""
    slant = 0.38
    letters = []
    # two "words": a tall capital then small loops, with the odd tall letter
    for word_len in (rng.randint(3, 5), rng.randint(5, 7)):
        letters.append(("cap", 1.0))
        for _ in range(word_len):
            letters.append(("loop", 0.95 if rng.random() < 0.25 else rng.uniform(0.32, 0.45)))
        letters.append(("gap", 0))
    unit = w / (sum(2.0 if k == "cap" else 1.0 if k == "loop" else 0.8 for k, _ in letters) + 1.5)
    strokes, cur = [], []
    cx = x
    for kind, hk in letters:
        if kind == "gap":
            cur.append((cx + unit * 0.3, y + h * 0.05))
            strokes.append(cur)
            cur = []
            cx += unit * 0.8
            continue
        lw = unit * (2.0 if kind == "cap" else 1.0)
        lh = h * hk
        r = lw * (0.3 if kind == "cap" else 0.22)
        steps = 22 if kind == "cap" else 14
        for i in range(steps + 1):
            t = i / steps
            px = cx + lw * t - r * math.sin(2 * math.pi * t)
            py = y - lh * (1 - math.cos(2 * math.pi * t)) / 2 + rng.uniform(-0.6, 0.6)
            px += (y - py) * slant
            cur.append((px, py))
        cx += lw
    if cur:
        strokes.append(cur)
    for s in strokes:
        _ink_stroke(surf, color, s, width)
    # underline flourish sweeping back under the name
    fl = []
    for i in range(30):
        t = i / 29
        fl.append((lerp(cx - unit * 0.5, x + unit * 0.6, t),
                   y + h * 0.18 + math.sin(t * math.pi) * h * 0.12 - t * h * 0.05))
    _ink_stroke(surf, color, fl, width * 0.8)
    # dot / cross on a couple of letters
    for _ in range(2):
        px = rng.uniform(x + w * 0.3, x + w * 0.85)
        pygame.draw.circle(surf, color, (int(px), int(y - h * 0.62)), max(1, int(width * 0.6)))


def _stamp(lines, color, size, rng, angle=0.0, scale=2):
    """A distressed rubber-stamp impression (alpha surface, 1x)."""
    S = scale
    w, h = size
    surf = pygame.Surface((w * S, h * S), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))
    c = tuple(color[:3]) + (255,)
    pygame.draw.rect(surf, c, (2 * S, 2 * S, (w - 4) * S, (h - 4) * S), 5 * S, border_radius=9 * S)
    pygame.draw.rect(surf, c, (10 * S, 10 * S, (w - 20) * S, (h - 20) * S), 2 * S, border_radius=5 * S)
    n = len(lines)
    for i, (text, frac) in enumerate(lines):
        cy = h * (i + 0.5) / n if n > 1 else h / 2
        if n > 1:
            cy = lerp(h * 0.32, h * 0.7, i / (n - 1))
        _text(surf, text, (w / 2, cy + 1), "sans", h * frac, c, bold=True, anchor="center", squeeze=0.9,
              fit=w - 40, scale=S)
    # wear: blotchy ink and speckled gaps
    sw, sh = w * S, h * S
    noise = _value_noise(sw, sh, 3, 0, 255, rng)
    noise.blit(_value_noise(sw, sh, 26, 90, 255, rng), (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    lum = _tobytes(noise, "RGB")[0::3]
    table = bytes(int(clamp((i - 40) * 4.0, 0, 225)) for i in range(256))
    rgba = bytearray(b"\xff" * (4 * sw * sh))
    rgba[3::4] = lum.translate(table)
    mask = _frombytes(bytes(rgba), (sw, sh), "RGBA")
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    surf = pygame.transform.smoothscale(surf, (w, h))
    if angle:
        surf = pygame.transform.rotozoom(surf, angle, 1.0)
    return surf


def _drop_shadow(size, angle, alpha=170, blur=0.12, pad=30):
    """Soft shadow for a rectangular card of ``size``, rotated like the card."""
    w, h = size
    sh = pygame.Surface((w + 2 * pad, h + 2 * pad), pygame.SRCALPHA)
    sh.fill((0, 0, 0, 0))
    pygame.draw.rect(sh, (0, 0, 0, alpha), (pad, pad, w, h), border_radius=6)
    sh = _soften(sh, blur)
    if angle:
        sh = pygame.transform.rotozoom(sh, angle, 1.0)
    return sh


def _dark_desk(rng, base=(46, 34, 26), spot=(W // 2, H // 2 - 10)):
    """Dark wooden desk surface under a single dim lamp."""
    bg = pygame.Surface((W, H))
    bg.fill(base)
    # wood grain: long horizontal streaks of value noise
    grain = _value_noise(W // 24 + 1, H, 1, 150, 255, rng)
    grain = pygame.transform.smoothscale(grain, (W, H))
    grain = _soften(grain, 0.5)
    bg.blit(grain, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    for i in range(7):
        y = rng.uniform(0, H)
        pygame.draw.line(bg, shade(base, 0.62), (0, y), (W, y + rng.uniform(-6, 6)), 1)
    _multiply_noise(bg, rng, [(90, 200), (3, 225), (1, 235)])
    _light(bg, (60, 58, 58), [(spot[0], spot[1], W * 0.62, H * 0.78, (210, 200, 185))])
    return bg


def _place_card(bg, card, center, angle, shadow_offset=(10, 16)):
    """Blit an opaque card onto bg, rotated, with a soft shadow."""
    sh = _drop_shadow(card.get_size(), angle)
    bg.blit(sh, sh.get_rect(center=(center[0] + shadow_offset[0], center[1] + shadow_offset[1])))
    img = _alpha_copy(card)
    if angle:
        img = pygame.transform.rotozoom(img, angle, 1.0)
    bg.blit(img, img.get_rect(center=center))


def _grain(surf, rng, lo=214):
    w, h = surf.get_size()
    surf.blit(_value_noise(w, h, 1, lo, 255, rng), (0, 0), special_flags=pygame.BLEND_RGB_MULT)


# ===========================================================================
# The newspaper
# ===========================================================================

NEWS_INK = (30, 27, 24)
NEWS_PAPER = (226, 216, 186)

_ART1_HEAD = "Local Pizzeria Reopens Under New Management"
_ART1_DECK = "New owners promise a “fresh start” for a family favorite"
_ART1_BY = "By ELLEN MARSH, Staff Writer"
_ART1 = [
    "Freddy Fazbear's Pizza, the family restaurant known for its singing animal characters, reopened its "
    "doors this week following a brief closure. The new management says the restaurant's beloved mascots "
    "have been cleaned, repaired and returned to the stage, where they will once again perform for young "
    "guests throughout the day.",
    "“They're part of the family,” a company spokesman said. “Kids have grown up with Freddy and "
    "his friends, and we want to keep that magic alive.”",
    "The spokesman declined to comment on the reasons for the closure, calling them “a matter for the "
    "lawyers.” Business hours are unchanged, and the restaurant is hiring for several positions, "
    "including overnight security.",
    "Parents interviewed outside on Saturday were mostly positive. “The kids love it,” said one "
    "mother of three. “I just wish they would do something about the smell.”",
    "The characters will be available for birthday parties beginning next month.",
]
_ART2_HEAD = "Inspector Notes ‘Unusual Odor’ at Eatery"
_ART2_DECK = "Family restaurant passes inspection; source of smell unknown"
_ART2 = [
    "A routine inspection of a family restaurant found no violations last week, though the inspector's "
    "report noted an “unusual odor” in the dining area and near the stage.",
    "Management attributed the smell to the costumes worn by the restaurant's animatronic performers, "
    "which are cleaned regularly, according to a company statement.",
    "“These characters have been with us a long time,” the statement said. “A little wear and "
    "tear is to be expected.”",
    "The inspector recommended a follow-up visit, which has not yet been scheduled. Several diners said "
    "the odor had grown stronger in recent weeks. Others said they had not noticed anything at all.",
]
_BRIEFS = [
    ("Inquiry closed.", "Police have closed their inquiry at a local restaurant, citing a lack of evidence. "
                        "No charges were filed."),
    ("Flyers to come down.", "City crews will remove old notices from downtown lamp posts this week as "
                             "part of a spring clean-up."),
    ("Toy drive.", "Gently used stuffed animals are welcome at the community center through Friday."),
]
_CLASSIFIEDS = [
    ("FOR SALE", "Used animatronic parts: servos, wiring, one (1) bear suit, slightly worn. Must pick up. "
                 "Cash only."),
    ("FOUND", "Purple electric guitar, left at the bus stop on Third St. Call to claim. No questions asked."),
    ("MUSIC BOX REPAIR", "Antique music boxes cleaned and tuned. Toreador March a specialty. Evenings."),
]
AD_COPY_1 = ("Freddy Fazbear's Pizza, a magical place for kids and grown-ups alike, where fantasy and fun "
             "come to life!")
AD_COPY_2 = ("Night shift, 12 AM to 6 AM. Monitor cameras, make sure nothing gets damaged or stolen.")


def _star_pts(x, y, r, inner=0.45, rot=0.0):
    pts = []
    for i in range(10):
        a = rot + math.pi * i / 5 - math.pi / 2
        rr = r if i % 2 == 0 else r * inner
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    return pts


def _stage_photo(w, h, rng):
    """Bonnie, Freddy and Chica on stage as a grainy halftone print (greyscale ink surface, w x h)."""
    S = 2
    pw, ph = w * S, h * S
    surf = pygame.Surface((pw, ph))
    back = (52, 36, 64)
    surf.fill(back)
    # backdrop curtain folds
    folds = 11
    for x in range(0, pw, 2):
        k = 0.7 + 0.3 * math.sin(x / pw * folds * 2 * math.pi) ** 2
        pygame.draw.line(surf, shade(back, k), (x, 0), (x, ph), 2)
    for _ in range(14):
        sx, sy = rng.uniform(10, pw - 10), rng.uniform(10, ph * 0.55)
        pygame.draw.polygon(surf, (210, 196, 120), _star_pts(sx, sy, rng.uniform(7, 13), rot=rng.uniform(0, 1)))
    # stage floor
    fy = int(ph * 0.83)
    pygame.draw.rect(surf, (120, 92, 70), (0, fy, pw, ph - fy))
    pygame.draw.rect(surf, (70, 50, 40), (0, fy, pw, 6))
    sc = ph / 7.6
    head_y = 3.1 * sc + 10
    cast = (("Bonnie", 0.2, 0.92, 0), ("Chica", 0.8, 0.94, 6), ("Freddy", 0.5, 1.04, 10))
    for name, fx, k, dy in cast:
        spr = characters.render_character(name, max(4, int(sc * k)), ss=1, eyes="normal", prop=True)
        spr.blit(surf, (pw * fx, head_y + dy))
    # camera flash: bright on the band, dark falling off behind
    _light(surf, (70, 66, 64), [(pw * 0.5, ph * 0.42, pw * 0.75, ph * 0.75, (220, 220, 220))], q=2)
    lum = _luma(surf)
    lum = lum.translate(_curve(lambda v: clamp((v - 0.08) * 1.35, 0, 1) ** 0.9))
    dots = _halftone(lum, pw, ph, period=10, soft=3.0)
    soft = _luma(_soften(_grey_surface(lum, (pw, ph)), 0.5))
    mix = _mix_bytes(dots, soft, 0.42)
    img = _grey_surface(mix, (pw, ph), dark=(20, 20, 20), light=(250, 250, 250))
    return pygame.transform.smoothscale(img, (w, h))


def _news_article(ink, x, y, w, bottom, head, deck, byline, paras, rng, head_size=25, cont=None):
    """Headline, deck, byline and body text in one column. Returns the y it ended at."""
    y, _ = _para(ink, [(head, True, False)], x, y, w, "serif", head_size, NEWS_INK, lead=head_size * 1.02,
                 justify=False, align="center")
    pygame.draw.line(ink, NEWS_INK, (x + w * 0.35, y + 5), (x + w * 0.65, y + 5), 1)
    y += 10
    if deck:
        y, _ = _para(ink, [(deck, False, True)], x, y, w, "serif", 14, NEWS_INK, justify=False, align="center")
        y += 4
    if byline:
        _text(ink, byline, (x + w / 2, y), "serif", 10, NEWS_INK, anchor="midtop", fit=w)
        y += 16
    for i, p in enumerate(paras):
        body_bottom = bottom - (14 if cont else 0)
        y, over = _para(ink, p, x, y, w, "serif", 12, NEWS_INK, lead=13.6, indent=10, bottom=body_bottom)
        if over:
            if cont:
                _text(ink, cont, (x + w, y + 1), "serif", 10, NEWS_INK, italic=True, anchor="topright")
            return bottom
        y += 1
    return y


def _news_layout(ink, rng):
    PW, PH = ink.get_size()
    m = 26
    I = NEWS_INK
    # -- masthead ----------------------------------------------------------
    pygame.draw.line(ink, I, (m, 16), (PW - m, 16), 1)
    _text(ink, "The Daily Clarion", (PW / 2, 60), "serif", 76, I, bold=True, anchor="center", squeeze=0.94)
    for ex, al in ((m, "left"), (PW - m - 168, "right")):
        pygame.draw.rect(ink, I, (ex, 26, 168, 64), 1)
    _text(ink, "LATE CITY EDITION", (m + 84, 36), "sans", 12, I, bold=True, anchor="midtop")
    _text(ink, "Classified Advertising", (m + 84, 53), "serif", 13, I, italic=True, anchor="midtop")
    _text(ink, "Section B", (m + 84, 70), "serif", 12, I, anchor="midtop")
    rx = PW - m - 84
    _text(ink, "WEATHER", (rx, 33), "sans", 12, I, bold=True, anchor="midtop")
    _text(ink, "Clear and cold tonight.", (rx, 50), "serif", 12, I, anchor="midtop")
    _text(ink, "Low 38. Wind light.", (rx, 66), "serif", 12, I, anchor="midtop")
    pygame.draw.rect(ink, I, (m, 100, PW - 2 * m, 3))
    pygame.draw.line(ink, I, (m, 106), (PW - m, 106), 1)
    _text(ink, "VOL. LXXI — No. 233", (m + 2, 111), "serif", 12, I)
    _text(ink, "CLASSIFIED ADVERTISING  •  HELP WANTED  •  NOTICES", (PW / 2, 111), "serif", 12, I,
          anchor="midtop")
    _text(ink, "35 CENTS", (PW - m - 2, 111), "serif", 12, I, anchor="topright")
    pygame.draw.line(ink, I, (m, 129), (PW - m, 129), 1)
    # -- columns -------------------------------------------------------------
    top, bottom = 142, PH - 22
    cw = 198
    lx, rxc = m, PW - m - cw
    cx0, cx1 = m + cw + 18, PW - m - cw - 18
    for x in (m + cw + 9, PW - m - cw - 9):
        pygame.draw.line(ink, I, (x, top), (x, bottom), 1)
    # left column: the reopening
    y = _news_article(ink, lx, top, cw, bottom - 130, _ART1_HEAD, _ART1_DECK, _ART1_BY, _ART1, rng,
                      cont="See PIZZERIA, Page B4")
    y = max(y, bottom - 120)
    pygame.draw.line(ink, I, (lx, y), (lx + cw, y), 2)
    y += 8
    y, _ = _para(ink, [("Costume Shop to Expand", True, False)], lx, y, cw, "serif", 17, I, justify=False,
                 align="center")
    y += 4
    _para(ink, "A downtown costume rental shop will double its floor space next month. “Mascot suits "
               "are big these days,” the owner said. “Everybody wants to be somebody else.”",
          lx, y, cw, "serif", 12, I, lead=13.6, indent=10, bottom=bottom)
    # right column: the inspection, then briefs
    y = _news_article(ink, rxc, top, cw, bottom - 196, _ART2_HEAD, _ART2_DECK, None, _ART2, rng, head_size=22)
    y = max(y + 6, bottom - 190)
    pygame.draw.rect(ink, I, (rxc, y, cw, bottom - y), 1)
    _text(ink, "IN BRIEF", (rxc + cw / 2, y + 7), "sans", 13, I, bold=True, anchor="midtop")
    y += 26
    for lead_in, body in _BRIEFS:
        y, _ = _para(ink, [(lead_in, True, False), (body, False, False)], rxc + 8, y, cw - 16, "serif", 12, I,
                     lead=13.6, bottom=bottom - 4)
        y += 5
    # -- the ad ---------------------------------------------------------------
    aw = cx1 - cx0
    ad = pygame.Rect(cx0, top + 2, aw, 448)
    pygame.draw.rect(ink, I, ad, 4)
    pygame.draw.rect(ink, I, ad.inflate(-14, -14), 1)
    _text(ink, "HELP WANTED", (ad.centerx, ad.y + 66), "sans", 112, I, bold=True, anchor="center",
          squeeze=0.86, fit=aw - 44)
    pygame.draw.line(ink, I, (ad.x + 22, ad.y + 122), (ad.right - 22, ad.y + 122), 2)
    ph = pygame.Rect(ad.x + 22, ad.y + 134, 304, 226)
    ink.blit(_stage_photo(ph.w, ph.h, rng), ph)
    pygame.draw.rect(ink, I, ph, 1)
    _text(ink, "Freddy and friends welcome guests of all ages.", (ph.centerx, ph.bottom + 5), "serif", 12, I,
          italic=True, anchor="midtop", fit=ph.w)
    tx, tw = ph.right + 18, ad.right - 22 - ph.right - 18
    y = ph.y - 3
    y, _ = _para(ink, AD_COPY_1, tx, y, tw, "serif", 19, I, lead=21.5, justify=False)
    y += 9
    y, _ = _para(ink, [("Security guard needed.", True, False), (AD_COPY_2, False, False)], tx, y, tw,
                 "serif", 19, I, lead=21.5, justify=False)
    y += 8
    _text(ink, "$120 a week.", (tx, y), "serif", 36, I, bold=True)
    pygame.draw.line(ink, I, (ad.x + 22, ad.bottom - 66), (ad.right - 22, ad.bottom - 66), 1)
    _text(ink, "Call 1-888-FAZBEAR", (ad.centerx, ad.bottom - 46), "sans", 27, I, bold=True, anchor="center")
    _text(ink, "Not responsible for injury or dismemberment.", (ad.centerx, ad.bottom - 22), "serif", 13, I,
          italic=True, anchor="center")
    # -- classifieds under the ad -----------------------------------------
    y0 = ad.bottom + 12
    pygame.draw.line(ink, I, (cx0, y0 - 5), (cx1, y0 - 5), 1)
    n = len(_CLASSIFIEDS)
    gw = (aw - 2 * 14) / n
    for i, (lead_in, body) in enumerate(_CLASSIFIEDS):
        x = cx0 + i * (gw + 14)
        if i:
            pygame.draw.line(ink, I, (x - 7, y0), (x - 7, bottom), 1)
        _para(ink, [(lead_in + " —", True, False), (body, False, False)], x, y0, gw, "serif", 12, I,
              lead=13.4, bottom=bottom + 2)


def newspaper():
    """The help-wanted newspaper page on black (opaque, SCREEN_W x SCREEN_H)."""
    _ensure_fonts()
    rng = random.Random(1987)
    PW, PH = 1108, 698
    ink = pygame.Surface((PW, PH))
    ink.fill(WHITE)
    _news_layout(ink, rng)
    # printing: slight ink spread and uneven density
    ink = _soften(ink, 0.8)
    ink.blit(_value_noise(PW, PH, 2, 0, 34, rng), (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    ink.blit(_value_noise(PW, PH, 40, 0, 26, rng), (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    page = _paper(PW, PH, NEWS_PAPER, rng)
    page.blit(ink, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    # folded in quarters, then handled a lot
    _crease(page, (0, PH * 0.49), (PW, PH * 0.505), rng, 1.0, spread=14)
    _crease(page, (PW * 0.5, 0), (PW * 0.497, PH), rng, 0.7, spread=10)
    _wrinkles(page, (0, 0, PW, PH), rng, 16, 0.7)
    # coffee ring on the corner of the left column
    ring = pygame.Surface((PW, PH), pygame.SRCALPHA)
    ring.fill((0, 0, 0, 0))
    cxr, cyr = 150, PH - 100
    for i in range(6):
        pygame.draw.circle(ring, (120, 80, 30, 10 + i * 4), (cxr, cyr), 58 - i, 2)
    pygame.draw.circle(ring, (130, 90, 40, 14), (cxr, cyr), 56)
    page.blit(_soften(ring, 0.5), (0, 0))
    # dim, uneven light, a little warm
    _light(page, (150, 138, 120), [(PW * 0.52, PH * 0.45, PW * 0.78, PH * 0.95, (120, 120, 116))])
    _grain(page, rng, 226)
    out = pygame.Surface((W, H))
    out.fill(BLACK)
    px, py = (W - PW) // 2, (H - PH) // 2
    out.blit(page, (px, py))
    _ragged_edges(out, (px, py, PW, PH), rng)
    return _finish(out)


# ===========================================================================
# Checks, pink slip, shift log
# ===========================================================================

CHECK_PAPER = (204, 224, 210)
CHECK_INK = (34, 62, 50)
TYPE_INK = (26, 26, 30)
PEN_BLUE = (28, 40, 120)


def _guilloche(surf, rect, color, rng, S):
    """Security pattern: interleaved fine sine waves across the rect."""
    x, y, w, h = rect
    for j in range(46):
        amp = h * (0.06 + 0.05 * math.sin(j * 0.7))
        ph_ = j * 0.42
        base = y + (j + 0.5) * h / 46
        pts = []
        for i in range(0, int(w) + 1, 6):
            t = i / w
            pts.append(((x + i) * S, (base + amp * math.sin(t * 2 * math.pi * 3.0 + ph_)
                                       + amp * 0.4 * math.sin(t * 2 * math.pi * 11 + ph_ * 2)) * S))
        pygame.draw.lines(surf, color, False, pts, max(1, S // 2))


def _rosette(surf, cx, cy, r, color, S, k=7, petals=18):
    """Spirograph rosette (as on bank notes)."""
    pts = []
    n = 900
    R, rr, d = r, r / k * 2.0, r * 0.62
    for i in range(n + 1):
        t = 2 * math.pi * i / n * petals / 2
        x = (R - rr) * math.cos(t) + d * math.cos((R - rr) / rr * t)
        y = (R - rr) * math.sin(t) - d * math.sin((R - rr) / rr * t)
        sc = r / (abs(R - rr) + d)
        pts.append(((cx + x * sc) * S, (cy + y * sc) * S))
    pygame.draw.lines(surf, color, False, pts, max(1, S // 2))


def _logo(size, ink):
    """Freddy's head printed as a one-colour emblem (alpha surface)."""
    spr = characters.render_character("Freddy", max(4, int(size / 2.3)), ss=2, body=False, eyes="normal")
    head = spr.surface
    ax, ay = spr.anchor
    s = size / 2.3
    crop = pygame.Rect(0, 0, head.get_width(), int(min(head.get_height(), ay + 1.12 * s)))
    head = head.subsurface(crop).copy()
    lum = _luma(head)
    lum = lum.translate(_curve(lambda v: clamp((v - 0.05) * 1.6, 0, 1)))
    img = _grey_surface(lum, head.get_size(), dark=ink, light=lerp_color(ink, WHITE, 0.72))
    out = _alpha_copy(img)
    alpha = _tobytes(head, "RGBA")[3::4]
    rgba = bytearray(_tobytes(out, "RGBA"))
    rgba[3::4] = alpha
    return _frombytes(bytes(rgba), head.get_size(), "RGBA")


def _micr(surf, x, y, groups, color, S, size=22):
    """MICR-style account line: digit groups separated by transit/on-us symbols."""
    f = _font("mono", size * S, bold=True)
    cx = x * S
    for g in groups:
        if g in ("T", "U"):
            h = size * 0.62 * S
            yy = (y + size * 0.18) * S
            bw = max(2, int(size * 0.14 * S))
            if g == "T":   # transit: bar + two stacked squares
                pygame.draw.rect(surf, color, (cx, yy, bw, h))
                pygame.draw.rect(surf, color, (cx + bw * 2, yy, bw * 2, h * 0.38))
                pygame.draw.rect(surf, color, (cx + bw * 2, yy + h * 0.62, bw * 2, h * 0.38))
            else:          # on-us: two bars and a square
                pygame.draw.rect(surf, color, (cx, yy, bw * 1.5, h * 0.6))
                pygame.draw.rect(surf, color, (cx + bw * 2.4, yy, bw * 1.5, h * 0.6))
                pygame.draw.rect(surf, color, (cx + bw * 4.6, yy + h * 0.62, bw * 1.5, h * 0.38))
            cx += bw * 7
        else:
            img = f.render(g, True, color)
            surf.blit(img, (cx, y * S))
            cx += img.get_width() + size * 0.5 * S


def _check_card(kind, rng):
    """The paycheck itself (opaque, 1x)."""
    S = 2
    cw, ch = 920, 384
    big = pygame.Surface((cw * S, ch * S))
    big.fill(CHECK_PAPER)
    pen = Pen(big, 0, 0, S)
    pen.hgrad(0, 0, cw, ch, (210, 228, 212), (194, 218, 214))
    pale = lerp_color(CHECK_PAPER, CHECK_INK, 0.13)
    _guilloche(big, (14, 14, cw - 28, ch - 28), pale, rng, S)
    _rosette(big, cw * 0.56, ch * 0.5, 120, lerp_color(CHECK_PAPER, CHECK_INK, 0.17), S)
    _rosette(big, cw * 0.56, ch * 0.5, 70, lerp_color(CHECK_PAPER, CHECK_INK, 0.2), S, k=5, petals=14)
    # border
    pen.rect(CHECK_INK, 8, 8, cw - 16, ch - 16, width=2.5)
    pen.rect(lerp_color(CHECK_PAPER, CHECK_INK, 0.5), 14, 14, cw - 28, ch - 28, width=1)
    # header
    logo = _logo(66 * S, CHECK_INK)
    big.blit(logo, (28 * S, 22 * S))
    _text(big, "Freddy Fazbear's Pizza", (106, 22), "serif", 38, CHECK_INK, bold=True, italic=True, scale=S)
    _text(big, "FAZBEAR ENTERTAINMENT, INC.  •  PAYROLL ACCOUNT", (108, 66), "sans", 12, CHECK_INK,
          bold=True, scale=S)
    _text(big, "A magical place for kids and grown-ups alike", (108, 84), "serif", 12, CHECK_INK, italic=True,
          scale=S)
    _text(big, "No. 0512" if kind == "week" else "No. 0519", (cw - 34, 26), "mono", 22, (150, 36, 32), bold=True,
          anchor="topright", scale=S)
    _text(big, "DATE", (cw - 268, 74), "sans", 12, CHECK_INK, bold=True, scale=S)
    pen.line(CHECK_INK, (cw - 226, 88), (cw - 34, 88), 1)
    _text(big, "NOV. 14" if kind == "week" else "NOV. 15", (cw - 130, 86), "mono", 22, TYPE_INK, bold=True,
          anchor="midbottom", scale=S)
    # pay to the order of
    _text(big, "PAY TO THE", (30, 130), "sans", 12, CHECK_INK, bold=True, scale=S)
    _text(big, "ORDER OF", (30, 145), "sans", 12, CHECK_INK, bold=True, scale=S)
    pen.line(CHECK_INK, (112, 160), (cw - 236, 160), 1)
    _text(big, "Night Security", (126, 157), "mono", 30, TYPE_INK, bold=True, anchor="bottomleft", scale=S)
    # amount box
    bx, by, bw, bh = cw - 206, 122, 172, 46
    _text(big, "$", (bx - 10, by + bh / 2), "serif", 34, CHECK_INK, bold=True, anchor="midright", scale=S)
    pen.rect((228, 240, 230), bx, by, bw, bh)
    pen.rect(CHECK_INK, bx, by, bw, bh, width=1.5)
    _text(big, "**120.50**", (bx + bw / 2, by + bh / 2 + 1), "mono", 28, TYPE_INK, bold=True, anchor="center",
          fit=bw - 10, scale=S)
    # amount in words
    pen.line(CHECK_INK, (30, 214), (cw - 124, 214), 1)
    r = _text(big, "One hundred twenty dollars and 50/100", (36, 211), "mono", 23, TYPE_INK, bold=True,
              anchor="bottomleft", scale=S)
    x = r.right / S + 10
    while x < cw - 140:
        pen.line(TYPE_INK, (x, 205), (x + 9, 205), 1.5)
        x += 14
    _text(big, "DOLLARS", (cw - 34, 214), "sans", 13, CHECK_INK, bold=True, anchor="bottomright", scale=S)
    # bank
    _text(big, "SECOND COUNTY SAVINGS BANK", (30, 236), "serif", 14, CHECK_INK, bold=True, scale=S)
    _text(big, "Main Street Branch", (30, 254), "serif", 12, CHECK_INK, italic=True, scale=S)
    # memo
    _text(big, "MEMO", (30, 300), "sans", 12, CHECK_INK, bold=True, scale=S)
    pen.line(CHECK_INK, (76, 314), (400, 314), 1)
    memo = "Good job! Week 1" if kind == "week" else "Night 6"
    hand = _font("hand", 30 * S, italic=True)
    img = hand.render(memo, True, PEN_BLUE)
    img = pygame.transform.rotozoom(img, 2.5, 1.0)
    big.blit(img, img.get_rect(midbottom=(int(90 * S + img.get_width() / 2), int(318 * S))))
    # signature
    pen.line(CHECK_INK, (cw - 380, 314), (cw - 34, 314), 1)
    _text(big, "AUTHORIZED SIGNATURE", (cw - 207, 320), "sans", 10, CHECK_INK, bold=True, anchor="midtop",
          scale=S)
    _signature(big, (cw - 352) * S, 300 * S, 300 * S, 50 * S, random.Random(41), (22, 30, 92), 3.2 * S / 2)
    # MICR line
    _micr(big, 92, 346, ["T", "071900948", "T", "  ", "2209 7731", "U", "0512"], (40, 40, 44), S)
    card = pygame.transform.smoothscale(big, (cw, ch))
    _multiply_noise(card, rng, [(70, 232), (2, 238)])
    return card


def _sticky_note(lines, rng, size=(220, 168), color=(240, 222, 110)):
    w, h = size
    S = 2
    big = pygame.Surface((w * S, h * S))
    big.fill(color)
    pen = Pen(big, 0, 0, S)
    pen.vgrad(0, 0, w, 26, shade(color, 0.93), color)
    hand = _font("hand", 30 * S, italic=True)
    y = 40
    for text, k in lines:
        img = hand.render(text, True, (36, 36, 60))
        if k != 1.0:
            img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
        if img.get_width() > (w - 20) * S:
            f = (w - 20) * S / img.get_width()
            img = pygame.transform.smoothscale(img, (int(img.get_width() * f), int(img.get_height() * f)))
        big.blit(img, img.get_rect(midtop=(w * S // 2, int(y * S))))
        y += img.get_height() / S + 4
    card = pygame.transform.smoothscale(big, (w, h))
    _multiply_noise(card, rng, [(40, 225), (2, 240)])
    # curled bottom edge
    for i in range(14):
        a = int(30 * (i / 14) ** 2)
        lay = pygame.Surface((w, 1), pygame.SRCALPHA)
        lay.fill((0, 0, 0, a))
        card.blit(lay, (0, h - 14 + i))
    return card


def _pink_slip(rng):
    S = 2
    cw, ch = 860, 470
    paper = (240, 202, 212)
    red = (128, 34, 58)
    big = pygame.Surface((cw * S, ch * S))
    big.fill(paper)
    pen = Pen(big, 0, 0, S)
    pen.vgrad(0, 0, cw, ch, (244, 208, 218), (232, 194, 206))
    pen.rect(red, 10, 10, cw - 20, ch - 20, width=1.5)
    _text(big, "FREDDY FAZBEAR'S PIZZA", (32, 26), "sans", 15, red, bold=True, scale=S)
    _text(big, "Fazbear Entertainment, Inc.  •  Personnel Department", (32, 46), "serif", 13, red,
          italic=True, scale=S)
    _text(big, "FORM 7-B", (cw - 32, 26), "sans", 15, red, bold=True, anchor="topright", scale=S)
    _text(big, "EMPLOYEE COPY", (cw - 32, 46), "sans", 12, red, anchor="topright", scale=S)
    pen.rect(red, 30, 72, cw - 60, 3)
    _text(big, "NOTICE OF TERMINATION", (cw / 2, 112), "serif", 52, red, bold=True, anchor="center",
          squeeze=0.95, scale=S)
    pen.rect(red, 30, 146, cw - 60, 1.5)

    def field(label, x, y, w, value=None, size=22):
        _text(big, label, (x, y), "sans", 12, red, bold=True, anchor="bottomleft", scale=S)
        lw = _font("sans", 12 * S, True).size(label)[0] / S
        pen.line(red, (x + lw + 8, y), (x + w, y), 1)
        if value:
            _text(big, value, (x + lw + 16, y - 1), "mono", size, TYPE_INK, bold=True, anchor="bottomleft",
                  scale=S)

    field("EMPLOYEE:", 32, 192, 500, "Night Security")
    field("DATE:", 560, 192, 268, "NOV. 16")
    field("POSITION:", 32, 232, 796, "Security Guard, Night Shift (12 AM - 6 AM)", size=20)
    _text(big, "REASON FOR TERMINATION:", (32, 270), "sans", 12, red, bold=True, anchor="bottomleft", scale=S)
    for y in (300, 334, 368):
        pen.line(red, (32, y), (cw - 32, y), 1)
    _text(big, "Tampering with the animatronics, general", (40, 298), "mono", 22, TYPE_INK, bold=True,
          anchor="bottomleft", scale=S)
    _text(big, "unprofessionalism, odor.", (40, 332), "mono", 22, TYPE_INK, bold=True, anchor="bottomleft", scale=S)
    # checkboxes
    for i, (label, ticked) in enumerate((("Attendance", False), ("Conduct", True), ("Hygiene", True))):
        x = 470 + i * 128
        pen.rect(red, x, 352 - 2, 13, 13, width=1.2)
        _text(big, label, (x + 19, 366), "sans", 12, red, anchor="bottomleft", scale=S)
        if ticked:
            _text(big, "X", (x + 6.5, 357), "mono", 18, TYPE_INK, bold=True, anchor="center", scale=S)
    # signature
    pen.line(red, (cw - 360, 430), (cw - 32, 430), 1)
    _text(big, "MANAGEMENT", (cw - 196, 436), "sans", 11, red, bold=True, anchor="midtop", scale=S)
    _signature(big, (cw - 340) * S, 416 * S, 280 * S, 44 * S, random.Random(7), (24, 28, 70), 3.0 * S / 2)
    _text(big, "Employee is to return keys, flashlight and uniform.", (32, 428), "serif", 13, red, italic=True,
          anchor="bottomleft", scale=S)
    card = pygame.transform.smoothscale(big, (cw, ch))
    _multiply_noise(card, rng, [(60, 228), (2, 236)])
    st = _stamp([("EFFECTIVE", 0.3), ("IMMEDIATELY", 0.3)], (176, 30, 40), (260, 104), rng, angle=9)
    card.blit(st, st.get_rect(center=(cw - 200, 290)))
    return card


def _shift_log(rng):
    S = 2
    cw, ch = 880, 440
    paper = (226, 210, 168)
    ink = (60, 52, 40)
    big = pygame.Surface((cw * S, ch * S))
    big.fill(paper)
    pen = Pen(big, 0, 0, S)
    pen.vgrad(0, 0, cw, ch, (232, 216, 176), (216, 198, 154))
    pen.rect(ink, 10, 10, cw - 20, ch - 20, width=1.5)
    _text(big, "FREDDY FAZBEAR'S PIZZA", (30, 24), "sans", 15, ink, bold=True, scale=S)
    _text(big, "NIGHT SHIFT LOG", (30, 44), "serif", 34, ink, bold=True, scale=S)
    _text(big, "EMPLOYEE:", (cw - 360, 44), "sans", 12, ink, bold=True, scale=S)
    _text(big, "Night Security", (cw - 280, 38), "mono", 22, TYPE_INK, bold=True, scale=S)
    pen.line(ink, (cw - 284, 62), (cw - 32, 62), 1)
    _text(big, "SHIFT:", (cw - 360, 74), "sans", 12, ink, bold=True, scale=S)
    _text(big, "12 AM - 6 AM", (cw - 280, 68), "mono", 22, TYPE_INK, bold=True, scale=S)
    pen.line(ink, (cw - 284, 92), (cw - 32, 92), 1)
    # table
    tx, ty, rh = 30, 112, 38
    cols = [(0, "TIME"), (120, "DOORS"), (230, "POWER"), (340, "NOTES")]
    pen.rect(ink, tx, ty, cw - 60, 26)
    for x, label in cols:
        _text(big, label, (tx + x + 10, ty + 13), "sans", 12, paper, bold=True, anchor="midleft", scale=S)
    times = ["12 AM", "1 AM", "2 AM", "3 AM", "4 AM", "5 AM", "6 AM"]
    notes = ["quiet. phone message.", "footsteps, west hall", "kitchen noises (Chica?)",
             "someone in 2B?", "pirate cove curtain open", "power low. door shut.", "6 AM !!"]
    power = ["100%", "88%", "71%", "52%", "34%", "17%", "4%"]
    hand = _font("hand", 21 * S, italic=True)
    for i, t in enumerate(times):
        y = ty + 26 + i * rh
        if i % 2:
            pen.rect(shade(paper, 0.95), tx, y, cw - 60, rh)
        pen.line(ink, (tx, y + rh), (cw - 30, y + rh), 1)
        _text(big, t, (tx + 10, y + rh / 2 + 1), "mono", 20, TYPE_INK, bold=True, anchor="midleft", scale=S)
        # door check mark
        mx, my = tx + 150, y + rh / 2
        _ink_stroke(big, PEN_BLUE, [((mx - 8) * S, my * S), ((mx - 2) * S, (my + 7) * S),
                                    ((mx + 12) * S, (my - 9) * S)], 2.6 * S, taper=False)
        img = hand.render(power[i], True, PEN_BLUE)
        big.blit(img, img.get_rect(midleft=((tx + 240) * S, int((y + rh / 2) * S))))
        img = hand.render(notes[i], True, PEN_BLUE)
        if img.get_width() > (cw - 60 - 350) * S:
            k = (cw - 60 - 350) * S / img.get_width()
            img = pygame.transform.smoothscale(img, (int(img.get_width() * k), int(img.get_height() * k)))
        big.blit(img, img.get_rect(midleft=((tx + 350) * S, int((y + rh / 2) * S))))
    for x, _ in cols[1:]:
        pen.line(ink, (tx + x, ty), (tx + x, ty + 26 + 7 * rh), 1)
    _text(big, "SUPERVISOR:", (30, ch - 30), "sans", 12, ink, bold=True, anchor="bottomleft", scale=S)
    pen.line(ink, (118, ch - 32), (400, ch - 32), 1)
    _signature(big, 132 * S, (ch - 38) * S, 230 * S, 36 * S, random.Random(7), (24, 28, 70), 2.6 * S / 2)
    card = pygame.transform.smoothscale(big, (cw, ch))
    _multiply_noise(card, rng, [(50, 224), (2, 236)])
    st = _stamp([("SHIFT COMPLETE", 0.42)], (182, 28, 34), (420, 110), rng, angle=-11)
    card.blit(st, st.get_rect(center=(cw * 0.6, ch * 0.56)))
    return card


def paycheck(kind="week"):
    """End-of-week card on a dark desk (opaque, SCREEN_W x SCREEN_H).

    kind: "week"     paycheck for $120.50, memo "Good job! Week 1"
          "overtime" the same check (memo "Night 6") with an "overtime: $0.50" note
          "fired"    pink NOTICE OF TERMINATION slip
          "custom"   night shift log stamped SHIFT COMPLETE
    The card sits a little below centre, leaving ~150 px clear at the top
    and bottom for captions.
    """
    _ensure_fonts()
    if kind not in ("week", "overtime", "fired", "custom"):
        kind = "week"
    rng = random.Random("paycheck-" + kind)
    center = (W // 2, H // 2 + 12)
    bg = _dark_desk(rng, spot=center)
    if kind in ("week", "overtime"):
        card = _check_card(kind, rng)
        _place_card(bg, card, center, 1.6)
        if kind == "overtime":
            note = _sticky_note([("overtime:", 1.0), ("$0.50", 1.25)], rng)
            _place_card(bg, note, (center[0] + 360, center[1] - 150), -7, shadow_offset=(6, 9))
    elif kind == "fired":
        _place_card(bg, _pink_slip(rng), center, -1.4)
    else:
        _place_card(bg, _shift_log(rng), center, 1.2)
    _grain(bg, rng, 232)
    return _finish(bg)


# ===========================================================================
# Game over: the stuffed suit
# ===========================================================================

def _human_eyes_supported():
    """True when characters.draw_character knows eyes="human" (it falls back to
    "normal" for unknown modes, so compare two tiny renders)."""
    try:
        a = characters.render_character("Freddy", 14, ss=1, body=False, eyes="human")
        b = characters.render_character("Freddy", 14, ss=1, body=False, eyes="normal")
        return _tobytes(a.surface, "RGBA") != _tobytes(b.surface, "RGBA")
    except Exception:
        return False


def _blob(pen, col, x, y, rx, ry, lo=0.55, hi=1.1, n=7, light=(-0.38, -0.55), outline=True):
    """Soft shaded ellipse (like the character art's blobs)."""
    if outline:
        pen.ellipse(shade(col, 0.32), x, y, rx * 1.06, ry * 1.06)
    for i in range(n):
        t = i / (n - 1)
        k = 0.6 * t
        pen.ellipse(shade(col, lerp(lo, hi, t)), x + light[0] * rx * k, y + light[1] * ry * k,
                    rx * (1 - k), ry * (1 - k))


def _draw_human_eye(pen, x, y, r, rng, look=(0.0, 0.0)):
    """Bloodshot eyeball pushed into a socket (fallback when characters lacks eyes='human')."""
    pen.ellipse((20, 6, 6), x, y, r * 1.12, r * 1.04)
    pen.ellipse((182, 120, 108), x, y, r, r * 0.92)
    pen.ellipse((222, 198, 180), x - r * 0.06, y - r * 0.06, r * 0.86, r * 0.8)
    pen.ellipse((236, 226, 212), x - r * 0.12, y - r * 0.12, r * 0.6, r * 0.55)
    for _ in range(9):
        a = rng.uniform(0, 2 * math.pi)
        p0 = (x + math.cos(a) * r * 0.92, y + math.sin(a) * r * 0.85)
        p1 = (x + math.cos(a + rng.uniform(-0.3, 0.3)) * r * 0.5, y + math.sin(a) * r * 0.45)
        pen.line((170, 30, 30), p0, p1, max(0.6, r * 0.05))
    ix, iy = x + look[0] * r * 0.3, y + look[1] * r * 0.3
    pen.circle((40, 26, 14), ix, iy, r * 0.46)
    pen.circle((110, 78, 38), ix, iy, r * 0.4)
    pen.circle((140, 108, 58), ix - r * 0.04, iy - r * 0.04, r * 0.26)
    pen.circle((6, 3, 3), ix, iy, r * 0.17)
    pen.circle((255, 250, 240), ix - r * 0.15, iy - r * 0.16, r * 0.09)


def _suit_sprite(scale, human):
    """The slumped Freddy suit (alpha surface at 1x, anchor = head centre).

    Uses the Golden Freddy 'slump' pose with Freddy's colours when the
    character module allows it; otherwise a standing Freddy.
    """
    eyes = "human" if human else "none"
    mouth = 0.2
    pal = getattr(characters, "_PAL", None)
    if isinstance(pal, dict) and "Freddy" in pal and "Golden" in pal:
        name, pose, tilt = "Golden", "slump", 22.0
    else:
        name, pose, tilt = "Freddy", "stand", 0.0
    x0, y0, x1, y1 = characters.bounds(name, True, pose)
    pad = 0.4
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    S = 2
    w, h = int((x1 - x0) * scale), int((y1 - y0) * scale)
    big = pygame.Surface((w * S, h * S), pygame.SRCALPHA)
    big.fill((0, 0, 0, 0))
    pen = Pen(big, -x0 * scale * S, -y0 * scale * S, scale * S)
    saved = None
    if name == "Golden":
        saved = pal["Golden"]
        pal["Golden"] = dict(pal["Freddy"], lid=0.08, grime=16)
    try:
        characters.draw_character(pen, name, eyes=eyes, mouth=mouth, pose=pose, prop=False)
    finally:
        if saved is not None:
            pal["Golden"] = saved
    fur = (128, 78, 42)
    belly = (192, 146, 94)
    if pose == "slump":
        # lumps: something is packed in there that doesn't fit
        for bx, by, rx, ry, col in ((-0.5, 1.75, 0.36, 0.3, fur), (0.62, 1.55, 0.32, 0.28, fur),
                                    (0.42, 2.55, 0.34, 0.26, belly), (-0.2, 2.9, 0.4, 0.22, belly),
                                    (0.05, 1.95, 0.24, 0.2, belly)):
            _blob(pen, col, bx, by, rx, ry, lo=0.5, hi=1.04, n=8)
        # strained seams
        for pts in (((-0.85, 1.4), (-0.6, 1.62), (-0.3, 1.55)), ((0.3, 2.75), (0.55, 2.82), (0.78, 2.7))):
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                for i in range(5):
                    t = i / 5
                    px, py = lerp(ax, bx, t), lerp(ay, by, t)
                    pen.line((40, 22, 14), (px - 0.03, py - 0.05), (px + 0.03, py + 0.05), 0.022)
        # dark seepage at the split seam and at the collar
        for sx, sy, ln in ((0.66, 2.78, 0.35), (-0.1, 1.1, 0.25), (0.3, 1.15, 0.4)):
            pen.thick((52, 10, 10), (sx, sy), (sx + 0.02, sy + ln), 0.05)
            pen.circle((52, 10, 10), sx + 0.02, sy + ln, 0.04)
    if not human:
        # draw the eyeballs ourselves, into the (rotated) head's sockets
        a = math.radians(tilt)
        ca, sa = math.cos(a), math.sin(a)
        rng = random.Random(5)
        for sx in (-1, 1):
            ex, ey = sx * 0.38, -0.2 + 0.02
            px, py = ex * ca - ey * sa, ex * sa + ey * ca
            _draw_human_eye(pen, px, py, 0.16, rng, look=(-0.2, 0.2))
    surf = pygame.transform.smoothscale(big, (w, h))
    return surf, (-x0 * scale, -y0 * scale)


def _backroom(pen, rng):
    """Parts & Service: grubby wall, shelves of spare heads, checker floor (pen unit = 1 px)."""
    wall, low = (68, 60, 70), (42, 36, 44)
    horizon = 500
    pen.vgrad(0, 0, W, horizon, shade(wall, 0.8), wall)
    pen.rect(low, 0, 360, W, horizon - 360)
    pen.rect(shade(low, 0.6), 0, 352, W, 9)
    # drips and stains on the wall
    for _ in range(40):
        x = rng.uniform(0, W)
        y0 = rng.uniform(0, 300)
        ln = rng.uniform(30, 220)
        pen.rect(shade(wall, rng.uniform(0.72, 0.9)), x, y0, rng.uniform(1.5, 5), ln)
    # pipes along the ceiling
    pen.rect((40, 38, 40), 0, 18, W, 14)
    pen.rect((80, 76, 78), 0, 20, W, 4)
    pen.rect((36, 34, 36), 0, 46, W, 8)
    # checker floor in perspective
    vx, vy = W * 0.46, 250
    for row in range(14):
        z0, z1 = 1.0 + row * 0.55, 1.0 + (row + 1) * 0.55
        ya = horizon + (H + 140 - horizon) / z1 * 0.9
        yb = horizon + (H + 140 - horizon) / z0 * 0.9
        ya, yb = min(ya, H + 200), min(yb, H + 200)
        for col in range(-16, 17):
            xa0 = vx + (col * 120) / z1 * 1.4
            xa1 = vx + ((col + 1) * 120) / z1 * 1.4
            xb0 = vx + (col * 120) / z0 * 1.4
            xb1 = vx + ((col + 1) * 120) / z0 * 1.4
            c = (176, 172, 160) if (row + col) % 2 else (22, 22, 24)
            pen.poly(c, [(xa0, ya), (xa1, ya), (xb1, yb), (xb0, yb)])
    pen.rect((20, 18, 22), 0, horizon - 4, W, 6)
    # shelves (left) with brackets
    for sy in (172, 312):
        pen.rect((86, 64, 44), 20, sy, 430, 12)
        pen.rect((52, 38, 26), 20, sy + 12, 430, 6)
        for bx in (50, 230, 410):
            pen.poly((44, 34, 26), [(bx, sy + 18), (bx + 8, sy + 18), (bx + 8, sy + 60)])
    # boxes stacked on the right wall, a hanging cord
    for bx, by, bw, bh, c in ((930, 360, 150, 140, (110, 82, 54)), (1090, 330, 170, 170, (96, 72, 48)),
                              (960, 250, 120, 110, (120, 92, 60))):
        pen.rect(c, bx, by, bw, bh)
        pen.rect(shade(c, 0.7), bx, by, bw, 10)
        pen.rect(shade(c, 1.15), bx + bw * 0.42, by, bw * 0.16, bh)
    pen.line((20, 20, 20), (720, 0), (722, 92), 2)
    pen.poly((54, 58, 54), [(704, 92), (740, 92), (762, 124), (682, 124)])
    pen.ellipse((150, 140, 110), 722, 124, 38, 7)


def game_over():
    """The guard, stuffed into a Freddy suit backstage (opaque, SCREEN_W x SCREEN_H).

    The bottom-right corner is left dark for the "GAME OVER" caption.
    """
    rng = random.Random(1111)
    human = _human_eyes_supported()
    S = 2
    big = pygame.Surface((W * S, H * S))
    big.fill((30, 26, 32))
    pen = Pen(big, 0, 0, S)
    _backroom(pen, rng)
    # spare heads on the shelves
    heads = [("Bonnie", 92, 172), ("Endo", 200, 172), ("Chica", 322, 172), ("Freddy", 110, 312),
             ("Endo", 300, 312)]
    for name, hx, sy in heads:
        sc = 46
        spr = characters.render_character(name, sc * S, ss=1, body=False, eyes="none", prop=False)
        ax, ay = spr.anchor
        crop = pygame.Rect(0, 0, spr.surface.get_width(), int(min(spr.surface.get_height(), ay + 1.08 * sc * S)))
        img = spr.surface.subsurface(crop)
        big.blit(img, (int(hx * S - ax), int((sy - 1.08 * sc) * S - ay + 2 * S)))
    # an endo arm and a loose hand on the lower shelf
    pen.thick((120, 120, 128), (380, 300), (444, 286), 7)
    pen.circle((150, 150, 160), 380, 300, 7)
    room = pygame.transform.smoothscale(big, (W, H))
    _multiply_noise(room, rng, [(110, 160), (30, 190), (6, 215), (2, 225)])
    # the suit, slumped against the wall
    suit, (ax, ay) = _suit_sprite(122, human)
    hx, hy = 548, 238
    # contact shadow
    sh = pygame.Surface((560, 120), pygame.SRCALPHA)
    sh.fill((0, 0, 0, 0))
    pygame.draw.ellipse(sh, (0, 0, 0, 170), (40, 20, 480, 80))
    sh = _soften(sh, 0.15)
    room.blit(sh, (hx - 260, hy + 380))
    room.blit(suit, (int(hx - ax), int(hy - ay)))
    # light: one weak bulb over the suit, everything else falls into murk
    _light(room, (16, 15, 18), [(hx + 20, hy + 40, 520, 470, (196, 182, 160)),
                                (722, 140, 240, 160, (70, 64, 52)),
                                (200, 240, 300, 220, (46, 44, 48))])
    # murky, half-desaturated, greenish-brown tint
    lum = _luma(room)
    grey = _grey_surface(lum, (W, H))
    grey.set_alpha(120)
    room.blit(grey, (0, 0))
    room.fill((236, 230, 206), special_flags=pygame.BLEND_RGB_MULT)
    # vignette, deep in the bottom-right where the caption goes
    vig = make_light_mask(W // 4, H // 4, (30, 30, 30), [(W / 8 * 0.86, H / 8 * 0.86, W / 4 * 0.62, H / 4 * 0.75,
                                                          (230, 230, 230))])
    vig = pygame.transform.smoothscale(vig, (W, H))
    room.blit(vig, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    room.fill((6, 6, 7), special_flags=pygame.BLEND_RGB_ADD)
    _grain(room, rng, 200)
    return _finish(room)
