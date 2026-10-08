#!/usr/bin/env python3
"""Play nights headlessly with a simple bot to sanity-check difficulty.

The bot cycles left light -> right light -> camera, closes doors when it
sees someone, reacts to footsteps like a player listening for them, and
watches Pirate Cove / the east hall corner. It is a decent but imperfect
player, so the numbers are a rough guide, not gospel.

    python tools/balance_sim.py          # 60 runs per night
    python tools/balance_sim.py 200
"""

import collections
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fnaf.logic import NightState  # noqa: E402


def play(night, seed, ai=None, dt=0.1):
    rng = random.Random(seed)
    st = NightState(night, ai_levels=ai, rng=random.Random(seed * 7 + 1))
    next_check = 1.0
    cycle = 0
    phase, phase_t = None, 0.0
    while st.result is None:
        if phase is None and st.time >= next_check:
            phase, phase_t = ["L", "R", "cam"][cycle % 3], 0.0
            cycle += 1
        if phase in ("L", "R"):
            if phase_t == 0:
                st.toggle_light(phase)
            phase_t += dt
            if phase_t >= 0.5:
                if st.lights[phase]:
                    st.toggle_light(phase)
                if st.at_door(phase) and not st.doors[phase]:
                    st.toggle_door(phase)
                phase, next_check = None, st.time + rng.uniform(1.2, 2.6)
        elif phase == "cam":
            if phase_t == 0:
                st.set_cams(True)
                st.set_cam("2A" if st.foxy.state == "ready" else rng.choice(["1C", "1C", "4B", "1A", "2B"]))
            phase_t += dt
            if phase_t >= 1.5:
                if st.freddy.loc == "4B" and not st.doors["R"]:
                    st.set_cam("4B")
                st.set_cams(False)
                if st.freddy.loc == "4B" and not st.doors["R"]:
                    st.toggle_door("R")
                if st.foxy.state in ("ready", "running") and not st.doors["L"]:
                    st.toggle_door("L")
                phase, next_check = None, st.time + rng.uniform(1.2, 2.6)
        # Re-open doors once the threat is gone (a player would confirm on camera).
        if st.doors["L"] and st.bonnie.loc != "LDOOR" and st.foxy.state == "cove":
            st.toggle_door("L")
        if st.doors["R"] and st.chica.loc != "RDOOR" and st.freddy.loc != "4B":
            st.toggle_door("R")
        st.update(dt)
        for name, data in st.pop_events():
            if name == "move" and data["dst"] in ("LDOOR", "RDOOR") and phase is None:
                # Heard footsteps: check that door soon.
                next_check = min(next_check, st.time + rng.uniform(0.8, 2.0))
                cycle = 0 if data["dst"] == "LDOOR" else 1
            elif name == "golden":
                phase, phase_t = "cam", 0.0
    return st.result, st.power


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    setups = [("Night %d" % n, n, None) for n in range(1, 7)] + [("4/20 mode", 7, (20, 20, 20, 20))]
    for label, night, ai in setups:
        outcomes = collections.Counter()
        power_left = []
        for seed in range(runs):
            result, power = play(night, seed, ai)
            if result == "win":
                outcomes["win"] += 1
                power_left.append(power)
            else:
                outcomes["killed by " + result[1]] += 1
        avg = sum(power_left) / len(power_left) if power_left else 0.0
        wins = outcomes.pop("win", 0)
        deaths = ", ".join("%s %d" % kv for kv in outcomes.most_common())
        print("%-10s win %3d%%   avg power left %4.1f%%   %s" % (label, 100 * wins // runs, avg, deaths))


if __name__ == "__main__":
    main()
