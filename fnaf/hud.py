"""HUD pieces: clock, power meter, camera map and the monitor toggle bar."""

import pygame

from .settings import CAM_NAMES, SCREEN_H, SCREEN_W
from .util import draw_text

# Camera map placement (screen coordinates).
MAP_RECT = pygame.Rect(842, 372, 410, 300)

# Room outlines in map-local coordinates.
_ROOMS = [
    (150, 0, 120, 38),     # 1A show stage
    (78, 38, 262, 118),    # 1B dining area
    (14, 52, 64, 76),      # 5 backstage
    (340, 44, 56, 92),     # 7 restrooms
    (318, 156, 78, 74),    # 6 kitchen
    (78, 118, 34, 38),     # 1C pirate cove nook
    (146, 156, 34, 136),   # west hall (2A / 2B)
    (100, 186, 46, 42),    # 3 supply closet
    (238, 156, 34, 136),   # east hall (4A / 4B)
    (180, 238, 58, 54),    # office
]

_BUTTONS = {
    "1A": (178, 2),
    "1B": (150, 70),
    "1C": (40, 134),
    "5": (6, 82),
    "7": (344, 62),
    "6": (330, 184),
    "3": (66, 196),
    "2A": (104, 150),
    "2B": (104, 246),
    "4A": (276, 150),
    "4B": (276, 246),
}
BTN_W, BTN_H = 52, 32


def map_buttons():
    """{cam: screen Rect} for the clickable camera buttons."""
    out = {}
    for cam, (x, y) in _BUTTONS.items():
        out[cam] = pygame.Rect(MAP_RECT.x + x, MAP_RECT.y + y, BTN_W, BTN_H)
    return out


_map_base = None


def _build_map_base():
    surf = pygame.Surface(MAP_RECT.size, pygame.SRCALPHA)
    line = (225, 225, 225, 200)
    for x, y, w, h in _ROOMS:
        pygame.draw.rect(surf, line, (x, y, w, h), 3)
    # The office.
    ox, oy, ow, oh = _ROOMS[-1]
    draw_text(surf, "YOU", (ox + ow // 2, oy + oh // 2 + 2), 20, (235, 235, 235), anchor="center", bold=True)
    return surf


def draw_map(screen, selected, t):
    global _map_base
    if _map_base is None:
        _map_base = _build_map_base()
    screen.blit(_map_base, MAP_RECT.topleft)
    blink = int(t * 2.2) % 2 == 0
    for cam, r in map_buttons().items():
        sel = cam == selected
        fill = (60, 160, 40) if sel and blink else (52, 98, 40) if sel else (72, 72, 72)
        pygame.draw.rect(screen, fill, r)
        pygame.draw.rect(screen, (220, 220, 220), r, 2)
        draw_text(screen, "CAM", (r.centerx, r.y + 9), 13, (240, 240, 240), anchor="center", mono=True, bold=True)
        draw_text(screen, cam, (r.centerx, r.y + 22), 15, (240, 240, 240), anchor="center", mono=True, bold=True)
    draw_text(screen, CAM_NAMES[selected], (MAP_RECT.x - 4, MAP_RECT.y - 34), 30, (240, 240, 240),
              anchor="topleft", bold=True, shadow=(0, 0, 0))


def draw_cam_frame(screen, cam, t):
    """Thin monitor frame, the blinking REC dot and the camera id."""
    pygame.draw.rect(screen, (210, 210, 210), (22, 22, SCREEN_W - 44, SCREEN_H - 44), 2)
    for (x, y, dx, dy) in ((22, 22, 1, 1), (SCREEN_W - 22, 22, -1, 1),
                           (22, SCREEN_H - 22, 1, -1), (SCREEN_W - 22, SCREEN_H - 22, -1, -1)):
        pygame.draw.line(screen, (240, 240, 240), (x, y), (x + 40 * dx, y), 5)
        pygame.draw.line(screen, (240, 240, 240), (x, y), (x, y + 40 * dy), 5)
    if int(t * 1.4) % 2 == 0:
        pygame.draw.circle(screen, (220, 20, 20), (62, 112), 13)
    draw_text(screen, "REC", (84, 112), 24, (230, 230, 230), anchor="midleft", mono=True, bold=True)
    draw_text(screen, "CAM " + cam, (84, 140), 18, (200, 200, 200), anchor="midleft", mono=True)


def draw_clock(screen, hour_label, night):
    draw_text(screen, hour_label, (SCREEN_W - 40, 26), 64, (245, 245, 245), anchor="topright", bold=True,
              shadow=(0, 0, 0))
    draw_text(screen, "Night %d" % night if night <= 6 else "Custom Night", (SCREEN_W - 42, 78), 28,
              (225, 225, 225), anchor="topright", bold=True, shadow=(0, 0, 0))


_USAGE_COLORS = [(60, 200, 60), (60, 200, 60), (230, 200, 40), (225, 60, 40), (225, 60, 40)]


def draw_power(screen, power, usage):
    x, y = 40, SCREEN_H - 112
    r = draw_text(screen, "Power left: ", (x, y), 30, (240, 240, 240), bold=True, shadow=(0, 0, 0))
    draw_text(screen, "%d%%" % max(0, int(power)), (r.right, y - 4), 40, (250, 250, 250), bold=True,
              shadow=(0, 0, 0))
    r = draw_text(screen, "Usage: ", (x, y + 40), 30, (240, 240, 240), bold=True, shadow=(0, 0, 0))
    bx = r.right + 4
    for i in range(min(5, usage)):
        rect = pygame.Rect(bx + i * 24, y + 36, 19, 30)
        pygame.draw.rect(screen, _USAGE_COLORS[i], rect)
        pygame.draw.rect(screen, (20, 20, 20), rect, 2)


# The strip at the bottom you hover over to raise / lower the monitor.
CAM_BAR = pygame.Rect(SCREEN_W // 2 - 300, SCREEN_H - 52, 600, 40)
_bar = None


def draw_cam_bar(screen, hot):
    global _bar
    if _bar is None:
        _bar = pygame.Surface(CAM_BAR.size, pygame.SRCALPHA)
        _bar.fill((255, 255, 255, 34))
        pygame.draw.rect(_bar, (255, 255, 255, 150), _bar.get_rect(), 2)
        cx, cy = CAM_BAR.w // 2, CAM_BAR.h // 2
        for dx in (-26, 0, 26):
            for dy in (-6, 6):
                pygame.draw.lines(_bar, (255, 255, 255, 190), False,
                                  [(cx + dx - 9, cy + dy + 4), (cx + dx, cy + dy - 4), (cx + dx + 9, cy + dy + 4)], 3)
    _bar.set_alpha(255 if hot else 170)
    screen.blit(_bar, CAM_BAR.topleft)
