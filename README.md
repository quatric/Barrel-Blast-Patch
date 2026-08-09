# Donkey Kong Barrel Blast — Classic Controller, GameCube Controller & DK Bongos support

Gecko codes that let you play the Wii release of **Donkey Kong Barrel Blast**
(`RDKE01`, USA) with a **Classic Controller**, a **GameCube controller**, or a set
of **DK Bongos** plugged into the console's GameCube ports.

The Wii version of Barrel Blast is a motion-controlled game: everything is driven
by Wii Remote / Nunchuk shake detection, and the game has no GameCube input path
at all. These codes hook the KPAD library, poll the Serial Interface directly, and
synthesise the motion the game is looking for from GameCube button and stick state.

> **✅ Confirmed working on real hardware** (2026-08-09), after a long stretch
> where these codes worked in Dolphin and did nothing at all on console. The
> cause was the SI auto-polling gap described under "Reading the GameCube
> controller" below; the `autopoll` poller fixes it. Build a disc image with
> `tools/build_full_dol.py` (poller + Classic Controller baked into
> `main.dol` together).
>
> **Still a work in progress** — see "Known issues" for what's broken. The
> game still insists on a Wii Remote + Nunchuk being connected, the left
> drum is less responsive than the right, and the post-race "Press A to
> continue" tips screen doesn't accept input. Hot-plugging a GameCube
> controller used to stop it being recognised until reboot; that one has a
> fix in the poller now, but the fix itself has not been run on hardware
> yet.

## Classic Controller

Every hook now has a Classic Controller path. `tools/ccpatch.py` documents and
applies the three changes that got it there — read it before touching any of
this, since it edits the shipped code at the word level (the working sources
were lost; see [`src/README.md`](src/README.md)).

**Buttons.** codeA checks the KPAD extension type (`2` = Classic Controller)
and folds the Classic button word into the Wii Remote button bits (`r7`) the
game actually reads, before the composing instruction runs:

| Classic Controller | Reported as Wii Remote |
| --- | --- |
| A | A |
| B | B |
| D-pad Up / Down / Left / Right | D-pad Up / Down / Left / Right |
| + | + |
| − | − |
| HOME | HOME |

D-pad **Right** never actually worked before: the test was `andis. r0,r9,0x8000`
(i.e. `r9 & 0x80000000`), but `r9` is the Classic button word loaded by
`lwz r9,0x60(r31)`, which `KPADiRead` fills from a zero-extended `u16` — bit 31
is never set. Classic Right is `0x8000`, so the test has to be `andi.`.

**Drumming and jump.** codeB previously had no Classic branch at all. It now
gets one that synthesises the same two registers its existing GameCube drum
state machine already consumes — `r6` (the button word, as if it were SI
`in_hi >> 16`) and `r12` (the analog word, as if it were `in_lo`) — and then
falls into that shared code untouched. So the edge-trigger, the 4-frame
oscillation and the "both drums at once = jump" behaviour are all reused
verbatim rather than reimplemented:

| Classic Controller | In-game action |
| --- | --- |
| R or ZR, or the R analog trigger | Right drum |
| L or ZL, or the L analog trigger | Left drum |
| both together | Both drums — jump / boost |

The analog triggers are read as normalised floats from KPAD `+0x7c` (L) and
`+0x80` (R) and rescaled to the 0-255 range the GameCube path expects, so a
half-pull registers exactly like a GameCube hair-trigger. Those two offsets are
confirmed from the game's own stick reader (`zz_80247864_`), which fills them
from raw bytes `+0x34`/`+0x35` scaled between the `nDigitalLRBorder`-style
bounds.

**Sticks.** codeC's Classic branch was reading the wrong addresses entirely.
In codeC `r30` is the channel base **+ 0x60** — provable from its own
channel-detect constants, which compare `r30` against `0x803C9220` /
`0x803C9C68` / `0x803CA18C`, each exactly `base+0x60` — but the branch was
written as though `r30` were the bare base. The extension-type probe therefore
read `base+0xBC` instead of `base+0x5c`, the stick reads hit `base+0xCC`/`0xD0`
instead of `base+0x6c`/`0x70`, and the float scratch landed at `base+0x168`,
which is **inside the KPAD sample ring buffer** (it starts at `+0x110`). All of
it is rebased, and the scratch moved onto codeC's own stack frame.

**Motion isolation.** The moment the Classic Controller branch is entered, it
zeroes six per-axis accelerometer scale factors at KPAD `+0x4dc`–`+0x4f0` —
the same fields `zz_80245f98_` (this hook's containing function) multiplies
the raw Wii Remote/Nunchuk delta by every frame, before this code even runs.
Without it, physically moving the Wii Remote you're required to keep
connected (see "What is mapped") would add its own spurious drum hits on top
of the Classic Controller's.

## No Nunchuk Required (opt-in)

A second, independent Gecko code in the same file: seven call sites gate
per-extension setup on `*(byte*)(param+0x5c) != 0`, i.e. some extension being
physically connected. Patching each site's `cmpwi r0,0` to `cmpwi r0,-1`
(a value the byte can never hold) makes that branch always taken, so a
Classic Controller or GameCube pad works without a physical Nunchuk also
being plugged into the Wii Remote. Ships as its own `$`-titled block —
enable it separately from the main code in Dolphin's Gecko Codes list if you
need it. `tools/nonunchuk.py` verifies all seven pre-images against the DOL
before writing.

## What is mapped

GameCube controller / DK Bongos:

| Physical input | In-game action |
| --- | --- |
| A / X (right bongo) | Right drum hit |
| B / Y (left bongo) | Left drum hit |
| R / Z (clap) | Both drums — jump / boost |
| L, R analog triggers | Left / right drum (hair-trigger, threshold ≈ 48) |
| Control stick | Nunchuk stick (character movement), with a ±16 deadzone |
| C-stick | IR pointer position |
| Start, D-pad, face buttons | Wii Remote / Nunchuk button equivalents |

DK Bongos (TaruKonga) report over the Serial Interface as an ordinary
GameCube pad with each drum surface wired to a button, so they arrive
through the same code path and the same table above. Two things are
specific to them:

- **They are detected by their sticks.** A bongo set has no control stick or
  C-stick, so both C-stick axes read `0x00` where a real pad idles near
  `0x80`. `codeA` treats that as "these are bongos" and switches to a
  bongo-specific button map: `Z` → left drum, `R` → right drum. Everything
  else (`Start`, the D-pad) still passes through.
- **A set connected to the Wii through the Wii Remote** (extension type 5)
  is read from the extension's own button bits instead: bit 0 → left drum,
  bit 1 → right drum. No SI involved.

| Bongo surface | In-game action |
| --- | --- |
| Left drum | Left drum hit |
| Right drum | Right drum hit |
| Both drums together | Jump / boost |
| Start/Pause | Wii Remote `+` |

Steering is unavailable on bongos alone — they have no stick, exactly as on
the retail game, which pairs bongo hits with Wii Remote tilt.

Player 1 should still have a real Wii Remote + Nunchuk connected — the game gates
on a connected controller before it will start a race. The GameCube pad is an
*alternate* whose input is injected on top.

## Usage

### Dolphin

Copy `codes/RDKE01.ini` to your Dolphin `GameSettings` folder:

- macOS: `~/Library/Application Support/Dolphin/GameSettings/`
- Windows: `%USERPROFILE%\Documents\Dolphin Emulator\GameSettings\`
- Linux: `~/.config/dolphin-emu/GameSettings/`

Then enable the code under **Properties → Gecko Codes**, and set GameCube Port 1
to your adapter **before** booting. Avoid hot-plugging mid-game.

### Console

Load the codes with any Gecko-code loader (Riivolution patch, Gecko OS, a loader
with cheat support). Only the USA revision (`RDKE01`) is supported — the hook
addresses are hardcoded and will crash on other regions.

### Patcher tool

`tools/gui.py` bakes the codes directly into a copy of a disc image instead
of relying on a Gecko loader at runtime — drop a `.wbfs` or `.iso` on the
window (or a prebuilt binary from a [release](../../releases), which bundles
[Wiimms ISO Tool](https://wit.wiimm.de/) so you don't need to install it) and
it patches `sys/main.dol` in place, keeping the original alongside as
`<name>.bak`. Pick `autopoll` (default) or `stash` from the window before
dropping the image — see "Reading the GameCube controller" above for what the
two variants actually do differently.

**The tool never ships or touches anyone else's copy of the game** — it only
operates on a disc image you already have locally. `tools/build.py` is the
same patch logic as a standalone module (`static_patches()`/
`poller_gecko_lines()`) if you'd rather drive it from a script; it checks the
base DOL against the exact bytes these offsets were computed against before
writing anything, so a foreign or already-patched dump fails loudly instead
of silently corrupting.

**This is not yet a from-scratch injector.** `static_patches()` writes into a
fixed set of addresses (`POLLER_BODY_RAM` and, for `stash`, ten more inside
codeA-D) that only exist because those five Gecko codes were already baked
into the DOL by an earlier, undocumented process — the tool patches an
*already-modified* build further, it doesn't inject the codehandler system
into a genuinely clean retail dump. Point it at a clean `.wbfs` extracted
straight from your own disc and `verify_patches()` will correctly refuse:
`POLLER_BODY_RAM` isn't mapped there at all. Actually supporting a clean dump
needs a new DOL segment, a codehandler-installer stub, and hook-table entries
for codeA-D — none of that exists yet.

## Sources

`src/` holds the PowerPC assembly and a `build.py` that turns it into Gecko
codes — but **it does not build the codeA-D codes shipped here**. It is a
later revision in which GameCube detection regressed, and it is included only
because the sources for the working build were lost. Read
[`src/README.md`](src/README.md) before touching it.

`tools/build.py` is unrelated to `src/build.py` (same filename, different
directory, different purpose): it's where the fifth code — the SI poller
described below — is defined, both as Gecko code text and as a direct
`main.dol` patch. `codeA`-`codeD` are hand-maintained in `codes/RDKE01.ini`
directly (see the caveat above); only the poller is generated.

## How it works

Everything below refers to the USA `main.dol`.

### KPAD injection points

The game links an older Wii SDK where the button state is composed inside
`KPADiRead` itself:

| Hook | Address | Notes |
| --- | --- | --- |
| Button compose | `0x80248090` | `andi. r0, r7, 0x9fff`; `r7` = Wii Remote buttons, `r27` = channel. Both the Classic Controller remap and the GameCube button injection run here. |
| Nunchuk stick read | return `0x8024791c` | `r30` = channel base + 0x60; `r12` holds a converter pointer that **must** be preserved |
| Accelerometer read | return `0x80246588` | `r30` = channel base |
| DPD / IR read | `0x802470c0`, epilogue hook `0x80247500` | |

Player 1's KPAD channel base is `0x803C91C0`, with a `0x524` stride per player.
Persistent state for the drum edge-trigger state machines is kept at offset
`0x100` of the *active* channel's KPAD struct — offsets `0x100`–`0x110` are unused
by every Wii Remote extension (they all end at `0xC0`), so per-player state can
never clobber another player or fake a button press.

### Reading the GameCube controller

The Serial Interface on Wii/Hollywood is at **`0xCD006400`**, not `0xCC006400`
(that's the GameCube/Flipper address). Dolphin mirrors `0xCC` ↔ `0xCD`, which is
why an earlier revision of these codes worked in the emulator and did nothing at
all on console. The correct base is confirmed by the game's own linked SDK —
`SISetXY`, `__SITransfer` (`0x801f48ec`) and `SIInterruptHandler` (`0x801f4440`)
all use `0xCD006400`.

Relevant registers: `SIPOLL 0x6430`, `SICOMCSR 0x6434`, `SISR 0x6438`, channel 0
`INBUFH 0x6404` / `INBUFL 0x6408` (channel stride `0xC`), I/O buffer `0x6480`.

The second half of the console problem is that *nothing polls*. Barrel Blast links
the full `si::` library (`SIInit` runs at boot from `OSInit`) but **not** the `PAD`
library — there is no `PADInit`/`PADRead`, and auto-polling is never enabled. On
real hardware `INBUFH` therefore stays empty forever, the validity bit fails, and
injection is skipped. Dolphin hides this because it fills `INBUFH` itself in
`UpdateDevices()`.

The four `C2` codes only ever *read* `INBUFH`/`INBUFL`. Under Dolphin that's
enough, because the emulator fills those registers itself whenever software
writes to them. On real hardware it is not: **`SIC0INBUFH`/`SIC0INBUFL` are
hardware-written auto-poll result registers. Writes to them from software are
silently ignored on real silicon.** An earlier version of the fifth code tried
exactly that (copy the I/O buffer response into `INBUFH`/`INBUFL` by hand) —
it's a no-op on console, which is why "works in Dolphin, does nothing on
hardware" was the exact symptom this project got stuck on. Dolphin doesn't
model that restriction, so the divergence only ever showed up on real
hardware.

The fifth code included here (`build.py`'s `autopoll` variant, hooking
`KPADiRead`'s entry at `0x80247ADC`, which runs once per channel per frame
before all three read hooks since they're `bl`-called from inside it) takes
the other path instead — it drives the SI hardware's own auto-polling logic
so the console fills `INBUFH`/`INBUFL` itself, the same way `PADRead` would if
this game linked the `PAD` library:

1. Acknowledge every channel's latched error status in `SISR` (`0x6438`) —
   see "Hot-plugging" below.
2. Write poll command `0x00400300` to each channel's output buffer
   (`SIC0OUTBUF` `0x6400`, stride `0xC`).
3. Read `SIPOLL` (`0x6430`); `SIInit` already programmed the X (rate) field,
   so this only needs to force the Y field to 1 poll/frame if it's unset.
4. OR in the enable and copy-on-vblank bits for all four channels (`0xFF`)
   and write it back to `SIPOLL`.

That's it — no `SICOMCSR` kick, no `TSTART` polling, no interrupt handling.
The hardware's own auto-poll state machine (the same one `SIInit`/`SIProbe`
already exercise for the boot-time `SIGetType` probes) takes it from there
every VBlank.

`SIPOLL`'s low byte is the per-channel control: bits 7–4 are the poll enable
for channels 0–3, bits 3–0 the matching copy-on-vblank bits. `SISetXY`
(`0x801f4a9c`) rewrites only X and Y and preserves that byte, so nothing in
the game ever clears what the poller sets.

#### Hot-plugging

Each channel gets a byte of `SISR` (`0x6438`), channel 0 in the high byte.
The low nibble of each byte is the latched error status — `NOREP`, `COLL`,
`OVRUN`, `UNRUN` — and it is **write-1-to-clear**; bits 5 and 4 above it are
the read-only `RDST`/`WRST` status. Unplugging a controller latches `NOREP`,
and until something acknowledges it, `ERRSTAT` stays set in `INBUFH`, every
hook's error check skips injection, and replugging the pad never brings it
back.

The poller therefore acknowledges the error nibbles every frame before it
re-arms polling: read `SISR`, mask to `0x0F0F0F0F`, write it back. That's the
game's own SI library idiom — `SIInterruptHandler` does it a channel at a
time at `0x801f4574`–`0x801f458c` (`lis 0x0F00`, `sraw` by `chan*8`, `and`,
store back). Writing a 0 to a write-1-to-clear bit leaves it alone, so
masking to one channel's nibble is how the SDK avoids acknowledging another
channel's error by accident; the poller masks to all four because it polls
all four.

A second variant (`build.py`'s `stash`) is included as a fallback in case the
`SIPOLL` enable-bit assumption above turns out wrong on real hardware: it
issues one SI immediate transfer per frame and stashes the response — read
from the I/O buffer at `0xCD006480`, which Dolphin and real hardware
implement identically for immediate transfers — into unused RAM (codeD's own
leading pad words) instead of trying to write `INBUFH`/`INBUFL`, then
repoints the four read hooks there. See `tools/gui.py` to try either one.

### Input decoding

`INBUFH` for a valid GameCube response:

```
byte0 = [ERRSTAT, 1, 0, Start, Y, X, B, A]
byte1 = [1, L, R, Z, DUp, DDown, DRight, DLeft]
byte2 = Stick X (0-255, centre 128)
byte3 = Stick Y
```

Byte 1 bit 7 (`INBUFH` bit 23) is always 1 in a valid response, so
`andis. r0, rX, 0x0080` is used as a presence check — when a pad is unplugged or
mid-hot-plug the whole word reads 0, the check fails, and injection is skipped so
the real Wii Remote takes over cleanly.

### Faking the motion

Barrel Blast does not look at absolute accelerometer magnitude — it looks at
**frame-to-frame deltas** across the three axes. A static injected vector reads as
"no motion". Each drum therefore oscillates its axis between `+10.0f` and
`-10.0f` every frame while active, which produces a delta far above the game's
shake threshold.

Drum hits are edge-triggered: a released→pressed transition arms a 4-frame
oscillation and then stops until the button is released again. Without this the
game reads a continuous shake and the karts accelerate without limit; with it you
have to physically alternate L/R hits to build speed, exactly like real bongos.

Jump is the exception. It is **angle**-based, not magnitude-based (parameters
`nBuraJumpAngleWM` / `L` / `R`, cached at config `+0x2C8` / `+0x2C0` / `+0x2C4`),
so faking it needs a genuine upward acceleration *vector* across `+0x0c/+0x10/+0x14`
and `+0x68/+0x6c/+0x70`. The current values are a placeholder — this is the main
piece of unfinished work.

All three hooks allocate proper stack frames and save/restore the volatile
registers (`r0`, `r3`–`r12`, `f0`–`f6`) the game's own compiled code is using for
locals. Skipping that corrupts the caller's frame and crashes.

## Known issues

Found on hardware, in rough priority order:

- **Hot-plugging a GameCube controller breaks it** — *fix written, not yet
  confirmed on hardware.* Unplug and replug and the console stopped
  recognising the pad until the game was restarted. The cause is SI error
  latching: `NOREP` sticks in `SISR` and nothing acknowledged it, so
  `ERRSTAT` stayed set in `INBUFH` and every hook skipped injection forever.
  The poller now clears the error nibbles each frame — see "Hot-plugging".
  If replugging still fails after this, the next suspect is that the port
  also needs a `SIGetType`-style re-probe, not just an error acknowledgement.
- **A Wii Remote + Nunchuk is still required.** The real blocker is upstream
  of the extension check: `KPADiRead` early-outs at `0x80247BE0` when the
  channel's queued-sample count at KPAD `+0x10F` is zero, which is exactly
  the case with no Wii Remote connected — so none of the four hooks ever run
  and there is nothing to inject into. Samples are a 16-entry ring of `0x38`
  bytes at KPAD `+0x110`, with the write index at `+0x10E` and the count at
  `+0x10F`, filled by the WPAD callback at `0x802485E0`.

  So making a bare GameCube pad drive player *N* means **synthesising a KPAD
  sample**: the poller hook at `0x80247ADC` runs before that early-out, so it
  can write a zeroed sample into the ring and set the count to 1 whenever
  SI reports a valid pad on the channel and no real sample arrived. The
  hooks downstream already overwrite buttons, motion and stick from SI, so
  the synthetic sample's contents barely matter — but its device-type byte
  does (see the next item). Untried.

  The "No Nunchuk Required" opt-in code below is a blunter, narrower thing:
  it only forces the seven `*(byte*)(x+0x5c) != 0` extension-present checks
  to pass, which does not help if `KPADiRead` bailed before reaching them.
- **The left drum is less responsive than the right.** Not a threshold
  problem — the two trigger paths really are symmetric in `codeA` (same
  `> 40` compare on both analog bytes, same digital masks). The asymmetry is
  the *destination*: the right drum writes the Wii Remote motion vector at
  `+0x4DC`–`+0x4E4` and the left drum writes the Nunchuk one at
  `+0x4E8`–`+0x4F0`, and only the second is gated. `read_kpad_acc` processes
  the Wii Remote vector unconditionally, but reaches the Nunchuk vector at
  `0x802462EC` only if the sample's device-type byte at `+0x36` is **4 or 5**
  (checked at `0x802462D4`) — anything else branches straight to the
  function's exit at `0x80246588` and the left drum's motion is simply never
  read. That byte is written from the WPAD probe result at `0x802485F8`, so
  it is a Wii-side device type, not something the GameCube pad influences.
  Forcing it to 4 when a GameCube pad is present should make both drums
  equally responsive, and is the same lever the synthetic-sample work above
  needs. Untried.
- **The post-race tips screen ("Press A to continue") doesn't accept input.**
  Finishing a Grand Prix stage lands on it and nothing gets past it. That
  screen presumably reads input through a path the KPAD button-compose hook
  doesn't cover.
- The Classic Controller left drum writes the "nunchuk shake" magnitude at
  KPAD `+0x74`/`+0x78`, which for a Classic Controller is where codeD reads
  the **right stick** for the IR pointer. So the pointer jerks for the ~4
  frames of each left-drum hit. Harmless during a race (the pointer is unused
  there) and you are not drumming in menus, but it is real. Fixing it properly
  needs codeC to snapshot the right stick before codeB clobbers it.
- Jump uses a placeholder acceleration vector; real captured values are needed.
  On a Classic Controller jump is reached by hitting both drums at once, so it
  inherits that same limitation.
- 4-player support exists as a variant that round-robins the SI channel each frame,
  but is not included here pending single-player console verification. Note that
  channel 1 detection is broken in codeB/codeC/codeD regardless: they build the
  channel-1 base with `ori rX,rX,0x524` where an **add** was meant, and
  `0x803C91C0 | 0x524` is `0x803C95E4`, not `0x803C96E4`. Channels 0, 2 and 3
  use correct absolute constants. Channel 3 additionally reaches codeB's shared
  drum logic by fallthrough rather than through a branch, so it is GameCube-only
  — the Classic Controller check is not on that path.
- USA (`RDKE01`) only.
- The `autopoll` poller's hook installs correctly and runs without crashing in
  Dolphin (checked with a GDB-stub debugger against the compiled code, not
  just read from source), but it has **not** been confirmed to actually fire
  during gameplay in Dolphin or on real hardware yet, and the `SIPOLL` enable-
  bit values it writes (`EN0`/`VBCPY0`) are inferred from the documented
  register layout, not independently confirmed against this game's linked SDK
  the way the SI base address and register offsets were. If it doesn't work
  on your console, try the `stash` variant, which sidesteps that assumption
  entirely.

## Credits

- Inspired by Vague Rant's Classic Controller hacks for *Donkey Kong Jungle Beat*.
- Gecko code format and code handler by the Gecko / WiiRD community.

## Contact

quatricsoftware@gmail.com

No support will be provided for this tool.

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 quatric
