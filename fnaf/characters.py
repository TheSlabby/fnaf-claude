"""Procedural animatronic art.

TEMPORARY STUB: the API below is final; the drawing is placeholder art.

Conventions (local units, drawn through a util.Pen):
  * origin (0, 0) is the centre of the head; 1 unit ~= head radius
  * +y is down; feet rest at about y = +6.0
  * ears / hat may extend up to y = -3.2 (Bonnie's ears are the tallest)
"""

import pygame

from .util import Pen, Sprite, render_ss

NAMES = ["Freddy", "Bonnie", "Chica", "Foxy", "Golden", "Endo"]

# (x0, y0, x1, y1) in local units, generous enough to contain everything.
_BODY_BOUNDS = (-2.6, -3.3, 2.6, 6.4)
_HEAD_BOUNDS = (-1.6, -3.3, 1.6, 1.9)

_COLORS = {
    "Freddy": (124, 74, 40),
    "Bonnie": (86, 84, 168),
    "Chica": (232, 188, 52),
    "Foxy": (160, 58, 40),
    "Golden": (196, 150, 52),
    "Endo": (140, 140, 148),
}


def bounds(name, body=True):
    """Bounding box (x0, y0, x1, y1) in local units."""
    return _BODY_BOUNDS if body else _HEAD_BOUNDS


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
    pose:  'stand' or 'run' (Foxy's sprint toward the office; step 0/1)
    """
    c = _COLORS[name]
    if body:
        pen.ellipse(c, 0, 2.8, 1.4, 1.8)
        for sx in (-1, 1):
            pen.ellipse(c, sx * 0.6, 5.0, 0.45, 1.0)
    if name == "Bonnie":
        for sx in (-1, 1):
            pen.ellipse(c, sx * 0.4, -1.9, 0.3, 1.1)
    else:
        for sx in (-1, 1):
            pen.circle(c, sx * 0.9, -0.85, 0.35)
    pen.circle(c, 0, 0, 1.05)
    pen.ellipse((20, 10, 10), 0, 0.6 + mouth * 0.3, 0.5, 0.1 + mouth * 0.4)
    for sx in (-1, 1):
        if eyes in ("normal", "glow"):
            pen.circle((230, 230, 230), sx * 0.4, -0.2, 0.2)
            pen.circle((20, 20, 20), sx * 0.4 + look[0] * 0.08, -0.2 + look[1] * 0.08, 0.08)
        else:
            pen.circle((5, 5, 5), sx * 0.4, -0.2, 0.2)
            if eyes == "pinpoint":
                pen.circle((255, 255, 255), sx * 0.4, -0.2, 0.04)
                pen.glow(sx * 0.4, -0.2, 0.25, (200, 200, 220))


def render_character(name, scale, ss=2, **kw):
    """Render to an alpha Sprite whose anchor is the head centre.

    ``scale`` is pixels per local unit. Extra keyword arguments are passed
    to draw_character.
    """
    x0, y0, x1, y1 = bounds(name, kw.get("body", True))
    w, h = (x1 - x0) * scale, (y1 - y0) * scale

    def draw(pen):
        draw_character(pen.sub(-x0 * scale, -y0 * scale, scale), name, **kw)

    surf, glows = render_ss(w, h, draw, ss=ss, alpha=True)
    return Sprite(surf, (-x0 * scale, -y0 * scale), glows)
