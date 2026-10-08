"""Tunable constants shared across the game."""

SCREEN_W, SCREEN_H = 1280, 720
FPS = 60
TITLE = "Five Nights at Freddy's - fan remake"

# Time. The original game uses roughly 89 real seconds per in-game hour.
HOUR_SECONDS = 75.0
NIGHT_HOURS = 6

# Power, in percent per second. Usage bars = 1 + doors + lights + camera.
DRAIN_PER_USAGE = 0.1
NIGHT_PASSIVE_DRAIN = {1: 0.0, 2: 0.01, 3: 0.018, 4: 0.026, 5: 0.034, 6: 0.04, 7: 0.04}

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
