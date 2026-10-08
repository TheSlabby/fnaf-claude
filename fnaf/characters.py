"""Procedural animatronic art.

Every animatronic is drawn from pygame primitives through a ``util.Pen``.
Shapes are built as layered "blobs" (a dark outline, then concentric
layers that brighten towards an upper-left light), which reads as soft
shading once the art is supersampled and smoothscaled (``render_ss``).

Conventions (local units, drawn through a util.Pen):
  * origin (0, 0) is the centre of the head; 1 unit ~= head radius
  * +y is down; feet rest at about y = +6.0
  * ears / hat may extend up to y = -3.2 (Bonnie's ears are the tallest)

Public API
  NAMES
  bounds(name, body=True, pose="stand") -> (x0, y0, x1, y1)
  draw_character(pen, name, mouth=0.0, eyes="normal", look=(0, 0), body=True,
                 prop=True, pose="stand", step=0)
  render_character(name, scale, ss=2, **kw) -> util.Sprite
"""

import math
import random

import pygame

from .util import Pen, Sprite, clamp, font, lerp, lerp_color, render_ss, shade

NAMES = ["Freddy", "Bonnie", "Chica", "Foxy", "Golden", "Endo"]


# ==========================================================================
# A Pen that can also rotate
# ==========================================================================

class _XPen(Pen):
    """util.Pen with a full 2x2 transform (rotation + uniform scale + flip).

    ``m = (a, b, c, d)`` maps local (x, y) to pixels as
    (ox + a*x + b*y, oy + c*x + d*y). Child pens made with ``sub`` may add
    a rotation (degrees, clockwise on screen).
    """

    def __init__(self, surf, ox, oy, m, glows):
        self.surf = surf
        self.ox, self.oy = ox, oy
        self.m = m
        a, b, c, d = m
        det = a * d - b * c
        self.s = math.sqrt(abs(det)) or 1e-6
        self.fx = -1 if det < 0 else 1
        self.axis = abs(b) < 1e-9 and abs(c) < 1e-9
        self.glows = glows

    @classmethod
    def wrap(cls, pen):
        if isinstance(pen, cls):
            return pen
        return cls(pen.surf, pen.ox, pen.oy, (pen.s * pen.fx, 0.0, 0.0, pen.s), pen.glows)

    def sub(self, x, y, scale=1.0, flip=False, rot=0.0):
        ox, oy = self.pt(x, y)
        a, b, c, d = self.m
        r = math.radians(rot)
        cr, sr = math.cos(r), math.sin(r)
        f = -scale if flip else scale
        m = ((a * cr + b * sr) * f, (b * cr - a * sr) * scale,
             (c * cr + d * sr) * f, (d * cr - c * sr) * scale)
        return _XPen(self.surf, ox, oy, m, self.glows)

    def pt(self, x, y):
        a, b, c, d = self.m
        return (self.ox + a * x + b * y, self.oy + c * x + d * y)

    def pts(self, pts):
        a, b, c, d = self.m
        ox, oy = self.ox, self.oy
        return [(ox + a * x + b * y, oy + c * x + d * y) for x, y in pts]

    def _nseg(self, r):
        return int(clamp(math.sqrt(abs(r) * self.s) * 2.6, 10, 72))

    def ellipse(self, color, x, y, rx, ry, width=0):
        if self.axis:
            cx, cy = self.pt(x, y)
            w = abs(2 * rx * self.m[0])
            h = abs(2 * ry * self.m[3])
            r = pygame.Rect(0, 0, max(1, int(round(w))), max(1, int(round(h))))
            r.center = (int(round(cx)), int(round(cy)))
            pygame.draw.ellipse(self.surf, color, r, self._w(width))
        else:
            self.rellipse(color, x, y, rx, ry, 0, width)

    def rellipse(self, color, x, y, rx, ry, angle_deg, width=0, n=None):
        if n is None:
            n = self._nseg(max(abs(rx), abs(ry)))
        a = math.radians(angle_deg)
        ca, sa = math.cos(a), math.sin(a)
        pts = []
        for i in range(n):
            t = 2 * math.pi * i / n
            ex, ey = rx * math.cos(t), ry * math.sin(t)
            pts.append((x + ex * ca - ey * sa, y + ex * sa + ey * ca))
        if width:
            self.lines(color, pts, width, closed=True)
        else:
            self.poly(color, pts)

    def rect(self, color, x, y, w, h, radius=0, width=0):
        if self.axis:
            return Pen.rect(self, color, x, y, w, h, radius, width)
        self.poly(color, _rrect(x, y, w, h, radius), width)

    def arc(self, color, x, y, rx, ry, start_deg, stop_deg, width=0.05):
        n = max(3, int(abs(stop_deg - start_deg) / 360.0 * self._nseg(max(rx, ry))) + 2)
        pts = []
        for i in range(n):
            a = math.radians(lerp(start_deg, stop_deg, i / (n - 1)))
            pts.append((x + rx * math.cos(a), y - ry * math.sin(a)))
        _stroke(self, color, pts, width)

    def vgrad(self, x, y, w, h, top, bottom):
        if self.axis:
            return Pen.vgrad(self, x, y, w, h, top, bottom)
        n = int(clamp(h * self.s / 3, 2, 64))
        for i in range(n):
            c = lerp_color(top, bottom, i / max(1, n - 1))
            y0 = y + h * i / n
            self.poly(c, [(x, y0), (x + w, y0), (x + w, y0 + h / n * 1.05), (x, y0 + h / n * 1.05)])

    def hgrad(self, x, y, w, h, left, right):
        if self.axis:
            return Pen.hgrad(self, x, y, w, h, left, right)
        n = int(clamp(w * self.s / 3, 2, 64))
        for i in range(n):
            c = lerp_color(left, right, i / max(1, n - 1))
            x0 = x + w * i / n
            self.poly(c, [(x0, y), (x0 + w / n * 1.05, y), (x0 + w / n * 1.05, y + h), (x0, y + h)])

    def text(self, string, x, y, size, color, anchor="center", bold=True, mono=False):
        if self.axis:
            return Pen.text(self, string, x, y, size, color, anchor, bold, mono)
        px_size = max(6, int(round(size * self.s)))
        img = font(px_size, bold=bold, mono=mono).render(string, True, color)
        a, _, c, _ = self.m
        ang = math.degrees(math.atan2(c, a if self.fx > 0 else -a))
        img = pygame.transform.rotozoom(img, -ang, 1.0)
        cx, cy = self.pt(x, y)
        r = img.get_rect(center=(int(round(cx)), int(round(cy))))
        self.surf.blit(img, r)
        return r


def _rrect(x, y, w, h, r, n=4):
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    pts = []
    for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0),
                       (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for i in range(n + 1):
            a = math.radians(a0 + 90 * i / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


# ==========================================================================
# Low-level drawing helpers
# ==========================================================================

LIGHT = (-0.38, -0.55)       # direction of the key light in each shape's frame
SOCKET = (7, 5, 6)
TEETH = (236, 228, 204)
METAL = (150, 152, 160)
INK = (14, 10, 10)


def _rng(*key):
    return random.Random("|".join(str(k) for k in key))


def _col(base, k):
    """``k`` may be a brightness factor or an explicit colour."""
    if isinstance(k, (tuple, list)):
        return tuple(k[:3])
    return shade(base, k)


def _nl(p, r, k=1.0, lo=2, hi=14):
    """Number of shading layers for a shape of local radius r."""
    return int(clamp(r * p.s * k / 8.0, lo, hi))


def _dot(surf, c, x, y, r):
    pygame.draw.circle(surf, c, (int(round(x)), int(round(y))), max(1, int(round(r))))


def _stroke(p, c, pts, w, closed=False):
    """Polyline with round joins; ``w`` is a width or a list of widths."""
    if len(pts) < 1:
        return
    ws = list(w) if isinstance(w, (list, tuple)) else [w] * len(pts)
    if closed:
        pts = list(pts) + [pts[0]]
        ws = ws + [ws[0]]
    P = p.pts(pts)
    W = [max(0.5, x * p.s / 2) for x in ws]
    surf = p.surf
    for i in range(len(P) - 1):
        (x1, y1), (x2, y2) = P[i], P[i + 1]
        dx, dy = x2 - x1, y2 - y1
        d = math.hypot(dx, dy)
        if d < 1e-6:
            continue
        nx, ny = -dy / d, dx / d
        r1, r2 = W[i], W[i + 1]
        pygame.draw.polygon(surf, c, [(x1 + nx * r1, y1 + ny * r1), (x2 + nx * r2, y2 + ny * r2),
                                      (x2 - nx * r2, y2 - ny * r2), (x1 - nx * r1, y1 - ny * r1)])
    for (x, y), r in zip(P, W):
        if r >= 1.0:
            _dot(surf, c, x, y, r)


def _ell(p, c, q, grow=0.0):
    a = q[4] if len(q) > 4 else 0
    if a:
        p.rellipse(c, q[0], q[1], q[2] + grow, q[3] + grow, a)
    else:
        p.ellipse(c, q[0], q[1], q[2] + grow, q[3] + grow)


def _blob(p, col, parts, lo=0.6, hi=1.1, ol=0.04, olc=None, light=LIGHT, shrink=0.6, n=None, gamma=1.0):
    """Shaded union of ellipses ``parts = [(x, y, rx, ry[, angle]), ...]``.

    Layers are interleaved across parts so overlapping parts merge into one
    smoothly shaded volume.
    """
    if olc is None:
        olc = shade(col, 0.28)
    if ol:
        for q in parts:
            _ell(p, olc, q, ol)
    if n is None:
        n = _nl(p, max(max(q[2], q[3]) for q in parts))
    c0, c1 = _col(col, lo), _col(col, hi)
    lx, ly = light
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.6
        c = lerp_color(c0, c1, t ** gamma)
        k = shrink * t
        for q in parts:
            x, y, rx, ry = q[0], q[1], q[2], q[3]
            a = q[4] if len(q) > 4 else 0
            if a:
                ar = math.radians(a)
                ca, sa = math.cos(ar), math.sin(ar)
                fx, fy = lx * ca + ly * sa, -lx * sa + ly * ca
                ox, oy = fx * rx * k, fy * ry * k
                p.rellipse(c, x + ox * ca - oy * sa, y + ox * sa + oy * ca, rx * (1 - k), ry * (1 - k), a)
            else:
                p.ellipse(c, x + lx * rx * k, y + ly * ry * k, rx * (1 - k), ry * (1 - k))


def _tube(p, col, pts, w, lo=0.6, hi=1.08, ol=0.04, olc=None, light=LIGHT, shrink=0.6, n=None):
    """Shaded capsule chain through ``pts`` (width or list of widths)."""
    if olc is None:
        olc = shade(col, 0.28)
    ws = list(w) if isinstance(w, (list, tuple)) else [w] * len(pts)
    if ol:
        _stroke(p, olc, pts, [x + 2 * ol for x in ws])
    if n is None:
        n = _nl(p, max(ws) / 2)
    c0, c1 = _col(col, lo), _col(col, hi)
    lx, ly = light
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.6
        k = shrink * t
        c = lerp_color(c0, c1, t)
        _stroke(p, c, [(x + lx * k * wi / 2, y + ly * k * wi / 2) for (x, y), wi in zip(pts, ws)],
                [wi * (1 - k) for wi in ws])


def _slab(p, col, pts, lo=0.65, hi=1.08, ol=0.035, olc=None, light=LIGHT, shrink=0.55, n=None):
    """Shaded (roughly convex) polygon."""
    if olc is None:
        olc = shade(col, 0.28)
    if ol:
        p.poly(olc, pts)
        _stroke(p, olc, pts, 2 * ol, closed=True)
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    hw, hh = (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2
    if n is None:
        n = _nl(p, max(hw, hh), 0.8)
    c0, c1 = _col(col, lo), _col(col, hi)
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.6
        k = shrink * t
        ccx, ccy = cx + light[0] * hw * k, cy + light[1] * hh * k
        p.poly(lerp_color(c0, c1, t), [(ccx + (x - cx) * (1 - k), ccy + (y - cy) * (1 - k)) for x, y in pts])


def _clip(poly, f):
    """Keep the part of polygon ``poly`` where f(x, y) <= 0."""
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        fa, fb = f(*a), f(*b)
        if fa <= 0:
            out.append(a)
        if (fa <= 0) != (fb <= 0):
            t = fa / (fa - fb)
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _epts(x, y, rx, ry, n=36, a=0.0):
    ar = math.radians(a)
    ca, sa = math.cos(ar), math.sin(ar)
    out = []
    for i in range(n):
        t = 2 * math.pi * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((x + ex * ca - ey * sa, y + ex * sa + ey * ca))
    return out


def _grime(p, rng, col, region, count, rmin, rmax, k=(0.74, 0.88), tint=(46, 34, 24), mix=0.25):
    """Darker stains scattered inside the ellipse ``region``."""
    x0, y0, rx, ry = region
    for _ in range(count):
        a = rng.random() * 2 * math.pi
        d = math.sqrt(rng.random())
        x, y = x0 + math.cos(a) * d * rx, y0 + math.sin(a) * d * ry
        r = lerp(rmin, rmax, rng.random())
        c = lerp_color(shade(col, lerp(k[0], k[1], rng.random())), tint, mix)
        ang = rng.random() * 180
        asp = 0.45 + 0.5 * rng.random()
        p.rellipse(c, x, y, r, r * asp, ang)
        if rng.random() < 0.6:
            p.rellipse(shade(c, 0.85), x + r * 0.15, y + r * 0.1, r * 0.55, r * asp * 0.5, ang)


def _stitches(p, pts, col, w=0.025, dash=0.07, gap=0.06):
    """Dashed line (seams / stitches) along a polyline."""
    acc = 0.0
    on = True
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        d = math.hypot(x2 - x1, y2 - y1)
        t = 0.0
        while t < d:
            seg = (dash if on else gap) - acc
            t2 = min(d, t + seg)
            if on:
                a = (x1 + (x2 - x1) * t / d, y1 + (y2 - y1) * t / d)
                b = (x1 + (x2 - x1) * t2 / d, y1 + (y2 - y1) * t2 / d)
                p.line(col, a, b, w)
            if t2 - t >= seg - 1e-9:
                on = not on
                acc = 0.0
            else:
                acc += t2 - t
            t = t2


def _arcpts(x, y, rx, ry, a0, a1, n=12):
    """Points on an ellipse arc; angles in degrees, 0 = +x, 90 = down (+y)."""
    out = []
    for i in range(n):
        a = math.radians(lerp(a0, a1, i / (n - 1)))
        out.append((x + rx * math.cos(a), y + ry * math.sin(a)))
    return out


# ==========================================================================
# Face parts
# ==========================================================================

def _teeth(p, cx, hw, y0, length, n, down, rng, col=TEETH, sharp=False, curve=0.0, jag=0.35,
           ol=0.016, metal=False):
    """A row of teeth; roots along y0 (+curve*u^2), pointing down or up."""
    w = 2 * hw / n
    d = 1 if down else -1
    olc = (34, 26, 24) if not metal else (30, 30, 36)
    for i in range(n):
        x = cx - hw + w * (i + 0.5)
        u = (x - cx) / hw
        yr = y0 + curve * u * u
        L = length * (1 - 0.28 * u * u) * (1 - jag / 2 + jag * rng.random())
        tw = w * 0.98
        yt = yr + d * L
        xl, xr = x - tw / 2, x + tw / 2
        if sharp:
            pts = [(xl, yr), (xr, yr), (xr - tw * 0.12, yr + d * L * 0.45), (x + tw * 0.05, yt),
                   (x - tw * 0.05, yt), (xl + tw * 0.12, yr + d * L * 0.45)]
        else:
            rr = min(tw * 0.38, L * 0.45)
            pts = [(xl, yr), (xl, yt - d * rr), (xl + rr * 0.3, yt - d * rr * 0.3), (xl + rr, yt),
                   (xr - rr, yt), (xr - rr * 0.3, yt - d * rr * 0.3), (xr, yt - d * rr), (xr, yr)]
        if metal:
            tc = lerp_color(col, (120, 122, 130), rng.random() * 0.4)
        else:
            tc = lerp_color(col, (190, 160, 104), rng.random() ** 2 * 0.6)
        _slab(p, tc, pts, lo=0.5, hi=1.04, ol=ol, olc=olc, light=(-0.25, d * 0.7), shrink=0.5,
              n=_nl(p, tw / 2, 1.0, 2, 5))


def _eye(p, x, y, rx, ry, iris, mode, look, lid=0.0, tilt=0.0, lidcol=None, mouth=0.0,
         iris_k=0.6, sock_k=1.2, sx=1):
    """Eye socket + eyeball (+ upper eyelid)."""
    srx, sry = rx * sock_k, ry * sock_k
    p.ellipse(SOCKET, x, y, srx, sry)
    lx, ly = clamp(look[0], -1, 1), clamp(look[1], -1, 1)
    if mode in ("normal", "glow"):
        _blob(p, (240, 236, 226), [(x, y, rx, ry)], lo=0.42, hi=1.0, ol=0, light=(-0.15, -0.7), shrink=0.45)
        ir = min(rx, ry) * iris_k
        ix = x + lx * (rx - ir) * 0.85
        iy = y + ly * (ry - ir) * 0.75 + ry * 0.04
        p.circle(shade(iris, 0.25), ix, iy, ir * 1.08)
        _blob(p, iris, [(ix, iy, ir, ir)], lo=0.45, hi=1.4, ol=0, light=(0.1, 0.65), shrink=0.55)
        pr = ir * (0.48 - 0.2 * mouth)
        p.circle((4, 3, 6), ix, iy, pr)
        p.circle((255, 255, 255), ix - ir * 0.36, iy - ir * 0.38, ir * 0.22)
        p.circle((230, 230, 230), ix + ir * 0.34, iy + ir * 0.32, ir * 0.08)
        if mode == "glow":
            p.glow(ix, iy, ir * 3.6, iris)
            p.glow(ix, iy, ir * 1.6, lerp_color(iris, (255, 255, 255), 0.5))
    elif mode == "pinpoint":
        px_, py_ = x + lx * rx * 0.35, y + ly * ry * 0.3
        p.circle((250, 250, 255), px_, py_, max(0.035, 1.4 / p.s))
        p.glow(px_, py_, 0.3, (215, 215, 235))
    else:  # none: empty socket with a faint inner rim
        p.ellipse((18, 14, 14), x, y + ry * 0.12, rx * 0.95, ry * 0.85)
        p.ellipse((2, 1, 2), x, y + ry * 0.05, rx * 0.88, ry * 0.8)
    if lid > 0 and lidcol is not None:
        _lid(p, x, y, srx, sry, lid, tilt * sx, lidcol)


def _lid(p, x, y, rx, ry, lid, tilt, col):
    yc = y - ry + 2 * ry * lid
    bulge = ry * 0.22

    def edge(X):
        u = (X - x) / rx
        return yc + tilt * (X - x) + bulge * (1 - u * u)

    pts = _epts(x, y, rx * 1.02, ry * 1.04, 40)
    shadow = _clip(pts, lambda X, Y: Y - edge(X) - ry * 0.16)
    if len(shadow) >= 3:
        p.poly((24, 16, 16), shadow)
    poly = _clip(pts, lambda X, Y: Y - edge(X))
    if len(poly) < 3:
        return
    _slab(p, col, poly, lo=0.62, hi=1.05, ol=0, light=(-0.3, -0.6), shrink=0.45)
    line = []
    for i in range(25):
        X = x - rx + 2 * rx * i / 24
        Y = edge(X)
        if ((X - x) / (rx * 1.02)) ** 2 + ((Y - y) / (ry * 1.04)) ** 2 <= 1.0:
            line.append((X, Y))
    if len(line) >= 2:
        _stroke(p, shade(col, 0.22), line, ry * 0.16)
        _stroke(p, shade(col, 1.15), [(X, Y - ry * 0.12) for X, Y in line[2:-2]], ry * 0.06)


def _brow(p, pts, w, col):
    _tube(p, col, pts, [w * 0.75] + [w] * (len(pts) - 2) + [w * 0.6], lo=(16, 12, 12) if sum(col) < 120 else 0.6,
          hi=lerp_color(col, (110, 100, 100), 0.35) if sum(col) < 120 else 1.15, ol=0.02,
          olc=(6, 4, 4), shrink=0.5)


def _nose(p, x, y, rx, ry, col):
    _blob(p, col, [(x, y, rx, ry)], lo=(8, 6, 6) if sum(col) < 150 else 0.55,
          hi=lerp_color(col, (150, 140, 140), 0.35) if sum(col) < 150 else 1.2, ol=0.025, olc=(6, 4, 4))
    p.rellipse(lerp_color(col, (255, 255, 255), 0.6), x - rx * 0.35, y - ry * 0.4, rx * 0.28, ry * 0.18, -12)


def _maw(p, ytop, ybot, hwt, hwb, drop, rng):
    """Dark mouth cavity; when open, the endoskeleton's metal jaw shows inside."""
    h = ybot - ytop
    rb = min(0.16, h * 0.45)
    pts = [(-hwt, ytop), (hwt, ytop)]
    pts += _arcpts(hwb - rb, ybot - rb, rb, rb, 0, 90, 5)
    pts += _arcpts(-hwb + rb, ybot - rb, rb, rb, 90, 180, 5)
    p.poly((14, 6, 6), pts)
    if drop < 0.08:
        return
    cy = ytop + h * 0.58
    mw = min(hwt, hwb)
    for i, k in enumerate((0.95, 0.8, 0.64, 0.48, 0.34)):
        c = lerp_color((44, 14, 13), (1, 0, 0), i / 4)
        p.ellipse(c, 0, cy, mw * k, h * 0.5 * k)
    dark_metal = (96, 96, 104)
    # pistons in the corners of the jaw
    for sx in (-1, 1):
        a = (sx * hwt * 0.84, ytop + 0.03)
        b = (sx * hwb * 0.8, ytop + h * 0.62)
        _tube(p, dark_metal, [a, b], 0.045, lo=0.35, hi=0.85, ol=0.01)
        _tube(p, (60, 60, 66), [a, (lerp(a[0], b[0], 0.4), lerp(a[1], b[1], 0.4))], 0.075, lo=0.35, hi=0.8, ol=0.01)
        p.circle((24, 24, 28), b[0], b[1], 0.035)
    # the endo's own jaws, deeper inside the head
    whu, whl = hwt * 0.6, hwb * 0.58
    ut = ytop + 0.17 + 0.1 * drop
    _slab(p, dark_metal, [(-whu, ytop), (whu, ytop), (whu * 0.96, ut - 0.08), (-whu * 0.96, ut - 0.08)],
          lo=0.35, hi=0.85, ol=0.012)
    _teeth(p, 0, whu * 0.9, ut - 0.09, 0.08 + 0.04 * drop, 8, True, rng, col=(178, 178, 186), metal=True)
    lt = ybot - 0.16 - 0.12 * drop
    _slab(p, dark_metal, [(-whl * 0.96, lt + 0.08), (whl * 0.96, lt + 0.08), (whl, ybot), (-whl, ybot)],
          lo=0.35, hi=0.85, ol=0.012)
    _teeth(p, 0, whl * 0.9, lt + 0.09, 0.07 + 0.04 * drop, 8, False, rng, col=(170, 170, 178), metal=True)
    # a few hanging wires
    for wx, col in ((-whu * 0.55, (110, 22, 22)), (whu * 0.35, (30, 30, 36)), (whu * 0.7, (34, 48, 96))):
        sag = h * (0.22 + 0.15 * rng.random())
        _stroke(p, col, [(wx, ytop + 0.02), (wx + 0.015, ytop + sag * 0.5), (wx + 0.06, ytop + sag * 0.85),
                         (wx + 0.11, ytop + sag)], 0.016)


def _jaw(p, ym, hw, drop, col, jw=1.0, Ll=0.19, chin=0.3, side=0.13, lo=0.5, hi=1.05, occl=None):
    """The costume's lower jaw: a U-shaped piece hanging from hinges in the cheeks.

    ``occl = (cy, rx, ry)`` is the head ellipse whose lower edge casts a
    shadow onto the jaw.
    """
    hwj = hw * jw
    yc = ym + drop + Ll
    top = ym - 0.24
    pts = [(-hw - side, top), (hw + side, top), (hwj + side, yc)]
    pts += _arcpts(0, yc, hwj + side, chin, 0, 180, 16)[1:-1]
    pts += [(-hwj - side, yc)]
    _slab(p, col, pts, lo=lo, hi=hi, light=(-0.3, 0.35), shrink=0.55)
    if occl is not None:
        cy, rx, ry = occl

        def below(x, y):
            u = min(1.0, abs(x) / rx)
            return y - (cy + ry * math.sqrt(1 - u * u) + 0.09)
        sh = _clip(pts, below)
        if len(sh) >= 3:
            p.poly(shade(col, 0.42), sh)
            sh2 = _clip(pts, lambda x, y: below(x, y) + 0.045)
            if len(sh2) >= 3:
                p.poly(shade(col, 0.3), sh2)
    return yc


def _mouth(p, key, ym, hw, drop, jaw_col, jw=1.0, upper=True, sharp=False, Lu=0.2, Ll=0.19, nu=9, nl=8,
           chin=0.3, tcol=TEETH, jaw=True):
    """Lower jaw, mouth cavity (with endo inside) and teeth rows.

    ``ym`` is where the upper teeth tips meet the lower ones when closed.
    """
    hwj = hw * jw
    yc = ym + drop + Ll
    if jaw:
        _jaw(p, ym, hw, drop, jaw_col, jw, Ll, chin)
    _maw(p, ym - 0.17, yc + 0.01, hw * 0.9, hwj * 0.88, drop, _rng(key, "maw"))
    _teeth(p, 0, hwj * 0.86, yc + 0.035, Ll + 0.035, nl, False, _rng(key, "lt"), col=tcol, sharp=sharp, curve=0.03)
    if upper:
        _teeth(p, 0, hw * 0.9, ym - Lu, Lu, nu, True, _rng(key, "ut"), col=tcol, sharp=sharp, curve=-0.03)
    return yc


def _round_ear(p, x, y, r, fur, inner, rot=0.0):
    _blob(p, fur, [(x, y, r, r * 0.96)])
    _blob(p, inner, [(x + 0.01, y + 0.02, r * 0.56, r * 0.54)], lo=0.45, hi=0.95, ol=0.02,
          light=(0.3, 0.5))


def _top_hat(p, x, y, col=(32, 28, 32)):
    """Small black top hat whose brim sits at y."""
    dark, light = (10, 9, 11), (92, 86, 96)
    _blob(p, col, [(x, y + 0.02, 0.44, 0.09)], lo=dark, hi=light, ol=0.03, olc=(4, 3, 4))
    crown = [(x - 0.27, y + 0.02), (x - 0.25, y - 0.52), (x + 0.25, y - 0.52), (x + 0.27, y + 0.02)]
    _slab(p, col, crown, lo=dark, hi=light, ol=0.03, olc=(4, 3, 4), light=(-0.7, -0.15), shrink=0.6)
    p.poly((58, 26, 30), [(x - 0.267, y - 0.03), (x + 0.267, y - 0.03), (x + 0.262, y - 0.13),
                          (x - 0.262, y - 0.13)])
    _blob(p, col, [(x, y - 0.52, 0.25, 0.055)], lo=(30, 28, 32), hi=(84, 80, 88), ol=0.022, olc=(4, 3, 4))


def _bowtie(p, x, y, s, col):
    olc = (6, 5, 6) if sum(col) < 150 else shade(col, 0.3)
    lo = (14, 12, 14) if sum(col) < 150 else 0.55
    hi = (90, 86, 92) if sum(col) < 150 else 1.15
    for sx in (-1, 1):
        pts = [(x + sx * 0.06 * s, y - 0.07 * s), (x + sx * 0.4 * s, y - 0.22 * s),
               (x + sx * 0.47 * s, y - 0.1 * s), (x + sx * 0.47 * s, y + 0.1 * s),
               (x + sx * 0.4 * s, y + 0.22 * s), (x + sx * 0.06 * s, y + 0.07 * s)]
        _slab(p, col, pts, lo=lo, hi=hi, ol=0.03, olc=olc, light=(-0.3 * sx, -0.5))
        _stroke(p, _col(col, lo), [(x + sx * 0.12 * s, y - 0.02 * s), (x + sx * 0.32 * s, y - 0.1 * s)], 0.03 * s)
        _stroke(p, _col(col, lo), [(x + sx * 0.12 * s, y + 0.03 * s), (x + sx * 0.32 * s, y + 0.12 * s)], 0.03 * s)
    _blob(p, col, [(x, y, 0.11 * s, 0.13 * s)], lo=lo, hi=hi, ol=0.03, olc=olc)


# ==========================================================================
# Heads
# ==========================================================================

def _head_bear(p, pal, mouth, eyes, look, name):
    """Freddy / Golden Freddy."""
    rng = _rng(name, "head")
    fur, muz = pal["fur"], pal["muz"]
    drop = 0.8 * mouth
    jw = 1 + 0.4 * mouth
    for sx in (-1, 1):
        _round_ear(p, sx * 0.8, -0.78, 0.32, fur, pal["ear"])
    jaw = shade(muz, 0.88)
    _jaw(p, 0.64, 0.46, drop, jaw, jw, 0.17, 0.34 + 0.1 * mouth, occl=(0.28, 1.05, 0.58))
    _blob(p, fur, [(0, -0.2, 0.96, 0.78), (0, 0.28, 1.05, 0.58)])
    _grime(p, rng, fur, (0, -0.25, 0.62, 0.38), pal.get("grime", 4), 0.025, 0.07)
    _top_hat(p, 0.02, -0.86)
    _mouth(p, name, 0.64, 0.46, drop, jaw, jw, Lu=0.21, Ll=0.17, nl=10, jaw=False)
    _blob(p, muz, [(-0.25, 0.29, 0.39, 0.25), (0.25, 0.29, 0.39, 0.25), (0, 0.12, 0.3, 0.22)], lo=0.62, hi=1.08)
    _grime(p, _rng(name, "muz"), muz, (0, 0.33, 0.45, 0.13), 3, 0.02, 0.045, k=(0.78, 0.9))
    _nose(p, 0, 0.07, 0.17, 0.105, pal["nose"])
    lid = pal["lid"] * (1 - 0.55 * mouth)
    for sx in (-1, 1):
        _eye(p, sx * 0.38, -0.2, 0.2, 0.18, pal["iris"], eyes, look, lid=lid, tilt=-0.18, lidcol=fur,
             mouth=mouth, sx=sx)
    rise = -0.05 * mouth
    for sx in (-1, 1):
        _brow(p, [(sx * 0.16, -0.43 + rise), (sx * 0.38, -0.52 + rise), (sx * 0.6, -0.5 + rise)], 0.1, pal["brow"])


def _head_bonnie(p, pal, mouth, eyes, look, name="Bonnie"):
    rng = _rng(name, "head")
    fur, muz = pal["fur"], pal["muz"]
    drop = 0.8 * mouth
    jw = 1 + 0.4 * mouth
    for sx in (-1, 1):
        ear = [(sx * 0.34, -0.55), (sx * 0.42, -1.5), (sx * 0.48, -2.4), (sx * 0.47, -2.84)]
        _tube(p, fur, ear, [0.44, 0.52, 0.48, 0.3], lo=0.55, hi=1.1)
        inner = [(sx * 0.36, -0.95), (sx * 0.43, -1.6), (sx * 0.47, -2.35), (sx * 0.465, -2.66)]
        _tube(p, pal["ear"], inner, [0.16, 0.28, 0.25, 0.12], lo=0.6, hi=1.05, ol=0.02, light=(0.3, -0.3))
    _jaw(p, 0.6, 0.46, drop, fur, jw, 0.18, 0.34 + 0.1 * mouth, occl=(0.3, 1.0, 0.56))
    _blob(p, fur, [(0, -0.24, 0.86, 0.74), (0, 0.3, 1.0, 0.56)])
    _grime(p, rng, fur, (0, -0.25, 0.55, 0.38), 4, 0.025, 0.06)
    _mouth(p, name, 0.6, 0.46, drop, fur, jw, upper=False, Ll=0.18, nl=10, jaw=False)
    _blob(p, muz, [(-0.3, 0.33, 0.42, 0.3), (0.3, 0.33, 0.42, 0.3), (0, 0.12, 0.25, 0.2)], lo=0.62, hi=1.1)
    _grime(p, _rng(name, "muz"), muz, (0, 0.36, 0.5, 0.15), 3, 0.02, 0.045, k=(0.78, 0.9))
    _nose(p, 0, 0.1, 0.12, 0.08, pal["nose"])
    lid = pal["lid"] * (1 - 0.6 * mouth)
    for sx in (-1, 1):
        _eye(p, sx * 0.36, -0.22, 0.21, 0.2, pal["iris"], eyes, look, lid=lid, tilt=-0.1, lidcol=fur,
             mouth=mouth, sx=sx)
        _brow(p, [(sx * 0.18, -0.5), (sx * 0.38, -0.58), (sx * 0.56, -0.55)], 0.07, pal["brow"])


def _head_chica(p, pal, mouth, eyes, look, name="Chica"):
    rng = _rng(name, "head")
    fur, beak = pal["fur"], pal["beak"]
    drop = 0.07 + 0.74 * mouth
    jw = 1 + 0.38 * mouth
    _blob(p, fur, [(-0.16, -1.05, 0.08, 0.25, -28), (0.02, -1.12, 0.085, 0.28, 4), (0.18, -1.02, 0.07, 0.22, 34)],
          lo=0.6, hi=1.12)
    _jaw(p, 0.52, 0.44, drop, shade(beak, 0.9), jw, 0.17, 0.28 + 0.1 * mouth, occl=(0.3, 0.98, 0.6))
    _blob(p, fur, [(0, -0.12, 0.93, 0.88), (0, 0.3, 0.98, 0.6)])
    _grime(p, rng, fur, (0, -0.25, 0.55, 0.4), 4, 0.025, 0.06)
    _mouth(p, name, 0.52, 0.44, drop, shade(beak, 0.9), jw, Lu=0.2, Ll=0.17, nl=10, jaw=False)
    _blob(p, beak, [(0, 0.22, 0.6, 0.24), (0, 0.34, 0.36, 0.18)], lo=0.6, hi=1.12)
    for sx in (-1, 1):
        p.ellipse(shade(beak, 0.3), sx * 0.12, 0.13, 0.04, 0.025)
    lid = pal["lid"] * (1 - 0.6 * mouth)
    for sx in (-1, 1):
        _eye(p, sx * 0.38, -0.27, 0.23, 0.21, pal["iris"], eyes, look, lid=lid, tilt=0.15, lidcol=fur,
             mouth=mouth, sx=sx)
        _brow(p, [(sx * 0.14, -0.47), (sx * 0.36, -0.59), (sx * 0.62, -0.68)], 0.09, pal["brow"])


def _head_foxy(p, pal, mouth, eyes, look, name="Foxy"):
    rng = _rng(name, "head")
    fur, muz = pal["fur"], pal["muz"]
    drop = 0.24 + 0.74 * mouth
    jw = 1 + 0.36 * mouth
    # ears; his left ear (screen right) is torn and shows its metal frame
    _stroke(p, METAL, [(0.8, -1.76), (0.92, -0.52)], 0.04)
    _stroke(p, (90, 90, 98), [(0.84, -1.3), (0.62, -1.0)], 0.03)
    for sx in (-1, 1):
        if sx < 0:
            ear = [(-0.28, -0.6), (-0.78, -1.82), (-0.92, -0.5)]
        else:
            ear = [(0.28, -0.6), (0.78, -1.82), (0.8, -1.42), (0.68, -1.3), (0.76, -1.16), (0.66, -1.02),
                   (0.88, -0.86), (0.92, -0.5)]
        _slab(p, fur, ear, lo=0.55, hi=1.08)
        inner = [(sx * 0.42, -0.68), (sx * 0.74, -1.52), (sx * 0.8, -0.64)] if sx < 0 else \
            [(0.42, -0.68), (0.73, -1.5), (0.66, -1.06), (0.78, -0.64)]
        _slab(p, pal["ear"], inner, lo=0.45, hi=0.9, ol=0.02)
    yc = _jaw(p, 0.68, 0.3, drop, shade(muz, 0.9), jw, 0.19, 0.32 + 0.1 * mouth, occl=(0.15, 0.9, 0.5))
    _blob(p, fur, [(0, -0.2, 0.86, 0.72), (0, 0.15, 0.9, 0.5)])
    for sx in (-1, 1):
        tuft = [(sx * 0.76, -0.08), (sx * 1.1, 0.02), (sx * 0.92, 0.13), (sx * 1.14, 0.28), (sx * 0.9, 0.34),
                (sx * 1.04, 0.5), (sx * 0.7, 0.48)]
        _slab(p, fur, tuft, lo=0.5, hi=1.0)
    _grime(p, rng, fur, (0, -0.25, 0.55, 0.38), 6, 0.03, 0.08)
    _mouth(p, name, 0.68, 0.3, drop, muz, jw, sharp=True, Lu=0.21, Ll=0.19, nu=7, nl=6, jaw=False)
    # torn patch on the jaw showing metal
    tx, ty = 0.15 * jw, yc + 0.14
    _slab(p, (36, 30, 30), [(tx - 0.08, ty - 0.05), (tx + 0.1, ty - 0.07), (tx + 0.12, ty + 0.05),
                            (tx + 0.02, ty + 0.1), (tx - 0.1, ty + 0.06)], lo=1.0, hi=1.0, ol=0.014,
          olc=shade(muz, 1.1), n=1)
    _stroke(p, METAL, [(tx - 0.06, ty + 0.01), (tx + 0.09, ty - 0.01)], 0.035)
    _blob(p, muz, [(0, 0.3, 0.33, 0.26), (-0.17, 0.44, 0.19, 0.15), (0.17, 0.44, 0.19, 0.15)], lo=0.6, hi=1.08)
    _tube(p, fur, [(0, -0.3), (0, -0.05), (0, 0.2)], [0.42, 0.34, 0.26], lo=0.6, hi=1.1, ol=0.0)
    _nose(p, 0, 0.24, 0.14, 0.095, pal["nose"])
    lid = pal["lid"] * (1 - 0.6 * mouth)
    for sx in (-1, 1):
        _eye(p, sx * 0.37, -0.24, 0.21, 0.19, pal["iris"], eyes, look, lid=lid, tilt=0.12, lidcol=fur,
             mouth=mouth, sx=sx)
        _brow(p, [(sx * 0.15, -0.46), (sx * 0.37, -0.56), (sx * 0.6, -0.6)], 0.08, pal["brow"])
    # eyepatch flipped up over his right eye (screen left)
    strap = (24, 20, 20)
    _stroke(p, strap, [(-0.86, -0.4), (-0.62, -0.6)], 0.035)
    _stroke(p, strap, [(-0.24, -0.72), (0.2, -0.82), (0.58, -0.76), (0.85, -0.56)], 0.035)
    pp = p.sub(-0.42, -0.69, rot=-14)
    _slab(pp, (30, 26, 26), _rrect(-0.2, -0.12, 0.4, 0.24, 0.1), lo=(8, 7, 7), hi=(78, 72, 72), ol=0.02,
          olc=(2, 2, 2), light=(-0.3, -0.6))
    _stitches(pp, _rrect(-0.15, -0.075, 0.3, 0.15, 0.06) + [(0.0, -0.075)], (70, 66, 66), 0.012, 0.03, 0.025)


def _head_endo(p, pal, mouth, eyes, look, name="Endo"):
    rng = _rng(name, "head")
    m = pal["fur"]
    drop = 0.7 * mouth
    jw = 1 + 0.3 * mouth
    for sx in (-1, 1):
        _tube(p, m, [(sx * 0.55, -0.6), (sx * 0.72, -0.98)], 0.16, lo=0.45, hi=1.1)
        _blob(p, m, [(sx * 0.74, -1.02, 0.12, 0.08)], lo=0.5, hi=1.1, ol=0.02)
    # jaw hinges and the metal lower jaw (behind the skull)
    for sx in (-1, 1):
        _tube(p, shade(m, 0.8), [(sx * 0.62, 0.1), (sx * 0.56, 0.6 + drop)], 0.1, lo=0.45, hi=1.05)
        _tube(p, (70, 70, 78), [(sx * 0.68, 0.0), (sx * 0.62, 0.4 + drop * 0.5)], 0.06, lo=0.5, hi=1.0, ol=0.012)
    ym = 0.52
    _jaw(p, ym, 0.42, drop, m, jw, 0.17, 0.24, side=0.1, lo=0.42, hi=1.1)
    # skull
    _blob(p, m, [(0, -0.3, 0.82, 0.68), (0, 0.1, 0.74, 0.4)], lo=0.45, hi=1.15)
    _grime(p, rng, m, (0, -0.4, 0.5, 0.3), 5, 0.03, 0.07, tint=(60, 44, 30), mix=0.35)
    _stroke(p, shade(m, 0.45), [(-0.5, -0.82), (0, -0.92), (0.5, -0.82)], 0.025)
    _stroke(p, shade(m, 0.45), [(0, -0.97), (0, -0.62)], 0.025)
    for bx, by in ((-0.62, -0.5), (0.62, -0.5), (-0.3, -0.75), (0.3, -0.75)):
        p.circle(shade(m, 0.4), bx, by, 0.035)
        p.circle(shade(m, 1.2), bx - 0.01, by - 0.01, 0.018)
    _slab(p, m, [(-0.5, 0.16), (0.5, 0.16), (0.46, 0.36), (-0.46, 0.36)], lo=0.55, hi=1.1, ol=0.025)
    _mouth(p, name, ym, 0.42, drop, m, jw, Lu=0.19, Ll=0.17, nu=10, nl=9, tcol=(222, 220, 214), jaw=False)
    for sx in (-1, 1):
        p.circle((60, 60, 66), sx * 0.36 * jw, ym + drop + 0.32, 0.03)
    # nose plate
    _slab(p, m, [(-0.1, 0.0), (0.1, 0.0), (0.13, 0.17), (-0.13, 0.17)], lo=0.5, hi=1.15, ol=0.02)
    for sx in (-1, 1):
        p.ellipse((20, 16, 16), sx * 0.05, 0.12, 0.03, 0.025)
    for sx in (-1, 1):
        ex = sx * 0.37
        p.circle(shade(m, 0.3), ex, -0.15, 0.31)
        _blob(p, m, [(ex, -0.15, 0.29, 0.29)], lo=0.4, hi=1.0, ol=0, light=(0.3, 0.5), n=3)
        _eye(p, ex, -0.14, 0.2, 0.2, pal["iris"], eyes, look, mouth=mouth, sock_k=1.22, sx=sx)
        _tube(p, m, [(sx * 0.12, -0.5), (sx * 0.4, -0.56), (sx * 0.66, -0.46)], 0.09, lo=0.5, hi=1.2, ol=0.02)


# ==========================================================================
# Bodies
# ==========================================================================

def _hand(p, col, thumb=1, fist=False, fingers=True, metal_fingers=()):
    """A mitt hand in a frame where the wrist is the origin and +y points to the fingertips."""
    if fist:
        _blob(p, col, [(0, 0.22, 0.25, 0.24)])
        for i, fx in enumerate((-0.15, -0.05, 0.05, 0.15)):
            _blob(p, col, [(fx, 0.24, 0.07, 0.17)], lo=0.55, hi=1.0, ol=0.02)
        _tube(p, col, [(thumb * 0.2, 0.12), (thumb * 0.1, 0.3)], 0.11, ol=0.02)
        return
    _tube(p, col, [(thumb * 0.18, 0.16), (thumb * 0.3, 0.36)], 0.12, ol=0.025)
    _blob(p, col, [(0, 0.22, 0.25, 0.25)])
    if fingers:
        for i, fx in enumerate((-0.16, -0.055, 0.055, 0.16)):
            c = METAL if i in metal_fingers else col
            w = 0.07 if i in metal_fingers else 0.12
            _tube(p, c, [(fx, 0.34), (fx * 1.12, 0.6 - abs(fx) * 0.5)], w, ol=0.022, lo=0.55, hi=1.08)


def _limb_rot(a, b):
    return math.degrees(math.atan2(-(b[0] - a[0]), b[1] - a[1]))


def _arm(p, pal, sh, el, wr, w1=0.6, w2=0.52, hand="mitt", thumb=1, col=None, tears=False):
    col = col or pal["fur"]
    gap = shade(col, 0.2)
    # endo joints visible in the gaps
    _tube(p, (110, 110, 118), [sh, el, wr], 0.12, lo=0.45, hi=1.0, ol=0.015)
    p.circle(gap, el[0], el[1], w2 * 0.42)
    d1 = math.hypot(el[0] - sh[0], el[1] - sh[1]) or 1
    d2 = math.hypot(wr[0] - el[0], wr[1] - el[1]) or 1
    u1 = ((el[0] - sh[0]) / d1, (el[1] - sh[1]) / d1)
    u2 = ((wr[0] - el[0]) / d2, (wr[1] - el[1]) / d2)
    fa = [(el[0] + u2[0] * 0.08, el[1] + u2[1] * 0.08), (wr[0] - u2[0] * 0.1, wr[1] - u2[1] * 0.1)]
    ua = [sh, (el[0] - u1[0] * 0.05, el[1] - u1[1] * 0.05)]
    _tube(p, col, fa, [w2 * 1.02, w2 * 0.9])
    if tears:
        _tear(p, lerp(fa[0][0], fa[1][0], 0.5), lerp(fa[0][1], fa[1][1], 0.5), w2 * 0.32, 0.22, _rng("arm", sh))
    _tube(p, col, ua, [w1, w1 * 0.92])
    hp = p.sub(wr[0], wr[1], rot=_limb_rot(el, wr))
    if hand == "mitt":
        _hand(hp, pal.get("hand", col), thumb)
    elif hand == "fist":
        _hand(hp, pal.get("hand", col), thumb, fist=True)
    elif hand == "foxy":
        _hand(hp, pal.get("hand", col), thumb, metal_fingers=(1,))
    elif hand == "hook":
        _hook(hp, thumb)
    return hp


def _hook(p, side=1):
    _blob(p, (60, 44, 32), [(0, 0.04, 0.2, 0.11)], lo=0.5, hi=1.1)
    _tube(p, (80, 80, 88), [(0, 0.02), (0, 0.16)], 0.13, lo=0.5, hi=1.0, ol=0.02)
    pts = [(0, 0.12), (0, 0.32)]
    r = 0.17
    for i in range(1, 9):
        t = math.pi * i / 8 * 0.95
        pts.append((side * (r - r * math.cos(t)), 0.32 + r * 1.25 * math.sin(t)))
    ws = [0.09, 0.085, 0.08, 0.075, 0.07, 0.062, 0.054, 0.045, 0.036, 0.026, 0.014]
    _tube(p, (196, 200, 210), pts, ws[:len(pts)], lo=0.45, hi=1.3, ol=0.018, olc=(30, 30, 36))


def _leg(p, pal, hip, knee, ankle, w1=0.66, w2=0.56, foot=1.0, col=None, sx=1):
    col = col or pal["fur"]
    gap = shade(col, 0.2)
    _tube(p, (110, 110, 118), [hip, knee, ankle], 0.13, lo=0.45, hi=1.0, ol=0.015)
    p.circle(gap, knee[0], knee[1], w2 * 0.42)
    _tube(p, col, [(knee[0], knee[1] + 0.07), (ankle[0], ankle[1] - 0.08)], [w2, w2 * 0.9])
    _tube(p, col, [hip, (knee[0], knee[1] - 0.05)], [w1, w1 * 0.9])
    _foot(p, pal, ankle[0] + sx * 0.05, ankle[1] + 0.2, foot, col)


def _foot(p, pal, x, y, s, col):
    fc = pal.get("foot", col)
    _blob(p, fc, [(x, y, 0.46 * s, 0.24 * s)], lo=0.5, hi=1.05)
    for i in (-1, 0, 1):
        _blob(p, fc, [(x + i * 0.22 * s, y + 0.12 * s, 0.13 * s, 0.1 * s)], lo=0.5, hi=1.0, ol=0.02)


def _endo_leg(p, hip, knee, ankle, sx=1, foot=1.0, metal=METAL):
    _tube(p, metal, [hip, knee], 0.13, lo=0.45, hi=1.15)
    _tube(p, (80, 80, 88), [(hip[0] + sx * 0.09, hip[1] + 0.1), (knee[0] + sx * 0.09, knee[1] - 0.12)], 0.05,
          lo=0.5, hi=1.1, ol=0.012)
    _tube(p, metal, [knee, ankle], 0.12, lo=0.45, hi=1.15)
    _tube(p, (80, 80, 88), [(knee[0] - sx * 0.08, knee[1] + 0.12), (ankle[0] - sx * 0.08, ankle[1] - 0.12)], 0.05,
          lo=0.5, hi=1.1, ol=0.012)
    _blob(p, metal, [(knee[0], knee[1], 0.12, 0.12)], lo=0.4, hi=1.2, ol=0.02)
    _blob(p, metal, [(ankle[0], ankle[1], 0.09, 0.09)], lo=0.4, hi=1.2, ol=0.02)
    x, y = ankle[0] + sx * 0.04, ankle[1] + 0.22 * foot
    s = foot
    _slab(p, metal, [(x - 0.2 * s, y - 0.16 * s), (x + 0.2 * s, y - 0.16 * s), (x + 0.34 * s, y + 0.1 * s),
                     (x - 0.34 * s, y + 0.1 * s)], lo=0.45, hi=1.1)
    for i in (-1, 0, 1):
        _tube(p, metal, [(x + i * 0.2 * s, y + 0.05 * s), (x + i * 0.27 * s, y + 0.18 * s)], 0.08 * s,
              lo=0.45, hi=1.1, ol=0.015)


def _tear(p, x, y, rx, ry, rng, fray=None):
    """A ragged hole in the costume with endoskeleton parts inside."""
    pts = []
    n = 22
    for i in range(n):
        a = 2 * math.pi * (i + 0.3 * rng.random()) / n
        r = (0.66 + 0.2 * rng.random()) if i % 2 else (0.9 + 0.3 * rng.random())
        pts.append((x + math.cos(a) * rx * r, y + math.sin(a) * ry * r))
    if fray:
        p.poly(shade(fray, 0.45), [(x + (px - x) * 1.16, y + (py - y) * 1.16) for px, py in pts])
        p.poly(fray, [(x + (px - x) * 1.09, y + (py - y) * 1.09) for px, py in pts])
    p.poly((30, 22, 22), pts)
    p.poly((12, 9, 9), [(x + (px - x) * 0.72, y + ry * 0.08 + (py - y) * 0.72) for px, py in pts])
    w = min(rx, ry)
    _tube(p, (104, 104, 112), [(x - rx * 0.48, y - ry * 0.18), (x + rx * 0.48, y - ry * 0.26)], w * 0.2,
          lo=0.35, hi=0.95, ol=0.012)
    _tube(p, (90, 90, 98), [(x + rx * 0.16, y - ry * 0.5), (x + rx * 0.1, y + ry * 0.5)], w * 0.14,
          lo=0.35, hi=0.9, ol=0.012)
    _stroke(p, (120, 26, 24), [(x - rx * 0.3, y - ry * 0.05), (x - rx * 0.22, y + ry * 0.25),
                                (x - rx * 0.05, y + ry * 0.42)], max(0.018, w * 0.07))
    _stroke(p, (24, 24, 28), [(x - rx * 0.15, y - ry * 0.1), (x - rx * 0.05, y + ry * 0.2),
                               (x + rx * 0.32, y + ry * 0.3)], max(0.016, w * 0.06))


def _mic(p, a, b):
    _tube(p, (26, 24, 28), [a, b], 0.1, lo=(8, 8, 10), hi=(90, 88, 96), ol=0.02, olc=(2, 2, 2))
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
    g = (b[0] + ux * 0.1, b[1] + uy * 0.1)
    _blob(p, (40, 40, 44), [(g[0], g[1], 0.15, 0.15)], lo=(10, 10, 12), hi=(120, 120, 128), ol=0.02, olc=(2, 2, 2))
    for k in (-0.08, 0.0, 0.08):
        p.line((70, 70, 76), (g[0] - 0.12, g[1] + k), (g[0] + 0.12, g[1] + k), 0.012)
    p.circle((170, 170, 180), g[0] - 0.05, g[1] - 0.06, 0.03)


def _guitar(p):
    """Bonnie's red electric guitar, body centred at origin, neck towards -y."""
    red = (198, 30, 34)
    _slab(p, (70, 40, 24), [(-0.08, -0.25), (0.08, -0.25), (0.07, -2.3), (-0.07, -2.3)], lo=0.6, hi=1.1, ol=0.02)
    for i in range(1, 12):
        y = -0.4 - i * 0.17
        p.line((200, 196, 180), (-0.07, y), (0.07, y), 0.012)
    head = [(-0.11, -2.28), (0.11, -2.28), (0.15, -2.62), (0.02, -2.74), (-0.12, -2.66)]
    _slab(p, red, head, lo=0.55, hi=1.1, ol=0.025)
    for i in range(3):
        for sx in (-1, 1):
            p.circle((210, 210, 214), sx * 0.15, -2.36 - i * 0.12, 0.03)
    body = [(0, 0.12, 0.56, 0.44), (-0.3, -0.3, 0.2, 0.34, -18), (0.3, -0.28, 0.18, 0.3, 22), (0, -0.05, 0.42, 0.4)]
    _blob(p, red, body, lo=0.5, hi=1.2, ol=0.035)
    _blob(p, (236, 232, 222), [(0.12, 0.22, 0.26, 0.2, -20)], lo=0.75, hi=1.0, ol=0.012)
    for y in (-0.1, 0.12):
        _slab(p, (24, 22, 24), [(-0.13, y - 0.05), (0.13, y - 0.05), (0.13, y + 0.05), (-0.13, y + 0.05)],
              lo=(10, 10, 10), hi=(70, 70, 74), ol=0.012, n=2)
    p.rect((180, 180, 186), -0.14, 0.27, 0.28, 0.06)
    for kx, ky in ((0.3, 0.3), (0.38, 0.14), (0.22, 0.42)):
        p.circle((30, 30, 30), kx, ky, 0.045)
        p.circle((200, 200, 200), kx - 0.01, ky - 0.012, 0.015)
    for sx in (-0.045, -0.015, 0.015, 0.045):
        p.line((220, 220, 226), (sx, 0.3), (sx * 0.8, -2.3), 0.008)
    _grime(p, _rng("guitar"), red, (0, 0.1, 0.35, 0.25), 3, 0.02, 0.05)


def _cupcake(p):
    """Mr. Cupcake on a plate; plate centre at origin."""
    _blob(p, (226, 226, 232), [(0, 0.0, 0.5, 0.11)], lo=0.55, hi=1.1, ol=0.025)
    p.ellipse((190, 190, 198), 0, -0.01, 0.34, 0.06)
    wr = [(-0.3, -0.42), (0.3, -0.42), (0.24, -0.04), (-0.24, -0.04)]
    _slab(p, (240, 120, 170), wr, lo=0.6, hi=1.1, ol=0.025)
    for i in range(-2, 3):
        p.line((200, 80, 130), (i * 0.11, -0.4), (i * 0.085, -0.06), 0.018)
    _blob(p, (246, 150, 196), [(0, -0.52, 0.38, 0.2), (-0.16, -0.62, 0.18, 0.15), (0.14, -0.66, 0.2, 0.16),
                              (0, -0.74, 0.16, 0.14)], lo=0.6, hi=1.15, ol=0.025)
    # candle
    _slab(p, (230, 230, 250), [(-0.04, -1.12), (0.04, -1.12), (0.04, -0.82), (-0.04, -0.82)], lo=0.7, hi=1.05,
          ol=0.015)
    for k in range(3):
        p.line((90, 140, 230), (-0.04, -1.05 + k * 0.09), (0.04, -1.0 + k * 0.09), 0.018)
    _blob(p, (255, 170, 40), [(0, -1.2, 0.04, 0.075)], lo=0.8, hi=1.3, ol=0.01, olc=(160, 60, 10))
    p.ellipse((255, 250, 200), 0, -1.18, 0.018, 0.035)
    # eyes and grin
    for sx in (-1, 1):
        p.circle((20, 16, 16), sx * 0.12, -0.6, 0.075)
        p.circle((250, 250, 250), sx * 0.12, -0.6, 0.06)
        p.circle((20, 16, 16), sx * 0.12 + 0.01, -0.59, 0.03)
    p.poly((40, 10, 20), [(-0.16, -0.48), (0.16, -0.48), (0.08, -0.38), (-0.08, -0.38)])
    for i in range(4):
        x = -0.12 + i * 0.08
        p.poly((240, 236, 220), [(x - 0.035, -0.48), (x + 0.035, -0.48), (x, -0.42)])


def _bib(p, x, y):
    _blob(p, (238, 234, 222), [(x, y + 0.05, 0.86, 0.7), (x, y - 0.6, 0.52, 0.42)], lo=0.6, hi=1.02, ol=0.03,
          olc=(80, 76, 70))
    _grime(p, _rng("bib"), (238, 234, 222), (x, y + 0.1, 0.5, 0.4), 5, 0.02, 0.06, k=(0.8, 0.9))
    cols = [(226, 56, 60), (246, 146, 30), (60, 168, 70), (60, 120, 220), (150, 70, 200),
            (50, 140, 230), (232, 70, 140), (240, 180, 20), (60, 170, 90), (226, 56, 60)]
    ci = 0
    for row, word, yy in ((0, "LET'S", y - 0.2), (1, "EAT!!", y + 0.22)):
        size = 0.36
        f = font(max(6, int(round(size * p.s))), bold=True)
        widths = [f.size(ch)[0] / p.s for ch in word]
        tot = sum(widths)
        cx = x - tot / 2
        for ch, w in zip(word, widths):
            c = cols[ci % len(cols)]
            ci += 1
            jy = 0.03 * math.sin(ci * 2.1)
            p.text(ch, cx + w / 2 + 0.015, yy + jy + 0.02, size, shade(c, 0.35))
            p.text(ch, cx + w / 2, yy + jy, size, c)
            cx += w


# -- full bodies --------------------------------------------------------------

def _body_bear(p, name, pal, prop, pose):
    """Freddy, Golden, Bonnie and Chica share a costume rig."""
    fur = pal["fur"]
    rng = _rng(name, "body")
    wide = {"Chica": 1.06, "Bonnie": 0.95}.get(name, 1.0)
    # legs
    for sx in (-1, 1):
        _leg(p, pal, (sx * 0.56, 4.28), (sx * 0.62, 5.0), (sx * 0.64, 5.56), sx=sx)
    # neck
    _blob(p, shade(fur, 0.45), [(0, 1.2, 0.38, 0.28)], lo=0.4, hi=0.9)
    # torso
    _blob(p, fur, [(0, 4.15, 0.98 * wide, 0.46)], lo=0.5, hi=1.0)
    parts = [(0, 2.42, 1.28 * wide, 1.08), (0, 3.28, 1.2 * wide, 1.0)]
    for sx in (-1, 1):
        parts.append((sx * 1.0 * wide, 1.92, 0.48, 0.42))
    _blob(p, fur, parts, lo=0.55, hi=1.1)
    _grime(p, rng, fur, (0, 2.6, 0.95, 1.1), pal.get("grime", 5) + 4, 0.03, 0.09)
    if pal.get("belly"):
        bc = pal["belly"]
        _blob(p, bc, [(0, 3.08, 0.82 * wide, 0.98)], lo=0.65, hi=1.06, ol=0.025)
        _stitches(p, _arcpts(0, 3.08, 0.86 * wide, 1.02, 200, 340, 14), shade(bc, 0.45), 0.02)
        _grime(p, _rng(name, "belly"), bc, (0, 3.0, 0.5, 0.65), pal.get("grime", 5), 0.025, 0.07, k=(0.8, 0.9))
    # arms
    if name == "Freddy" and prop:
        _arm(p, pal, (1.22, 1.95), (1.55, 3.05), (1.5, 3.92), thumb=-1)
        _arm(p, pal, (-1.22, 1.95), (-1.62, 3.02), (-1.08, 2.98), hand=None)
        _mic(p, (-0.93, 3.32), (-0.88, 2.5))
        hp = p.sub(-1.08, 2.98, rot=_limb_rot((-1.62, 3.02), (-1.08, 2.98)))
        _hand(hp, fur, thumb=-1, fist=True)
    elif name == "Bonnie" and prop:
        _stroke(p, (40, 26, 22), [(-0.75, 3.0), (0.3, 2.15), (1.0, 1.7)], 0.1)
        _arm(p, pal, (1.18, 1.95), (1.74, 2.85), (1.3, 2.3), hand=None)
        _guitar(p.sub(-0.4, 3.45, scale=1.2, rot=50))
        hp = p.sub(1.3, 2.3, rot=_limb_rot((1.74, 2.85), (1.3, 2.3)))
        _hand(hp, fur, thumb=1, fist=True)
        _arm(p, pal, (-1.18, 1.95), (-1.62, 2.95), (-0.6, 3.28), thumb=1)
    elif name == "Chica" and prop:
        _arm(p, pal, (-1.25, 1.95), (-1.6, 3.05), (-1.52, 3.92), thumb=1)
        _arm(p, pal, (1.25, 1.95), (1.66, 3.05), (2.02, 2.78), hand=None)
        hp = p.sub(2.02, 2.78, rot=_limb_rot((1.66, 3.05), (2.02, 2.78)))
        _hand(hp, fur, thumb=-1)
        _cupcake(p.sub(2.08, 2.62))
    else:
        limp = name == "Golden"
        for sx in (-1, 1):
            if limp:
                _arm(p, pal, (sx * 1.22, 1.98), (sx * 1.5, 3.12), (sx * 1.42, 4.05), thumb=-sx)
            else:
                _arm(p, pal, (sx * 1.22, 1.95), (sx * 1.55, 3.05), (sx * 1.5, 3.92), thumb=-sx)
    if name == "Chica":
        _bib(p, 0, 2.4)
    else:
        _bowtie(p, 0, 1.4, 1.0, pal["tie"])


def _body_foxy(p, pal, pose, step):
    fur = pal["fur"]
    rng = _rng("Foxy", "body")
    run = pose == "run"
    if run:
        L = -1 if step % 2 == 0 else 1
        # back leg first (further away), then the leading leg
        _endo_leg(p, (-L * 0.42, 3.45), (-L * 0.55, 4.4), (-L * 0.48, 4.85), sx=-L, foot=0.8)
        _endo_leg(p, (L * 0.42, 3.45), (L * 0.66, 4.05), (L * 0.72, 5.6), sx=L, foot=1.15)
        top, ty = 1.48, -0.5
    else:
        for sx in (-1, 1):
            _endo_leg(p, (sx * 0.45, 4.35), (sx * 0.55, 5.05), (sx * 0.58, 5.62), sx=sx)
        top, ty = 1.95, 0.0
    # neck (endo)
    _tube(p, METAL, [(0, 0.7), (0, top - 0.4)], 0.18, lo=0.45, hi=1.1)
    _blob(p, shade(fur, 0.6), [(0, top - 0.75 + 0.0, 0.42, 0.3)], lo=0.4, hi=0.9)
    # pants (tattered shorts)
    py = 3.8 + ty
    pants = [(-0.98, py), (0.98, py), (1.02, py + 0.95)]
    hem = []
    for i in range(9):
        x = 1.0 - i * 0.12
        hem.append((x, py + 0.95 + (0.12 if i % 2 else -0.02)))
    hem += [(0.06, py + 0.62), (-0.06, py + 0.62)]
    for i in range(9):
        x = -0.04 - i * 0.12
        hem.append((x, py + 0.95 + (0.12 if i % 2 else -0.02)))
    pants += hem
    pants.append((-1.02, py + 0.95))
    _slab(p, pal["pants"], pants, lo=0.55, hi=1.05)
    _grime(p, rng, pal["pants"], (0, py + 0.4, 0.7, 0.3), 6, 0.04, 0.1)
    # torso
    ry = 0.86 if run else 1.0
    parts = [(0, top + 0.47 * ry, 1.15, 1.02 * ry), (0, top + 1.25 * ry, 1.0, 0.86 * ry)]
    for sx in (-1, 1):
        parts.append((sx * 0.95, top - 0.03, 0.42, 0.38))
    _blob(p, fur, parts, lo=0.55, hi=1.1)
    _blob(p, pal["muz"], [(0, top + 1.1 * ry, 0.6, 0.72 * ry)], lo=0.6, hi=1.0, ol=0.02)
    _grime(p, rng, fur, (0, top + 0.9, 0.7, 0.8), 10, 0.04, 0.12)
    fray = shade(fur, 1.25)
    _tear(p, 0.45, top + 0.38, 0.32, 0.36, _rng("Foxy", "t1"), fray)
    _tear(p, -0.38, top + 1.38 * ry, 0.28, 0.26, _rng("Foxy", "t2"), shade(pal["muz"], 1.1))
    _tear(p, -0.95, top + 0.02, 0.18, 0.16, _rng("Foxy", "t3"), fray)
    # arms
    if run:
        _arm(p, pal, (1.12, top), (1.6, top + 0.9 - 0.15 * L), (1.32, top + 1.65), hand="foxy", thumb=-1, tears=True)
        _arm(p, pal, (-1.12, top), (-1.78, top - 0.4), (-1.62, top - 1.3), hand="hook", thumb=1)
    else:
        _arm(p, pal, (1.15, 1.95), (1.5, 3.0), (1.45, 3.85), hand="foxy", thumb=-1, tears=True)
        _arm(p, pal, (-1.15, 1.95), (-1.5, 2.98), (-1.38, 3.78), hand="hook", thumb=1)


def _body_golden_slump(p, pal):
    fur = pal["fur"]
    rng = _rng("Golden", "slump")
    # torso sagging forward and to the side
    parts = [(0.12, 1.5, 1.32, 1.08), (0.2, 2.35, 1.32, 1.05), (-0.98, 0.95, 0.46, 0.42), (1.22, 1.12, 0.46, 0.42)]
    _blob(p, fur, parts, lo=0.5, hi=1.05)
    _blob(p, pal["belly"], [(0.18, 2.2, 0.84, 0.92)], lo=0.6, hi=1.0, ol=0.025)
    _grime(p, rng, fur, (0.15, 2.0, 0.8, 0.9), 14, 0.04, 0.13)
    # legs sticking out towards the viewer, soles up
    for sx in (-1, 1):
        _blob(p, fur, [(sx * 0.72, 3.2, 0.55, 0.4)], lo=0.5, hi=1.05)
        _tube(p, fur, [(sx * 0.95, 3.35), (sx * 1.35, 3.55)], 0.5, lo=0.5, hi=1.0)
        _blob(p, fur, [(sx * 1.5, 3.42, 0.3, 0.42, sx * 12)], lo=0.5, hi=1.05)
        _blob(p, shade(pal["belly"], 0.75), [(sx * 1.5, 3.46, 0.18, 0.28, sx * 12)], lo=0.6, hi=1.0, ol=0.02)
    # limp arms, hands resting on the floor
    _arm(p, pal, (-1.12, 1.0), (-1.65, 2.05), (-1.86, 2.95), thumb=1)
    _arm(p, pal, (1.36, 1.18), (1.8, 2.18), (1.92, 3.02), thumb=-1)
    _bowtie(p.sub(0.05, 0.95, rot=20), 0, 0, 1.0, pal["tie"])


def _body_endo(p, pal):
    m = pal["fur"]
    # legs
    for sx in (-1, 1):
        _endo_leg(p, (sx * 0.42, 4.3), (sx * 0.52, 5.02), (sx * 0.55, 5.6), sx=sx, metal=m)
    # spine and pelvis
    _tube(p, (90, 90, 98), [(0, 0.8), (0, 4.2)], 0.16, lo=0.45, hi=1.1)
    for i in range(6):
        y = 3.2 + i * 0.16
        _blob(p, m, [(0, y, 0.17, 0.065)], lo=0.45, hi=1.15, ol=0.015)
    _slab(p, m, [(-0.7, 4.0), (0.7, 4.0), (0.55, 4.5), (-0.55, 4.5)], lo=0.45, hi=1.1)
    for sx in (-1, 1):
        p.circle((40, 40, 46), sx * 0.42, 4.3, 0.07)
    # chest frame
    chest = [(-0.95, 1.65), (0.95, 1.65), (0.82, 2.65), (0.45, 3.05), (-0.45, 3.05), (-0.82, 2.65)]
    _slab(p, m, chest, lo=0.45, hi=1.12)
    for i, y in enumerate((2.0, 2.35, 2.7)):
        hw = 0.62 - i * 0.12
        p.poly((20, 18, 20), [(-hw, y - 0.08), (hw, y - 0.08), (hw - 0.05, y + 0.08), (-hw + 0.05, y + 0.08)])
    _stroke(p, (150, 30, 30), [(-0.3, 2.9), (-0.38, 3.3), (-0.2, 3.7)], 0.035)
    _stroke(p, (30, 30, 36), [(0.25, 2.95), (0.35, 3.4), (0.22, 3.8)], 0.035)
    _stroke(p, (60, 90, 170), [(0.1, 3.0), (0.16, 3.5)], 0.03)
    _tube(p, m, [(-1.2, 1.72), (1.2, 1.72)], 0.15, lo=0.45, hi=1.15)
    # arms
    for sx in (-1, 1):
        sh, el, wr = (sx * 1.25, 1.75), (sx * 1.5, 2.95), (sx * 1.45, 3.85)
        _tube(p, m, [sh, el, wr], 0.12, lo=0.45, hi=1.15)
        _tube(p, (80, 80, 88), [(sh[0] + sx * 0.09, sh[1] + 0.15), (el[0] + sx * 0.09, el[1] - 0.15)], 0.05,
              lo=0.5, hi=1.1, ol=0.012)
        for j in (sh, el, wr):
            _blob(p, m, [(j[0], j[1], 0.13, 0.13)], lo=0.4, hi=1.2, ol=0.02)
        hp = p.sub(wr[0], wr[1], rot=_limb_rot(el, wr))
        _slab(hp, m, [(-0.16, 0.08), (0.16, 0.08), (0.18, 0.32), (-0.18, 0.32)], lo=0.45, hi=1.1, ol=0.02)
        for fx in (-0.13, -0.045, 0.045, 0.13):
            _tube(hp, m, [(fx, 0.32), (fx * 1.1, 0.5), (fx * 1.05, 0.64)], 0.055, lo=0.45, hi=1.1, ol=0.015)
        _tube(hp, m, [(-sx * 0.17, 0.15), (-sx * 0.3, 0.38)], 0.06, lo=0.45, hi=1.1, ol=0.015)
    # neck
    _tube(p, m, [(0, 0.75), (0, 1.7)], 0.14, lo=0.45, hi=1.15)
    for k, c in enumerate(((150, 30, 30), (30, 30, 36), (60, 90, 170))):
        x = -0.12 + k * 0.12
        _stroke(p, c, [(x, 0.85), (x * 1.6, 1.25), (x, 1.6)], 0.03)


def _bust(p, pal, name):
    """Neck and shoulders for head-only renders."""
    fur = pal["fur"]
    if name == "Endo":
        m = fur
        _tube(p, m, [(0, 0.75), (0, 2.4)], 0.16, lo=0.45, hi=1.15)
        _tube(p, m, [(-1.3, 1.75), (1.3, 1.75)], 0.15, lo=0.45, hi=1.15)
        chest = [(-0.95, 1.65), (0.95, 1.65), (0.9, 2.4), (-0.9, 2.4)]
        _slab(p, m, chest, lo=0.45, hi=1.12)
        for sx in (-1, 1):
            _blob(p, m, [(sx * 1.25, 1.75, 0.14, 0.14)], lo=0.4, hi=1.2, ol=0.02)
        for k, c in enumerate(((150, 30, 30), (30, 30, 36), (60, 90, 170))):
            x = -0.12 + k * 0.12
            _stroke(p, c, [(x, 0.85), (x * 1.6, 1.25), (x, 1.6)], 0.03)
        return
    _blob(p, shade(fur, 0.45), [(0, 1.2, 0.38, 0.3)], lo=0.4, hi=0.9)
    _blob(p, fur, [(0, 2.25, 1.42, 0.78)], lo=0.55, hi=1.08)
    if name == "Chica":
        _blob(p, (238, 234, 222), [(0, 2.3, 0.82, 0.72)], lo=0.6, hi=1.02, ol=0.03, olc=(80, 76, 70))
    elif name == "Foxy":
        _tear(p, 0.5, 1.85, 0.25, 0.22, _rng("Foxy", "bt"), shade(fur, 1.25))
    else:
        _bowtie(p, 0, 1.4, 1.0, pal["tie"])


# ==========================================================================
# Public API
# ==========================================================================

_PAL = {
    "Freddy": dict(fur=(128, 78, 42), muz=(198, 150, 98), belly=(192, 146, 94), ear=(74, 42, 24),
                   iris=(64, 136, 238), nose=(30, 22, 20), brow=(20, 14, 12), tie=(26, 24, 28), lid=0.42),
    "Golden": dict(fur=(186, 146, 62), muz=(210, 178, 108), belly=(206, 172, 104), ear=(110, 80, 36),
                   iris=(78, 110, 170), nose=(30, 22, 20), brow=(48, 34, 20), tie=(26, 24, 28), lid=0.34,
                   grime=12),
    "Bonnie": dict(fur=(80, 82, 184), muz=(142, 138, 216), belly=(138, 132, 210), ear=(222, 150, 196),
                   iris=(230, 36, 110), nose=(234, 104, 156), brow=(36, 32, 92), tie=(198, 28, 36), lid=0.2),
    "Chica": dict(fur=(236, 194, 50), beak=(244, 134, 30), belly=None, iris=(150, 58, 190), brow=(64, 34, 60),
                  lid=0.14),
    "Foxy": dict(fur=(170, 60, 40), muz=(214, 172, 132), ear=(214, 172, 132), iris=(252, 196, 40),
                 nose=(26, 20, 20), brow=(40, 22, 18), pants=(98, 68, 46), lid=0.12),
    "Endo": dict(fur=(150, 152, 160), iris=(120, 170, 230)),
}

_HEADS = {
    "Freddy": _head_bear, "Golden": _head_bear, "Bonnie": _head_bonnie, "Chica": _head_chica,
    "Foxy": _head_foxy, "Endo": _head_endo,
}

# (x0, y0, x1, y1) per (name, body, pose); pose falls back to "stand".
_BOUNDS = {
    ("Freddy", True, "stand"): (-1.96, -1.52, 1.92, 6.1),
    ("Freddy", False, "stand"): (-1.5, -1.52, 1.5, 2.15),
    ("Golden", True, "stand"): (-1.86, -1.52, 1.86, 6.1),
    ("Golden", False, "stand"): (-1.5, -1.52, 1.5, 2.15),
    ("Golden", True, "slump"): (-2.3, -1.46, 2.32, 3.94),
    ("Golden", False, "slump"): (-1.5, -1.46, 1.5, 2.1),
    ("Bonnie", True, "stand"): (-1.96, -3.1, 2.25, 6.1),
    ("Bonnie", False, "stand"): (-1.5, -3.1, 1.5, 2.12),
    ("Chica", True, "stand"): (-1.95, -1.5, 2.68, 6.1),
    ("Chica", False, "stand"): (-1.5, -1.5, 1.5, 2.0),
    ("Foxy", True, "stand"): (-1.86, -1.92, 1.86, 6.13),
    ("Foxy", False, "stand"): (-1.5, -1.92, 1.5, 2.38),
    ("Foxy", True, "run"): (-2.14, -1.92, 1.96, 6.17),
    ("Foxy", False, "run"): (-1.5, -1.92, 1.5, 2.38),
    ("Endo", True, "stand"): (-1.72, -1.18, 1.72, 6.1),
    ("Endo", False, "stand"): (-1.46, -1.18, 1.46, 1.95),
}


def bounds(name, body=True, pose="stand"):
    """Bounding box (x0, y0, x1, y1) in local units that contains the drawing."""
    b = _BOUNDS.get((name, bool(body), pose))
    if b is None:
        b = _BOUNDS.get((name, bool(body), "stand"), (-2.6, -3.3, 2.6, 6.4))
    return b


def draw_character(pen, name, mouth=0.0, eyes="normal", look=(0.0, 0.0), body=True,
                   prop=True, pose="stand", step=0):
    """Draw an animatronic with ``pen`` (origin = head centre).

    mouth: 0 (closed) .. 1 (jaw wide open, jumpscare)
    eyes:  'normal' (white eyes, coloured iris), 'glow' (normal + glowing
           iris for dark rooms), 'pinpoint' (black sockets with tiny white
           glowing pupils), 'none' (empty black sockets)
    look:  pupil offset, each component in -1..1
    body:  False draws only the head (plus neck/shoulders)
    prop:  Freddy's mic, Bonnie's guitar, Chica's cupcake
    pose:  'stand'; Foxy also 'run' (sprint toward the office; step 0/1);
           Golden also 'slump' (sitting on the floor)
    """
    p = _XPen.wrap(pen)
    pal = _PAL.get(name, _PAL["Freddy"])
    head = _HEADS.get(name, _head_bear)
    mouth = clamp(float(mouth), 0.0, 1.0)
    if eyes not in ("normal", "glow", "pinpoint", "none"):
        eyes = "normal"
    tilt = 0.0
    if name == "Foxy" and pose == "run":
        mouth = max(mouth, 0.85)
    if name == "Golden":
        tilt = 22.0 if pose == "slump" else 6.0
    if body:
        if name == "Foxy":
            _body_foxy(p, pal, pose, step)
        elif name == "Endo":
            _body_endo(p, pal)
        elif name == "Golden" and pose == "slump":
            _body_golden_slump(p, pal)
        else:
            _body_bear(p, name, pal, prop, pose)
    else:
        _bust(p, pal, name)
    hp = p.sub(0, 0, rot=tilt) if tilt else p
    if name in ("Freddy", "Golden"):
        head(hp, pal, mouth, eyes, look, name)
    else:
        head(hp, pal, mouth, eyes, look)


def render_character(name, scale, ss=2, **kw):
    """Render to an alpha Sprite whose anchor is the head centre.

    ``scale`` is pixels per local unit. Extra keyword arguments are passed
    to draw_character.
    """
    x0, y0, x1, y1 = bounds(name, kw.get("body", True), kw.get("pose", "stand"))
    w, h = int(math.ceil((x1 - x0) * scale)), int(math.ceil((y1 - y0) * scale))

    def draw(pen):
        draw_character(pen.sub(-x0 * scale, -y0 * scale, scale), name, **kw)

    surf, glows = render_ss(w, h, draw, ss=ss, alpha=True)
    return Sprite(surf, (-x0 * scale, -y0 * scale), glows)
