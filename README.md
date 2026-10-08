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

Python 3.8+ and pygame 2.1+ (or pygame-ce). Startup takes a few seconds
while the art and sounds are generated.

Useful flags:

| Flag | What it does |
| --- | --- |
| `--night N` | Skip the menu and start night N (1-6) |
| `--custom F B C X` | Custom night with AI levels for Freddy, Bonnie, Chica, Foxy (0-20) |
| `--speed 4` | Make the night clock run 4x faster (for testing) |
| `--fullscreen` | Start fullscreen (F11 toggles at any time) |
| `--mute` | No sound |

### Sending it to a friend

The easiest way: zip the folder and have them run the same two commands above.

If they don't have Python, build a standalone app **on the same OS they use**
(PyInstaller can't cross-compile, so build a Windows `.exe` on Windows):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name FNAF main.py
```

Send them `dist/FNAF.exe` (or `dist/FNAF` on macOS/Linux). Their saves go in a
`save.json` next to the executable.

## How to play

Survive from 12 AM to 6 AM (about 9 real minutes per night, like the
original) without letting the animatronics reach you. Doors, lights and the
camera all use power, and when the power runs out the doors stop working...

| Action | Mouse | Keyboard |
| --- | --- | --- |
| Look around the office | Move to the screen edges | ← / → |
| Left door / left light | Click the panel buttons left of the door | Q / A |
| Right door / right light | Click the panel buttons right of the door | E / D |
| Raise / lower the camera monitor | Hover over the bar at the bottom | Space |
| Switch cameras | Click the map buttons | ← / → while the monitor is up |
| Pause / quit to menu | | Esc, then Q twice |
| Fullscreen | | F11 |

On the custom night screen, ← / → pick an animatronic, ↑ / ↓ change its level
and Enter starts the night.

Tips:

- **Bonnie** comes down the west (left) hall, **Chica** down the east (right)
  hall. They stand right outside your office, where no camera can see them,
  so check the door lights, especially when you hear footsteps. Bonnie shows
  up in the left doorway; Chica peers through the window on the right, so you
  can still see her with the right door shut.
- If your door buttons suddenly stop working, someone is already inside.
  Whatever you do, don't touch the monitor.
- **Foxy** creeps out of Pirate Cove (CAM 1C) when you don't check the cameras.
  If the cove is empty, close the left door, fast.
- **Freddy** moves from night 3 on, only while your monitor is down, and laughs
  when he does. Watching him on camera stalls him. Once he's in the east hall
  corner (CAM 4B), close the right door before you look at any other camera.
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
  screens.py       newspaper, paychecks, Game Over art
  audio.py         procedural sound synthesis (runs in a helper process at startup)
  effects.py       office panorama warp, CRT glitches, monitor flip
  util.py          drawing helpers (Pen, lighting, static, text)
tools/
  balance_sim.py   headless bot that plays nights to check difficulty
tests/
  test_logic.py    rules of the night simulation (python tests/test_logic.py)
```

The AI follows the original game's reverse-engineered rules (movement
opportunities every few seconds, d20 rolls against each animatronic's AI
level, Freddy's countdown, Foxy's lock timer, the per-night power drain).
To make the game easier or harder, tweak `HOUR_SECONDS`, `DRAIN_PER_USAGE`
and `NIGHT_PASSIVE_DRAIN` in `fnaf/settings.py`, or the per-night AI levels in
`NIGHT_AI` in `fnaf/logic.py`, then check the result with
`python tools/balance_sim.py`.
