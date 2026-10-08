"""Headless tests for the night simulation (fnaf/logic.py).

Run with ``python -m pytest -q`` or ``python tests/test_logic.py``.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fnaf import logic  # noqa: E402
from fnaf.logic import NightState  # noqa: E402
from fnaf.settings import (DRAIN_PER_USAGE, HOUR_SECONDS, NIGHT_HOURS,  # noqa: E402
                           NIGHT_PASSIVE_DRAIN)


class LuckyRandom(random.Random):
    """random() always returns 0.0, so every percentage roll succeeds."""

    def random(self):
        return 0.0


def quiet(night=1, seed=1, rng=None):
    """A night where nobody moves unless a test sets them up."""
    st = NightState(night, ai_levels=(0, 0, 0, 0), rng=rng or random.Random(seed))
    st.night = night
    return st


def run(st, seconds, dt=0.05):
    t = 0.0
    while t < seconds and st.result is None:
        st.update(dt)
        t += dt


def names(st):
    return [name for name, _ in st.pop_events()]


# -- time, power, AI schedule ---------------------------------------------------

def test_night_length_and_win():
    st = quiet()
    assert st.length == HOUR_SECONDS * NIGHT_HOURS
    st.power = 1e9
    run(st, st.length + 1, dt=0.5)
    assert st.result == "win"
    ev = names(st)
    assert ev.count("hour") == NIGHT_HOURS - 1 and ev[-1] == "win"
    assert st.hour_label == "5 AM"


def test_power_drain_usage_and_passive():
    st = NightState(5, rng=random.Random(3))
    for a in (st.freddy, st.bonnie, st.chica, st.foxy):
        a.ai = 0
    run(st, 10.0)
    expected = 100 - (DRAIN_PER_USAGE + NIGHT_PASSIVE_DRAIN[5]) * 10
    assert abs(st.power - expected) < 0.05, st.power
    st.toggle_door("L")
    st.toggle_door("R")
    st.toggle_light("L")
    assert st.usage == 4
    st.set_cams(True)               # lifting the monitor turns the lights off
    assert st.usage == 4 and not st.lights["L"]


def test_hourly_ai_increases():
    st = NightState(1, rng=random.Random(1))
    st.power = 1e9
    run(st, HOUR_SECONDS * 4 + 1, dt=0.5)
    assert (st.freddy.ai, st.bonnie.ai, st.chica.ai, st.foxy.ai) == (0, 3, 2, 2)
    custom = NightState(7, ai_levels=(1, 2, 3, 4), rng=random.Random(1))
    custom.power = 1e9
    run(custom, HOUR_SECONDS * 4 + 1, dt=0.5)
    assert tuple(custom.roster[n].ai for n in ("Freddy", "Bonnie", "Chica", "Foxy")) == (1, 2, 3, 4)
    assert custom.start_ai == (1, 2, 3, 4)


def test_night_ai_table():
    assert NightState(6, rng=random.Random(1)).start_ai == (4, 10, 12, 16)
    for seed in range(10):
        assert NightState(4, rng=random.Random(seed)).freddy.ai in (1, 2)


# -- Bonnie and Chica -----------------------------------------------------------

def test_closed_door_sends_bonnie_back():
    st = quiet()
    st.bonnie.ai = 20
    st.bonnie.loc = "LDOOR"
    st.toggle_door("L")
    run(st, logic.BONNIE_INTERVAL + 0.1)
    assert st.bonnie.loc == "1B"
    assert st.result is None and not st.jammed["L"]


def test_closed_door_sends_chica_to_dining_area_or_east_hall():
    seen = set()
    for seed in range(20):
        st = quiet(seed=seed)
        st.chica.ai = 20
        st.chica.loc = "RDOOR"
        st.toggle_door("R")
        run(st, logic.CHICA_INTERVAL + 0.1)
        seen.add(st.chica.loc)
    assert seen == {"1B", "4A"}


def test_open_door_lets_bonnie_in_and_jams_it():
    st = quiet()
    st.bonnie.ai = 20
    st.bonnie.loc = "LDOOR"
    run(st, logic.BONNIE_INTERVAL + 0.1)
    assert st.bonnie.loc == "OFFICE" and st.jammed["L"]
    assert "enter_office" in names(st)
    assert st.at_door("L") is None
    assert not st.toggle_door("L") and not st.toggle_light("L")
    assert st.toggle_door("R")      # the other side still works


def test_door_light_holds_roamer_still():
    st = quiet()
    st.bonnie.ai = 20
    st.bonnie.loc = "LDOOR"
    st.toggle_light("L")
    run(st, logic.BONNIE_INTERVAL * 3)
    assert st.bonnie.loc == "LDOOR" and st.at_door("L") == "Bonnie"
    st.toggle_light("L")
    run(st, logic.BONNIE_INTERVAL + 0.1)
    assert st.bonnie.loc == "OFFICE"


def test_lowering_monitor_with_intruder_kills():
    st = quiet()
    st.bonnie.ai = 20
    st.bonnie.loc = "LDOOR"
    run(st, logic.BONNIE_INTERVAL + 0.1)
    st.pop_events()
    st.set_cams(True)
    assert ("breathing", {"who": "Bonnie"}) in st.pop_events()
    run(st, 3.0)
    assert st.result is None
    st.set_cams(False)
    assert st.result == ("dead", "Bonnie") and st.killer == "Bonnie"


def test_intruder_waits_while_monitor_stays_down():
    st = quiet()
    st.chica.ai = 20
    st.chica.loc = "RDOOR"
    run(st, 60.0)
    assert st.chica.loc == "OFFICE" and st.result is None


def test_intruder_forces_monitor_down_eventually():
    st = quiet()
    st.chica.loc = "OFFICE"
    st.chica.office_timer = logic.ROAMER_FORCE_DOWN
    st.set_cams(True)
    run(st, logic.ROAMER_FORCE_DOWN - 1)
    assert st.result is None
    run(st, 2.0)
    assert st.result == ("dead", "Chica")


def test_roamer_paths_are_connected():
    for paths, door in ((logic.BONNIE_PATHS, "LDOOR"), (logic.CHICA_PATHS, "RDOOR")):
        for room, options in paths.items():
            for dst in options:
                assert dst in paths or dst == door, (room, dst)


# -- Freddy -----------------------------------------------------------------------

def test_freddy_waits_for_the_stage_to_clear():
    st = quiet()
    st.freddy.ai = 20
    st.bonnie.loc = "1A"            # Bonnie (ai 0) never leaves
    run(st, 30.0)
    assert st.freddy.loc == "1A"


def test_freddy_cannot_move_while_monitor_up():
    st = quiet()
    st.freddy.ai = 20
    st.bonnie.loc = st.chica.loc = "5"
    st.set_cams(True)
    st.set_cam("1A")                # watching him...
    run(st, 20.0)
    assert st.freddy.loc == "1A"
    st.set_cam("7")                 # ...or any other camera
    run(st, 20.0)
    assert st.freddy.loc == "1A"
    st.set_cams(False)
    run(st, logic.FREDDY_INTERVAL + 0.1)
    assert st.freddy.loc == "1B" and "laugh" in names(st)


def test_freddy_countdown_and_watching_stalls_him():
    st = quiet()
    st.freddy.ai = 1
    assert abs(st.freddy.stall_time - 15.0) < 1e-6
    st.freddy.ai = 12
    assert st.freddy.stall_time == 0.0
    st.freddy.ai = 3
    st.bonnie.loc = st.chica.loc = "5"
    st.freddy.primed = True
    st.freddy.countdown = st.freddy.stall_time
    st.set_cams(True)
    st.set_cam("1A")
    run(st, 30.0)                   # watching him keeps resetting the wait
    assert st.freddy.loc == "1A" and st.freddy.countdown == st.freddy.stall_time
    st.set_cams(False)
    run(st, st.freddy.stall_time - 0.5)
    assert st.freddy.loc == "1A"
    run(st, 1.0)
    assert st.freddy.loc == "1B"


def test_freddy_at_4b():
    # Monitor down or watching 4B: he stays put.
    st = quiet()
    st.freddy.ai = 20
    st.freddy.loc = "4B"
    run(st, 10.0)
    st.set_cams(True)
    st.set_cam("4B")
    run(st, 10.0)
    assert st.freddy.loc == "4B"
    # Looking elsewhere with the right door shut: back to 4A.
    st.set_cams(False)
    st.toggle_door("R")
    st.set_cams(True)
    st.set_cam("1C")
    run(st, logic.FREDDY_INTERVAL + 0.1)
    assert st.freddy.loc == "4A"
    # Looking elsewhere with the door open: he's in.
    st = quiet()
    st.freddy.ai = 20
    st.freddy.loc = "4B"
    st.set_cams(True)
    st.set_cam("1C")
    run(st, logic.FREDDY_INTERVAL + 0.1)
    assert st.freddy.loc == "OFFICE" and st.jammed["R"]


def test_freddy_in_office_only_strikes_with_monitor_down():
    st = quiet(rng=LuckyRandom(1))
    st.freddy.loc = "OFFICE"
    st.set_cams(True)
    run(st, 60.0)
    assert st.result is None
    st.set_cams(False)
    assert st.result is None        # unlike Bonnie/Chica, not instantly
    run(st, 1.1)
    assert st.result == ("dead", "Freddy")


# -- Foxy ---------------------------------------------------------------------------

def test_foxy_frozen_while_monitor_up():
    st = quiet()
    st.foxy.ai = 20
    st.set_cams(True)
    st.set_cam("3")
    run(st, 60.0)
    assert st.foxy.stage == 0
    st.set_cams(False)
    run(st, logic.FOXY_LOCK[1] + logic.FOXY_INTERVAL * 3 + 0.5)
    assert st.foxy.stage == 3 and st.foxy.state in ("ready", "running")


def test_foxy_lock_after_lowering_monitor():
    st = quiet()
    st.set_cams(True)
    st.set_cams(False)
    assert logic.FOXY_LOCK[0] <= st.foxy.lock <= logic.FOXY_LOCK[1]


def test_foxy_runs_when_2a_viewed_and_bangs_drain_1_then_6():
    st = quiet()
    st.toggle_door("L")
    drains = []
    for _ in range(3):
        st.foxy.stage, st.foxy.state, st.foxy.ready_timer = 3, "ready", 25.0
        st.set_cams(True)
        st.set_cam("2A")
        st.update(0.05)
        assert st.foxy.state == "running"
        st.set_cams(False)
        st.set_cam("1A")
        before = st.power
        run(st, st.foxy.run_time + 0.1)
        bang = [d for n, d in st.pop_events() if n == "foxy_bang"]
        assert bang and st.foxy.state == "cove" and st.foxy.stage in (0, 1)
        drains.append(bang[0]["drain"])
        assert before - st.power >= bang[0]["drain"]
    assert drains == [1, 6, 11]


def test_foxy_runs_on_his_own_after_25s_and_kills_through_open_door():
    st = quiet()
    st.foxy.stage, st.foxy.state, st.foxy.ready_timer = 3, "ready", logic.FOXY_READY_TIME
    run(st, logic.FOXY_READY_TIME - 0.5)
    assert st.foxy.state == "ready"
    run(st, 0.6)
    assert st.foxy.state == "running" and 0.0 <= st.foxy.run_progress < 0.2
    run(st, st.foxy.run_time + 0.1)
    assert st.result == ("dead", "Foxy")


# -- Golden Freddy -----------------------------------------------------------------

def test_golden_freddy_poster_and_office():
    st = quiet(night=2, rng=LuckyRandom(1))
    st.custom = False
    st.set_cams(True)
    st.set_cam("2B")
    assert st.golden == "poster"
    st.set_cams(False)
    assert st.golden == "office" and "golden" in names(st)
    st.set_cams(True)               # lifting the monitor makes him vanish
    assert st.golden == "done"
    run(st, 10.0)
    assert st.result is None


def test_golden_freddy_kills_if_monitor_stays_down():
    st = quiet(night=3, rng=LuckyRandom(1))
    st.custom = False
    st.set_cams(True)
    st.set_cam("2B")
    st.set_cams(False)
    run(st, logic.GOLDEN_TIME + 0.2)
    assert st.result == ("dead", "Golden")


def test_golden_freddy_conditions():
    for night, custom, bonnie in ((1, False, "1A"), (3, True, "1A"), (3, False, "2B")):
        st = quiet(night=night, rng=LuckyRandom(1))
        st.custom = custom
        st.bonnie.loc = bonnie
        st.set_cam("2B")
        assert st.golden == "none", (night, custom, bonnie)


# -- power out -----------------------------------------------------------------------

def test_power_out_sequence_ends_in_freddy_or_6am():
    outcomes = set()
    for seed in range(40):
        st = quiet(seed=seed)
        st.toggle_door("L")
        st.set_cams(True)
        st.power = 0.05
        st.time = st.length - 50.0 if seed % 2 else 0.0
        run(st, 1.0)
        assert st.power_out is not None and not st.cams_up and not any(st.doors.values())
        assert not st.toggle_door("L") and not st.toggle_light("R")
        st.set_cams(True)
        assert not st.cams_up
        order = []
        while st.result is None:
            st.update(0.05)
            order += [n for n in names(st) if n in ("musicbox", "blackout", "kill", "win")]
        assert st.power_out["t"] <= 20.0 + 0.1 or st.power_out["phase"] == "black"
        if st.result == "win":
            outcomes.add("win")
        else:
            assert st.result == ("dead", "Freddy")
            assert order[:2] == ["musicbox", "blackout"] and order[-1] == "kill"
            outcomes.add("dead")
    assert outcomes == {"win", "dead"}


def test_foxy_bang_can_cause_power_out():
    st = quiet()
    st.toggle_door("L")
    st.power = 0.5
    st.foxy.state, st.foxy.stage, st.foxy.run_timer = "running", 3, 0.01
    run(st, 0.2)
    assert st.power_out is not None


# -- API surface ---------------------------------------------------------------------

def test_api_surface():
    st = NightState(3)
    for attr in ("night", "custom", "time", "length", "hour", "hour_label", "power", "usage", "doors",
                 "lights", "jammed", "cams_up", "cam", "freddy", "bonnie", "chica", "foxy", "roster",
                 "power_out", "golden", "result", "killer"):
        assert hasattr(st, attr), attr
    for a in (st.freddy, st.bonnie, st.chica):
        assert a.loc == "1A" and isinstance(a.ai, int) and a.name in st.roster
    assert st.foxy.state == "cove" and st.foxy.stage == 0 and st.foxy.run_progress == 0.0
    assert st.occupants("1A") == frozenset(["Freddy", "Bonnie", "Chica"])
    assert st.at_door("L") is None and st.at_door("R") is None
    assert st.toggle_door("L") is True and st.doors["L"]
    assert st.toggle_light("R") is True and st.lights["R"] and not st.lights["L"]
    st.set_cams(True)
    st.set_cam("1C")
    st.update(0.1)
    assert isinstance(st.pop_events(), list) and st.pop_events() == []
    st.kill("Bonnie")
    assert st.result == ("dead", "Bonnie") and st.killer == "Bonnie"


def test_full_nights_run_clean():
    """Smoke test: idle and busy players through every night without errors."""
    for night in range(1, 7):
        for seed in range(3):
            st = NightState(night, rng=random.Random(seed))
            t = 0.0
            while st.result is None:
                if int(t * 2) % 7 == 0:
                    st.set_cams(not st.cams_up)
                    st.set_cam(random.Random(int(t)).choice(["1A", "1C", "2A", "2B", "4A", "4B", "6"]))
                st.update(0.1)
                st.pop_events()
                t += 0.1
            assert st.result == "win" or st.result[0] == "dead"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print("ok   ", n)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL ", n, "-", type(e).__name__, e)
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    sys.exit(1 if failed else 0)
