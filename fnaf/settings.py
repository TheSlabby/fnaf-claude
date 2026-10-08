"""Tunable constants shared across the game."""

SCREEN_W, SCREEN_H = 1280, 720
FPS = 60
TITLE = "Five Nights at Freddy's - fan remake"

# Time. The original uses 89 real seconds per in-game hour (12 AM is one
# second longer), so a night is just under 9 minutes.
HOUR_SECONDS = 89.0
NIGHT_HOURS = 6

# Power, in percent per second. Usage bars = 1 + doors + lights + camera.
# As in the original: 0.1% per second per bar, plus an extra 0.1% every
# 6/5/4/3 seconds on nights 2/3/4/5+ (custom night counts as 5+).
DRAIN_PER_USAGE = 0.1
NIGHT_PASSIVE_DRAIN = {1: 0.0, 2: 0.1 / 6, 3: 0.1 / 5, 4: 0.1 / 4, 5: 0.1 / 3, 6: 0.1 / 3, 7: 0.1 / 3}

# Office panorama is wider than the screen; the player pans across it.
OFFICE_W = 1920
OFFICE_SCROLL_MAX = OFFICE_W - SCREEN_W

# Camera feeds are rendered wider than the screen and slowly pan.
CAM_W, CAM_H = 1440, 720
CAM_PAN_MAX = CAM_W - SCREEN_W

CAM_NAMES = {
    "1A": "Show Stage",
    "1B": "Dining Area",
    "1C": "Pirate Cove",
    "2A": "West Hall",
    "2B": "W. Hall Corner",
    "3": "Supply Closet",
    "4A": "East Hall",
    "4B": "E. Hall Corner",
    "5": "Backstage",
    "6": "Kitchen",
    "7": "Restrooms",
}
CAM_ORDER = ["1A", "1B", "1C", "5", "7", "6", "3", "2A", "2B", "4A", "4B"]

CHARACTERS = ["Freddy", "Bonnie", "Chica", "Foxy"]
