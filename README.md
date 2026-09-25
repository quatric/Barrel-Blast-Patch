# Barrel Blast Patch

Play the Wii release of **Donkey Kong Barrel Blast** (`RDKE01`, USA) with a
**GameCube controller**, a set of **DK Bongos**, or a **Classic Controller** —
instead of shaking a Wii Remote and Nunchuk.

The patch is applied to your own copy of the game: drop a clean `.wbfs` or
`.iso` onto the patcher and play the result on a Wii (USB loader) or in
Dolphin. Nothing from the game is included in this repository.

## Status — v1.2

Tested on a real Wii with a USB loader.

**Works:**

- GameCube pad: left/right drums, both-drums jump, steering, menus
- DK Bongos: left/right drums and both drums to jump (rim hits and claps no
  longer steer you right)
- presses registering reliably, and unplugging/replugging a GameCube pad
  at the title screen or in menus
- a GameCube pad driving a player that has no Wii Remote (players 1 and 2)
- Classic Controller: buttons, pointer, left and right drums
- the HOME Menu (Wii Remote or Classic Controller), with no accidental HOME
  Menu when a Classic Controller is plugged in

**Known issues:**

- player 1's pointer may not respond until the HOME Menu has been opened once
- player 2 occasionally misses a shake
- on a Classic Controller, ZL also counts as a left-drum hit
- a GameCube pad unplugged and replugged mid-race doesn't come back until
  you're back at a menu
- relaunching the game without powering the console off can leave GameCube
  pads unresponsive — power-cycle between sessions
- players 3 and 4 are untested

## Controls

### GameCube controller

| Input | Action |
| --- | --- |
| Y, R, or the R trigger | Right drum |
| X, L, or the L trigger | Left drum |
| Z (or both drums together) | Both drums — jump / boost |
| Control stick | Steer / move |
| C-stick | Pointer |
| A / B | Wii Remote A / B |
| Start | Wii Remote + |
| D-pad | D-pad |

### DK Bongos

The bongos plug into a GameCube port and are detected automatically (they
have no sticks).

| Input | Action |
| --- | --- |
| Left drum | Left drum, and Wii Remote B |
| Right drum | Right drum, and Wii Remote A (attack / menu select) |
| Both drums together | Jump / boost |
| Start | Pause |
| Clap | Nothing |

There's no steering on bongos alone, just like the original game, which pairs
bongo hits with Wii Remote tilt.

### Classic Controller

| Input | Action |
| --- | --- |
| Y, R, ZR, or the R trigger | Right drum |
| X, L, ZL, or the L trigger | Left drum |
| ZR (both drums) | Jump / boost |
| Left stick | Steer / move |
| Right stick | Pointer |
| A / B, D-pad, + / −, HOME | Same as the Wii Remote |

A Wii Remote must still be connected for a Classic Controller (it plugs into
the remote). A GameCube pad can play without one.

## Installing

### Patch your disc image

You need a clean **USA** (`RDKE01`) `.wbfs` or `.iso` and
[Wiimms ISO Tool](https://wit.wiimm.de/) (`wit`) on your `PATH`.

```bash
python3 tools/gui.py
```

Drop the image onto the window (or click to choose it). The patcher checks the
disc ID, patches `sys/main.dol`, rebuilds the image in the same format, and
replaces your file, keeping the original next to it as `<name>.bak`. Other
regions, and images that are already patched, are refused rather than
corrupted.

### Play on a Wii

Copy the patched image to your USB loader's drive as usual
(`wbfs/<Title> [RDKE01]/RDKE01.wbfs`). In the loader's settings for this game,
turn the **debugger, hook type and cheats off**: the loader's cheat code
handler loads into the same memory as the patch and black-screens the game.

### Play in Dolphin

Either boot the patched image, or use the Gecko codes instead: copy
`codes/RDKE01.ini` to Dolphin's `GameSettings` folder, enable the code under
**Properties → Gecko Codes**, and set GameCube Port 1 to your controller or
adapter before booting. The Gecko codes don't include the Classic Controller
left drum, which only exists in patched images.

## Building from source

The patcher needs only Python 3 and `wit`. The one piece of C
(`src/cc_nunchuk.c`) ships prebuilt in `tools/prebuilt/`, checked against its
source hash. With [devkitPPC](https://devkitpro.org/) installed it's rebuilt
automatically. To patch a `main.dol` directly:

```bash
python3 tools/inject_dol.py <retail main.dol> <patched main.dol>
```

How the patch works, and the long investigation behind it, is in
[docs/TECHNICAL.md](docs/TECHNICAL.md).

## Credits

- Inspired by Vague Rant's Classic Controller hacks for *Donkey Kong Jungle
  Beat*, whose approach (letting the game see Nunchuk input from a Classic
  Controller) the Classic Controller left drum follows.
- Gecko code format and code handler by the Gecko / WiiRD community.

## Contact

quatricsoftware@gmail.com

No support will be provided for this tool.

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 quatric
