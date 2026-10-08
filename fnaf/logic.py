"""Night simulation: time, power and animatronic AI.

Pure game logic with no pygame dependency, so it can be simulated
headlessly. The scene feeds player input in (doors, lights, cameras) and
reads state + emitted events back out to drive visuals and sound.

The AI follows the structure of the original game: every animatronic gets a
"movement opportunity" every few seconds and moves if a d20 roll is at or
below its AI level (0-20).
"""

import random

from .settings import (DRAIN_PER_USAGE, HOUR_SECONDS, NIGHT_HOURS,
                       NIGHT_PASSIVE_DRAIN)

# Starting AI levels per night: Freddy, Bonnie, Chica, Foxy.
NIGHT_AI = {
    1: (0, 0, 0, 0),
    2: (0, 3, 1, 1),
    3: (1, 0, 5, 2),
    4: (1, 2, 4, 6),
    5: (3, 5, 7, 5),
    6: (4, 10, 12, 16),
}
# AI increases at certain hours (not on the custom night).
HOUR_BUMPS = {2: ("Bonnie",), 3: ("Bonnie", "Chica", "Foxy"), 4: ("Bonnie", "Chica", "Foxy")}

NEAR_ROOMS = {"2A", "2B", "3", "4A", "4B", "LDOOR", "RDOOR"}


class Roamer:
    """Bonnie (left side) and Chica (right side)."""

    def __init__(self, name, side, paths, interval, ai):
        self.name = name
        self.side = side
        self.door_loc = "LDOOR" if side == "L" else "RDOOR"
        self.paths = paths
        self.interval = interval
        self.timer = interval
        self.ai = ai
        self.loc = "1A"
        self.office_timer = 0.0

    def update(self, dt, st):
        if self.loc == "OFFICE":
            if not st.cams_up:
                self.office_timer -= dt
                if self.office_timer <= 0:
                    st.kill(self.name)
            return
        self.timer -= dt
        if self.timer <= 0:
            self.timer += self.interval
            if st.rng.randint(1, 20) <= self.ai:
                self.move(st)

    def move(self, st):
        if self.loc == self.door_loc:
            if st.doors[self.side]:
                st.move(self, "1B")
            elif st.lights[self.side] and not st.cams_up:
                # The guard is staring right at them; they hold still.
                return
            else:
                st.move(self, "OFFICE")
                st.jammed[self.side] = True
                st.lights[self.side] = False
                self.office_timer = st.rng.uniform(18.0, 30.0)
                st.emit("enter_office", who=self.name, side=self.side)
            return
        options = self.paths[self.loc]
        dest = st.rng.choice(options)
        st.move(self, dest)


BONNIE_PATHS = {
    "1A": ["1B", "5"],
    "1B": ["5", "2A"],
    "5": ["1B", "2A"],
    "2A": ["3", "2B", "2B"],
    "3": ["2A", "LDOOR"],
    "2B": ["3", "LDOOR", "LDOOR"],
}

CHICA_PATHS = {
    "1A": ["1B"],
    "1B": ["7", "6"],
    "7": ["6", "4A"],
    "6": ["7", "4A"],
    "4A": ["4B", "4B", "1B"],
    "4B": ["RDOOR", "RDOOR", "4A"],
}


class Freddy:
    PATH = ["1A", "1B", "7", "6", "4A", "4B"]

    def __init__(self, ai):
        self.name = "Freddy"
        self.ai = ai
        self.interval = 3.02
        self.timer = self.interval
        self.loc = "1A"
        self.office_timer = 0.0

    def update(self, dt, st):
        if self.loc == "OFFICE":
            if not st.cams_up:
                self.office_timer -= dt
                if self.office_timer <= 0:
                    st.kill(self.name)
            return
        self.timer -= dt
        if self.timer > 0:
            return
        self.timer += self.interval
        if st.rng.randint(1, 20) > self.ai:
            return
        if self.loc == "1A" and (st.bonnie.loc == "1A" or st.chica.loc == "1A"):
            return
        if st.cams_up and st.cam == self.loc:
            return  # Freddy never moves while you're watching him.
        if self.loc == "4B":
            if st.cams_up and st.cam != "4B":
                if st.doors["R"]:
                    st.move(self, "4A")
                else:
                    st.move(self, "OFFICE")
                    st.jammed["R"] = True
                    st.lights["R"] = False
                    self.office_timer = st.rng.uniform(1.0, 3.0)
                st.emit("laugh")
            return
        st.move(self, self.PATH[self.PATH.index(self.loc) + 1])
        st.emit("laugh")


class Foxy:
    """Lives in Pirate Cove; advances when the cameras aren't being used."""

    def __init__(self, ai):
        self.name = "Foxy"
        self.ai = ai
        self.interval = 5.01
        self.timer = self.interval
        self.stage = 0          # 0 curtains closed .. 3 left the cove
        self.state = "cove"     # cove | ready | running
        self.lock = 0.0
        self.ready_timer = 0.0
        self.run_time = 1.7
        self.run_timer = 0.0
        self.bangs = 0

    @property
    def run_progress(self):
        if self.state != "running":
            return 0.0
        return 1.0 - max(0.0, self.run_timer) / self.run_time

    def on_cams_lowered(self, st):
        self.lock = st.rng.uniform(0.83, 16.67)

    def update(self, dt, st):
        if self.state == "cove":
            if not st.cams_up:
                self.lock -= dt
            self.timer -= dt
            if self.timer <= 0:
                self.timer += self.interval
                if not st.cams_up and self.lock <= 0 and st.rng.randint(1, 20) <= self.ai:
                    self.stage += 1
                    if self.stage >= 3:
                        self.stage = 3
                        self.state = "ready"
                        self.ready_timer = 25.0
                        st.emit("foxy_left")
        elif self.state == "ready":
            self.ready_timer -= dt
            if (st.cams_up and st.cam == "2A") or self.ready_timer <= 0:
                self.state = "running"
                self.run_timer = self.run_time
                st.emit("foxy_run")
        elif self.state == "running":
            self.run_timer -= dt
            if self.run_timer <= 0:
                if st.doors["L"]:
                    drain = 1 + 5 * self.bangs
                    self.bangs += 1
                    st.power = max(0.0, st.power - drain)
                    st.emit("foxy_bang", drain=drain)
                    self.stage = st.rng.choice([0, 1])
                    self.state = "cove"
                    self.timer = self.interval
                    self.lock = st.rng.uniform(0.83, 16.67)
                else:
                    st.kill("Foxy")


class NightState:
    """All the state of one night. Advance with ``update(dt)``."""

    def __init__(self, night, ai_levels=None, rng=None):
        self.night = night
        self.rng = rng or random.Random()
        self.custom = ai_levels is not None
        if ai_levels is None:
            ai_levels = NIGHT_AI.get(night, NIGHT_AI[6])
            if night == 4:  # Freddy's Night 4 level is randomised in the original.
                ai_levels = (self.rng.choice([1, 2]),) + tuple(ai_levels[1:])
        fr, bo, ch, fx = ai_levels

        self.time = 0.0
        self.length = HOUR_SECONDS * NIGHT_HOURS
        self.hour = 0
        self.power = 100.0
        self.passive_drain = NIGHT_PASSIVE_DRAIN.get(night, NIGHT_PASSIVE_DRAIN[6])

        self.doors = {"L": False, "R": False}
        self.lights = {"L": False, "R": False}
        self.jammed = {"L": False, "R": False}
        self.cams_up = False
        self.cam = "1A"

        self.freddy = Freddy(fr)
        self.bonnie = Roamer("Bonnie", "L", BONNIE_PATHS, 4.97, bo)
        self.chica = Roamer("Chica", "R", CHICA_PATHS, 4.98, ch)
        self.foxy = Foxy(fx)
        self.roster = {"Freddy": self.freddy, "Bonnie": self.bonnie, "Chica": self.chica, "Foxy": self.foxy}

        self.power_out = None   # dict(phase=..., t=..., roll=...) once power hits 0
        self.golden = "none"    # none | poster | office | done
        self.golden_timer = 0.0
        self.result = None      # None | "win" | ("dead", who)
        self.killer = None
        self.events = []

    # -- helpers -------------------------------------------------------------
    def emit(self, name, **data):
        self.events.append((name, data))

    def pop_events(self):
        ev, self.events = self.events, []
        return ev

    def move(self, anim, dest):
        src = anim.loc
        anim.loc = dest
        self.emit("move", who=anim.name, src=src, dst=dest)

    def kill(self, who):
        if self.result is None:
            self.result = ("dead", who)
            self.killer = who
            self.emit("kill", who=who)

    @property
    def hour_label(self):
        return "12 AM" if self.hour == 0 else "%d AM" % self.hour

    def occupants(self, room):
        """Names of Freddy/Bonnie/Chica currently in a room."""
        return frozenset(a.name for a in (self.freddy, self.bonnie, self.chica) if a.loc == room)

    def at_door(self, side):
        roamer = self.bonnie if side == "L" else self.chica
        return roamer.name if roamer.loc == roamer.door_loc else None

    @property
    def usage(self):
        if self.power_out:
            return 0
        return 1 + sum(self.doors.values()) + sum(self.lights.values()) + (1 if self.cams_up else 0)

    # -- player input --------------------------------------------------------
    def can_use(self, side):
        return self.power_out is None and not self.jammed[side] and self.result is None

    def toggle_door(self, side):
        if not self.can_use(side):
            return False
        self.doors[side] = not self.doors[side]
        return True

    def toggle_light(self, side):
        if not self.can_use(side) or self.cams_up:
            return False
        on = not self.lights[side]
        self.lights = {"L": False, "R": False}
        self.lights[side] = on
        return True

    def set_cams(self, up):
        if up == self.cams_up or self.result is not None:
            return
        if up and self.power_out:
            return
        self.cams_up = up
        if up:
            self.lights = {"L": False, "R": False}
            if self.golden == "office":
                self.golden = "done"
        else:
            self.foxy.on_cams_lowered(self)
            # Anyone who slipped into the office while you were looking at
            # the monitor is waiting right in front of you.
            for anim in (self.freddy, self.bonnie, self.chica):
                if anim.loc == "OFFICE":
                    self.kill(anim.name)
                    return
            if self.golden == "poster":
                self.golden = "office"
                self.golden_timer = 5.0
                self.emit("golden")

    def set_cam(self, cam):
        if cam == self.cam:
            return
        self.cam = cam
        if (cam == "2B" and self.golden == "none" and not self.custom and self.night >= 2
                and self.rng.random() < 0.004):
            self.golden = "poster"

    # -- simulation ------------------------------------------------------------
    def update(self, dt):
        if self.result is not None:
            return
        self.time += dt
        if self.time >= self.length:
            self.result = "win"
            self.emit("win")
            return
        hour = min(NIGHT_HOURS - 1, int(self.time // HOUR_SECONDS))
        while self.hour < hour:
            self.hour += 1
            self.emit("hour", hour=self.hour)
            if not self.custom:
                for name in HOUR_BUMPS.get(self.hour, ()):
                    self.roster[name].ai = min(20, self.roster[name].ai + 1)

        if self.power_out is not None:
            self._update_power_out(dt)
            return

        self.power -= (self.usage * DRAIN_PER_USAGE + self.passive_drain) * dt
        if self.power <= 0:
            self._start_power_out()
            return

        for anim in (self.freddy, self.bonnie, self.chica, self.foxy):
            anim.update(dt, self)
            if self.result is not None:
                return

        if self.golden == "office":
            self.golden_timer -= dt
            if self.golden_timer <= 0:
                self.kill("Golden")

    def _start_power_out(self):
        self.power = 0.0
        self.doors = {"L": False, "R": False}
        self.lights = {"L": False, "R": False}
        self.cams_up = False
        self.golden = "done" if self.golden != "none" else "none"
        self.power_out = {"phase": "dark", "t": 0.0, "roll": 5.0}
        self.emit("power_out")

    def _update_power_out(self, dt):
        po = self.power_out
        po["t"] += dt
        po["roll"] -= dt
        phase = po["phase"]
        if phase == "dark":
            if po["roll"] <= 0 or po["t"] >= 20:
                po["roll"] = 5.0
                if self.rng.random() < 0.2 or po["t"] >= 20:
                    po.update(phase="show", t=0.0)
                    self.emit("musicbox")
        elif phase == "show":
            if po["roll"] <= 0 or po["t"] >= 20:
                po["roll"] = 5.0
                if self.rng.random() < 0.2 or po["t"] >= 20:
                    po.update(phase="black", t=0.0, roll=2.0)
                    self.emit("blackout")
        elif phase == "black":
            if po["roll"] <= 0:
                po["roll"] = 2.0
                if self.rng.random() < 0.2:
                    self.kill("Freddy")
