#!/usr/bin/env python3
"""Play nights headlessly with a bot to sanity-check difficulty.

The bot plays like a competent (not perfect) player who knows the rules:

* flips the monitor up every few seconds (Foxy can't move while any camera
  is up) and checks both door lights every other round, with human-ish
  pauses; flips faster once Foxy is peeking out of the cove;
* listens for footsteps at the doors and checks that door soon after,
  closes it on whoever is in the light, reopens it once they've walked off;
* glances at Pirate Cove, and at Freddy once he's in the east hall
  (watching him stalls him);
* tracks Freddy by his laughs: while he's at CAM 4B it shuts the right door
  before looking at any other camera;
* closes the left door when Foxy leaves the cove, opens it after the bang;
* if a door button is dead, someone is inside: it stops using the monitor;
* lifts the monitor at once if Golden Freddy shows up;
* eases off the monitor and lights when power is running short.

It also makes mistakes: it misses some footsteps (more often while staring at
the monitor), reacts with a delay, and now and then forgets Freddy is at 4B.
It reads a few things straight from the state that a player would know
from sounds and the cameras (Freddy's room via laughs, who's in the room on
screen). The numbers are a rough guide, not gospel.

    python tools/balance_sim.py          # 100 runs per night
    python tools/balance_sim.py 300
"""

import collections
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fnaf.logic import NightState  # noqa: E402
from fnaf.settings import DRAIN_PER_USAGE  # noqa: E402

RAISE_TIME = 0.26   # monitor animation in the scene; cams_up flips at the end
LIGHT_EVERY = 2     # routine light checks every Nth cycle (footsteps prompt extra ones)
GLANCE = 0.6        # seconds looking at a camera
CAM_GAP = (1.0, 2.6)  # pause after lowering the monitor
FOXY_GAP = (0.5, 1.4)  # ...when Foxy is already peeking out
# Human error: footsteps that go unnoticed (phone call, busy on the cameras,
# just not paying attention), reaction time, and losing track of Freddy.
MISS_FOOTSTEPS = (0.2, 0.45)   # (idle in the office, busy on the monitor)
REACTION = (0.6, 1.8)
FREDDY_LAPSE = 0.1


class Bot:
    def __init__(self, st, rng):
        self.st = st
        self.rng = rng
        self.plan = []           # queued actions: "L", "R", "cam"
        self.act = None
        self.act_t = 0.0
        self.wait = 1.0          # pause before the next action
        self.cycle = 0
        self.inside = False      # learned that someone got in (dead button)
        self.foxy_out = False    # saw an empty cove / heard him run
        self.foxy_seen = 0       # Foxy's stage last time we looked at the cove
        self.reasons = {"L": set(), "R": set()}
        self.cam_step = 0
        self.extra = 0.0
        self.later = []          # [seconds, callback] reactions in flight

    # -- helpers -------------------------------------------------------------
    def close(self, side, why):
        self.reasons[side].add(why)
        self.sync_doors()

    def release(self, side, why):
        self.reasons[side].discard(why)
        self.sync_doors()

    def monitor_busy(self):
        """Doors can't be used while the monitor is up or moving (as in the scene)."""
        return self.st.cams_up or (self.act == "cam" and self.cam_step in (1, 2, 3))

    def sync_doors(self):
        st = self.st
        if self.monitor_busy():
            return
        for side in "LR":
            want = bool(self.reasons[side])
            if st.doors[side] != want and not st.toggle_door(side) and st.jammed[side]:
                self.inside = True

    def short_on_power(self):
        st = self.st
        left = st.length - st.time
        idle = (DRAIN_PER_USAGE + st.passive_drain) * left
        return st.power - idle < 6.0

    def urgent(self, side, sure=False):
        """Heard footsteps at a door: check it next (unless already busy)."""
        busy = self.st.cams_up or self.act is not None
        if not sure and self.rng.random() < MISS_FOOTSTEPS[busy]:
            return
        if side not in self.plan:
            self.plan.insert(0, side)
        self.wait = min(self.wait, self.rng.uniform(*REACTION))

    # -- main loop -----------------------------------------------------------
    def step(self, dt):
        st = self.st
        if st.power_out:
            return
        for item in list(self.later):
            item[0] -= dt
            if item[0] <= 0:
                self.later.remove(item)
                item[1]()
        self.sync_doors()
        if self.act is None:
            self.wait -= dt
            if self.wait > 0:
                return
            if not self.plan:
                self.cycle += 1
                self.plan = ["L", "R", "cam"] if self.cycle % LIGHT_EVERY == 0 else ["cam"]
            self.act = self.plan.pop(0)
            self.act_t = 0.0
            self.cam_step = 0
        if self.act in ("L", "R"):
            self.do_light(self.act, dt)
        else:
            self.do_cams(dt)

    def finish(self, lo, hi):
        self.act = None
        if self.short_on_power():
            lo, hi = lo * 2.5, hi * 2.5
        self.wait = self.rng.uniform(lo, hi)

    def do_light(self, side, dt):
        st = self.st
        if self.act_t == 0.0:
            if not st.toggle_light(side):
                if st.jammed[side]:
                    self.inside = True
                self.finish(0.3, 0.8)
                return
        self.act_t += dt
        if self.act_t >= 0.5:
            who = st.at_door(side)
            if st.lights[side]:
                st.toggle_light(side)
            name = "bonnie" if side == "L" else "chica"
            if who:
                self.close(side, name)
            else:
                self.release(side, name)
            self.finish(0.6, 1.8)

    def do_cams(self, dt):
        st = self.st
        fr = st.freddy
        if self.cam_step == 0:
            if self.inside or (self.short_on_power() and not self.foxy_out and st.golden != "poster"
                               and self.rng.random() < 0.5):
                self.finish(0.8, 2.0)
                return
            if fr.loc == "4B" and self.rng.random() >= FREDDY_LAPSE:
                self.close("R", "freddy")   # he slips in while you look elsewhere
            if self.foxy_out:
                self.close("L", "foxy")
            self.cam_step = 1
        self.act_t += dt
        if self.cam_step == 1 and self.act_t >= RAISE_TIME:
            st.set_cams(True)
            st.set_cam("2A" if self.foxy_out else "1C")
            self.cam_step = 2
        elif self.cam_step == 2 and self.act_t >= RAISE_TIME + GLANCE:
            if st.cam == "1C":
                self.foxy_seen = st.foxy.stage
            if st.foxy.state != "cove":
                self.foxy_out = True
            self.extra = 0.0
            if fr.loc in ("4A", "4B"):
                st.set_cam(fr.loc)   # keep an eye on him; watching him stalls him
                self.extra = GLANCE
            self.cam_step = 3
        elif self.cam_step == 3 and self.act_t >= RAISE_TIME + GLANCE + self.extra:
            st.set_cams(False)
            if self.foxy_seen >= 1 and not self.short_on_power():
                self.finish(*FOXY_GAP)     # he's restless: keep flipping the monitor
            else:
                self.finish(*CAM_GAP)
            if self.foxy_out:
                self.close("L", "foxy")
            self.release("R", "freddy")     # he can't get in while the monitor is down

    def on_event(self, name, data):
        st = self.st
        if name == "move" and data["who"] in ("Bonnie", "Chica"):
            side = "L" if data["who"] == "Bonnie" else "R"
            if data["dst"] in ("LDOOR", "RDOOR"):
                self.urgent(side)
            elif data["src"] in ("LDOOR", "RDOOR"):
                # Heard them walk off; confirm with the light before reopening.
                self.urgent(side)
        elif name == "foxy_run":
            # Hear him running (or see him on 2A): slam the door after a beat.
            self.foxy_out = True
            if st.cams_up:
                # Drop the monitor after a short reaction time.
                self.act = "cam"
                self.cam_step = 3
                self.extra = 0.0
                self.act_t = RAISE_TIME + GLANCE - self.rng.uniform(0.2, 0.4)
            self.later.append([self.rng.uniform(0.35, 0.7), lambda: self.close("L", "foxy")])
        elif name == "foxy_bang":
            self.foxy_out = False
            self.foxy_seen = 0
            self.release("L", "foxy")
            self.urgent("L", sure=True)
        elif name == "golden":
            self.plan.insert(0, "cam")
            self.act = None
            self.wait = 0.0
        elif name == "move" and data["who"] == "Freddy" and data["src"] == "4B":
            self.release("R", "freddy")


def play(night, seed, ai=None, dt=0.05):
    rng = random.Random(seed)
    st = NightState(night, ai_levels=ai, rng=random.Random(seed * 7 + 1))
    bot = Bot(st, rng)
    while st.result is None:
        bot.step(dt)
        st.update(dt)
        for name, data in st.pop_events():
            bot.on_event(name, data)
    killer = None
    if st.result != "win":
        killer = st.result[1] + (" (power out)" if st.power_out else "")
    return st.result, st.power, killer


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    setups = [("Night %d" % n, n, None) for n in range(1, 7)] + [("4/20 mode", 7, (20, 20, 20, 20))]
    for label, night, ai in setups:
        outcomes = collections.Counter()
        power_left = []
        for seed in range(runs):
            result, power, killer = play(night, seed, ai)
            if result == "win":
                outcomes["win"] += 1
                power_left.append(power)
            else:
                outcomes[killer] += 1
        avg = sum(power_left) / len(power_left) if power_left else 0.0
        wins = outcomes.pop("win", 0)
        deaths = ", ".join("%s %d" % kv for kv in outcomes.most_common())
        print("%-10s win %3d%%   avg power left %4.1f%%   %s" % (label, 100 * wins // runs, avg, deaths))


if __name__ == "__main__":
    main()
