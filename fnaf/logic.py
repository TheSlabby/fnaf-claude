"""Night simulation: time, power and animatronic AI.

Pure game logic with no pygame dependency, so it can be simulated
headlessly. The scene feeds player input in (doors, lights, cameras) and
reads state + emitted events back out to drive visuals and sound.

The rules follow the community's reverse-engineering of the original
Five Nights at Freddy's (technicalfnaf.fandom.com "Movement Opportunities /
AI Levels / Power Mechanics / Freddy / Bonnie / Chica / Foxy (Fnaf 1)", the
Steam guide "Fnaf 1 - Technical", ModDB "Technical FNaF Facts"):

* Every animatronic gets a *movement opportunity* on a fixed interval
  (Freddy 3.02 s, Bonnie 4.97 s, Chica 4.98 s, Foxy 5.01 s) and passes it if
  a 1-20 roll is at or below its AI level (0-20).
* Bonnie and Chica wander a fixed room graph (Bonnie can skip rooms and
  backtrack). From the blind spot outside the office their next successful
  opportunity either sends them away (door closed) or lets them in (door
  open), which jams that side's door and light. Once inside they strike the
  next time the monitor comes down, or yank it down if it stays up too long.
  If you never lift the monitor again they can't get you.
* Freddy only rolls while the monitor is down. A successful roll primes him,
  and he then waits ``(1000 - 100 * AI)`` frames at 60 fps (0 from AI 10)
  before moving; watching his camera restarts that wait, and he only
  actually moves while the monitor is down (he laughs when he does). From
  CAM 4B he only advances while the monitor is up and you are *not*
  watching 4B: with the right door shut he falls back to 4A, otherwise he is
  in the office, where he has a 25% chance per second to strike while the
  monitor is down. He never leaves the stage while Bonnie or Chica is on it.
* Foxy only advances while the monitor is down, and is locked for a random
  0.83-16.67 s each time it comes down (watching Pirate Cove also stalls his
  interval). Once out he runs after 25 s or as soon as you look at CAM 2A.
  A closed left door costs 1%, then 6%, 11%... power per bang.
* Power drains 0.1% per second per usage bar, plus an extra 0.1% every
  6/5/4/3 seconds on nights 2/3/4/5+ (see settings.py).
* Power out: Freddy shows up within 20 s (20% every 5 s), plays the music box
  for up to 20 s (20% every 5 s to stop), then strikes in the dark (20% every
  2 s). Reaching 6 AM still wins.

One deliberate remake rule on top of the original: Bonnie or Chica standing
at a door holds still while you shine that door's light on them (monitor
down). It is readable, fair, and costs power, so it stays.
"""

import random

from .settings import (DRAIN_PER_USAGE, HOUR_SECONDS, NIGHT_HOURS,
                       NIGHT_PASSIVE_DRAIN)

ORIGINAL_FPS = 60.0

# Starting AI levels per night: Freddy, Bonnie, Chica, Foxy.
NIGHT_AI = {
    1: (0, 0, 0, 0),
    2: (0, 3, 1, 1),
    3: (1, 0, 5, 2),
    4: (1, 2, 4, 6),   # Freddy is 1 or 2 at random on night 4
    5: (3, 5, 7, 5),
    6: (4, 10, 12, 16),
}
# AI increases at certain hours. The original also applies these on the
# custom night; the remake keeps custom levels fixed so the numbers picked
# on the custom-night screen mean what they say (and `.ai` stays the level
# the player chose, which the 4/20 star check reads).
HOUR_BUMPS = {2: ("Bonnie",), 3: ("Bonnie", "Chica", "Foxy"), 4: ("Bonnie", "Chica", "Foxy")}

NEAR_ROOMS = {"2A", "2B", "3", "4A", "4B", "LDOOR", "RDOOR"}

# Movement-opportunity intervals in seconds.
FREDDY_INTERVAL = 3.02
BONNIE_INTERVAL = 4.97
CHICA_INTERVAL = 4.98
FOXY_INTERVAL = 5.01

# Bonnie/Chica inside the office: how long the monitor can stay up before
# they force it down.
ROAMER_FORCE_DOWN = 30.0
# Freddy inside the office: chance per second (monitor down) that he strikes.
FREDDY_OFFICE_KILL_CHANCE = 0.25
# Foxy: lock after the monitor comes down (50-1000 frames at 60 fps), how
# long he waits out of the cove before running, and the run itself.
FOXY_LOCK = (50 / ORIGINAL_FPS, 1000 / ORIGINAL_FPS)
FOXY_READY_TIME = 25.0
FOXY_RUN_TIME = 1.7
# Golden Freddy: chance per switch to CAM 2B that the poster turns (much
# likelier than the original's ~1/32768 per second, so people actually see
# him), and how long you have to lift the monitor once he sits in the office.
GOLDEN_CHANCE = 0.004
GOLDEN_TIME = 5.0
# Rare "IT'S ME" / face-flash hallucinations: chance per second, from night 2
# (about one every other night).
HALLUCINATION_CHANCE = 1 / 1000.0


def roll(st, ai):
    """A movement opportunity: d20 at or below the AI level passes."""
    return st.rng.randint(1, 20) <= ai


class Roamer:
    """Bonnie (left side) and Chica (right side)."""

    def __init__(self, name, side, paths, retreat, interval, ai):
        self.name = name
        self.side = side
        self.door_loc = "LDOOR" if side == "L" else "RDOOR"
        self.paths = paths
        self.retreat = retreat      # where they go when the door is shut on them
        self.interval = interval
        self.timer = interval
        self.ai = ai
        self.loc = "1A"
        self.office_timer = 0.0     # monitor-up time left before they force it down

    def update(self, dt, st):
        if self.loc == "OFFICE":
            # Inside: they wait for the monitor to come back down (set_cams),
            # or pull it down themselves if it stays up too long.
            if st.cams_up:
                self.office_timer -= dt
                if self.office_timer <= 0:
                    st.kill(self.name)
            return
        self.timer -= dt
        if self.timer <= 0:
            self.timer += self.interval
            if roll(st, self.ai):
                self.move(st)

    def move(self, st):
        if self.loc == self.door_loc:
            if st.doors[self.side]:
                st.move(self, st.rng.choice(self.retreat))
            elif st.lights[self.side] and not st.cams_up:
                # The guard is staring right at them; they hold still.
                return
            else:
                st.enter_office(self, self.side)
                self.office_timer = ROAMER_FORCE_DOWN
            return
        st.move(self, st.rng.choice(self.paths[self.loc]))


# Room graphs from the original. Bonnie can skip rooms (1A -> 5, 1B -> 2A)
# and backtrack; a shut door always sends him back to the dining area.
BONNIE_PATHS = {
    "1A": ["1B", "5"],
    "1B": ["5", "2A"],
    "5": ["1B", "2A"],
    "2A": ["3", "2B"],
    "3": ["2A", "LDOOR"],
    "2B": ["3", "LDOOR"],
}
BONNIE_RETREAT = ["1B"]

# Chica often wanders back to the dining area from the east hall, so she
# reaches her door less often than Bonnie, but a shut door can send her
# straight back to 4A for another go.
CHICA_PATHS = {
    "1A": ["1B"],
    "1B": ["7", "6"],
    "7": ["6", "4A"],
    "6": ["7", "4A"],
    "4A": ["1B", "4B"],
    "4B": ["4A", "RDOOR"],
}
CHICA_RETREAT = ["1B", "4A"]


class Freddy:
    PATH = ["1A", "1B", "7", "6", "4A", "4B"]

    def __init__(self, ai):
        self.name = "Freddy"
        self.ai = ai
        self.interval = FREDDY_INTERVAL
        self.timer = self.interval
        self.loc = "1A"
        self.primed = False     # passed a roll and is waiting to move
        self.countdown = 0.0    # seconds left before he moves (restarts while watched)
        self.office_timer = 1.0

    @property
    def stall_time(self):
        """How long Freddy waits after a successful roll (0 from AI 10)."""
        return max(0.0, (1000 - 100 * self.ai) / ORIGINAL_FPS)

    def update(self, dt, st):
        if self.loc == "OFFICE":
            if not st.cams_up:
                self.office_timer -= dt
                if self.office_timer <= 0:
                    self.office_timer += 1.0
                    if st.rng.random() < FREDDY_OFFICE_KILL_CHANCE:
                        st.kill(self.name)
            return
        self.timer -= dt
        if self.timer <= 0:
            self.timer += self.interval
            self.opportunity(st)
            if self.loc == "OFFICE" or st.result is not None:
                return
        if self.primed:
            if st.cams_up and st.cam == self.loc:
                self.countdown = self.stall_time   # watching him stalls him
            else:
                self.countdown -= dt
                if self.countdown <= 0 and not st.cams_up:
                    self.primed = False
                    st.move(self, self.PATH[self.PATH.index(self.loc) + 1])
                    st.emit("laugh")

    def opportunity(self, st):
        if self.loc == "4B":
            # The last step only happens while you're looking at some other camera.
            if st.cams_up and st.cam != "4B" and roll(st, self.ai):
                if st.doors["R"]:
                    st.move(self, "4A")
                else:
                    st.enter_office(self, "R")
                    self.office_timer = 1.0
                st.emit("laugh")
            return
        if self.primed or st.cams_up:
            return
        if self.loc == "1A" and (st.bonnie.loc == "1A" or st.chica.loc == "1A"):
            return
        if roll(st, self.ai):
            self.primed = True
            self.countdown = self.stall_time


class Foxy:
    """Lives in Pirate Cove; advances when the cameras aren't being used."""

    def __init__(self, ai):
        self.name = "Foxy"
        self.ai = ai
        self.interval = FOXY_INTERVAL
        self.timer = self.interval
        self.stage = 0          # 0 curtains closed, 1 peeking, 2 stepping out, 3 left the cove
        self.state = "cove"     # cove | ready | running
        self.lock = 0.0
        self.ready_timer = 0.0
        self.run_time = FOXY_RUN_TIME
        self.run_timer = 0.0
        self.bangs = 0

    @property
    def run_progress(self):
        if self.state != "running":
            return 0.0
        return 1.0 - max(0.0, self.run_timer) / self.run_time

    def on_cams_lowered(self, st):
        self.lock = st.rng.uniform(*FOXY_LOCK)

    def update(self, dt, st):
        if self.state == "cove":
            if not st.cams_up:
                self.lock -= dt
            if not (st.cams_up and st.cam == "1C"):
                self.timer -= dt    # watching the cove stalls him outright
            if self.timer <= 0:
                self.timer += self.interval
                if not st.cams_up and self.lock <= 0 and roll(st, self.ai):
                    self.stage += 1
                    if self.stage >= 3:
                        self.stage = 3
                        self.state = "ready"
                        self.ready_timer = FOXY_READY_TIME
                        st.emit("foxy_left")
                    else:
                        st.emit("foxy_stage", stage=self.stage)
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
                    self.lock = st.rng.uniform(*FOXY_LOCK)
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
        self.start_ai = (fr, bo, ch, fx)

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
        self.bonnie = Roamer("Bonnie", "L", BONNIE_PATHS, BONNIE_RETREAT, BONNIE_INTERVAL, bo)
        self.chica = Roamer("Chica", "R", CHICA_PATHS, CHICA_RETREAT, CHICA_INTERVAL, ch)
        self.foxy = Foxy(fx)
        self.roster = {"Freddy": self.freddy, "Bonnie": self.bonnie, "Chica": self.chica, "Foxy": self.foxy}

        self.power_out = None   # dict(phase=..., t=..., roll=...) once power hits 0
        self.golden = "none"    # none | poster | office | done
        self.golden_timer = 0.0
        self.hallucination_timer = 1.0
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

    def enter_office(self, anim, side):
        """``anim`` slips into the office through ``side``, jamming that door."""
        self.move(anim, "OFFICE")
        self.jammed[side] = True
        self.lights[side] = False
        self.emit("enter_office", who=anim.name, side=side)

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
            for anim in (self.bonnie, self.chica):
                if anim.loc == "OFFICE":
                    self.emit("breathing", who=anim.name)
        else:
            self.foxy.on_cams_lowered(self)
            # Bonnie or Chica got in: they're right in front of you the
            # moment the monitor comes down. (Freddy rolls for it instead.)
            for anim in (self.bonnie, self.chica):
                if anim.loc == "OFFICE":
                    self.kill(anim.name)
                    return
            if self.golden == "poster":
                self.golden = "office"
                self.golden_timer = GOLDEN_TIME
                self.emit("golden")

    def set_cam(self, cam):
        if cam == self.cam:
            return
        self.cam = cam
        if (cam == "2B" and self.golden == "none" and not self.custom and self.night >= 2
                and self.bonnie.loc != "2B" and self.rng.random() < GOLDEN_CHANCE):
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
                return

        self.hallucination_timer -= dt
        if self.hallucination_timer <= 0:
            self.hallucination_timer += 1.0
            if self.night >= 2 and self.rng.random() < HALLUCINATION_CHANCE:
                kind = "its_me" if self.cams_up else self.rng.choice(["its_me", "faces"])
                self.emit("hallucination", kind=kind, cams=self.cams_up)

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
