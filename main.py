#!/usr/bin/env python3
"""Five Nights at Freddy's - a fan remake in pygame.

Run:  python main.py            (normal game)
      python main.py --night 3  (jump straight to a night)
      python main.py --custom 20 20 20 20   (custom night: Freddy Bonnie Chica Foxy)
      python main.py --speed 4  (night clock runs 4x faster - for testing)
"""

import argparse
import multiprocessing

from fnaf.game import Game


def parse_args():
    p = argparse.ArgumentParser(description="Five Nights at Freddy's - fan remake")
    p.add_argument("--night", type=int, choices=range(1, 7), help="skip the menu and start this night")
    p.add_argument("--custom", type=int, nargs=4, metavar=("FREDDY", "BONNIE", "CHICA", "FOXY"),
                   help="start a custom night with these AI levels (0-20)")
    p.add_argument("--speed", type=float, default=1.0, help="game-time multiplier (debug)")
    p.add_argument("--fullscreen", action="store_true", help="start in fullscreen (F11 toggles)")
    p.add_argument("--mute", action="store_true", help="disable sound")
    args = p.parse_args()
    if args.custom:
        args.custom = [max(0, min(20, v)) for v in args.custom]
    return args


if __name__ == "__main__":
    multiprocessing.freeze_support()  # sounds are synthesised in a helper process
    Game(parse_args()).run()
