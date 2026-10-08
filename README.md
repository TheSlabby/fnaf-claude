# Five Nights at Freddy's — pygame fan remake

A from-scratch recreation of the first *Five Nights at Freddy's*, made for fun.
Everything (the office, camera feeds, animatronics, jumpscares, static, every
sound effect and the Toreador March music box) is generated procedurally in
code at startup. There are no image or audio files.

Fan project, not affiliated with or endorsed by Scott Cawthon. All characters
belong to him.

## Running it

```bash
pip install pygame        # or: pip install pygame-ce
python main.py
```

Python 3.8+ and pygame 2.1+ (or pygame-ce). The first launch takes a few
seconds while the art and sounds are generated.

Useful flags:

| Flag | What it does |
| --- | --- |
| `--night N` | Skip the menu and start night N (1-6) |
| `--custom F B C X` | Custom night with AI levels for Freddy, Bonnie, Chica, Foxy (0-20) |
| `--speed 4` | Make the night clock run 4x faster (for testing) |
| `--fullscreen` | Start fullscreen (F11 toggles at any time) |
| `--mute` | No sound |

## How to play

Survive from 12 AM to 6 AM (about 7.5 real minutes per night) without letting
the animatronics reach you. Doors, lights and the camera all use power, and
when the power runs out the doors stop working...

| Action | Mouse | Keyboard |
| --- | --- | --- |
| Look around the office | Move to the screen edges | ← / → |
| Left door / left light | Click the panel buttons left of the door | Q / A |
| Right door / right light | Click the panel buttons right of the door | E / D |
| Raise / lower the camera monitor | Hover over the bar at the bottom | Space |
| Switch cameras | Click the map buttons | ← / → while the monitor is up |
| Pause | | Esc |

Tips:

- **Bonnie** comes down the west (left) hall, **Chica** down the east (right)
  hall. They stand right outside your doors, where no camera can see them,
  so check the door lights, especially when you hear footsteps.
- If your door buttons suddenly stop working, someone is already inside.
- **Foxy** creeps out of Pirate Cove (CAM 1C) when you don't check the cameras.
  If the cove is empty, close the left door, fast.
- **Freddy** moves from night 3 on, mostly in the dark, and laughs when he does.
  He can't move while you're watching him on camera.
- Glance at CAM 2B now and then. Or don't.

Beat night 5 to unlock night 6, and night 6 to unlock the custom night. Each
gives you a star on the menu, and so does beating 4/20 mode (everyone at 20).
Progress is saved to `save.json` next to `main.py`.

## Code layout

```
main.py            entry point and command-line flags
fnaf/
  settings.py      tunable constants (night length, power drain, ...)
  logic.py         the night simulation: time, power, animatronic AI (no pygame)
  scenes.py        menu, night, jumpscares, 6 AM, game over, custom night...
  hud.py           clock, power meter, camera map
  game.py          window, asset generation, save file, main loop
  characters.py    procedural animatronic art
  office.py        the office panorama, doors, lights, buttons
  rooms.py         the camera feeds
  audio.py         procedural sound synthesis
  util.py          drawing helpers (Pen, lighting, static, text)
tools/
  balance_sim.py   headless bot that plays nights to check difficulty
```

To make the game easier or harder, tweak `HOUR_SECONDS`, `DRAIN_PER_USAGE`
and `NIGHT_PASSIVE_DRAIN` in `fnaf/settings.py`, or the per-night AI levels in
`NIGHT_AI` in `fnaf/logic.py`, then check the result with
`python tools/balance_sim.py`.
