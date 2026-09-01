#!/usr/bin/env python3
"""Inject the DKBB controller hooks into a clean USA (RDKE01) main.dol.

Unlike the historical patcher, this does not require a Gecko codehandler or
an already-baked code list.  It appends the hook bodies as a normal DOL text
section and replaces each verified hook instruction with a direct branch.
"""
import os
import re
import struct

from dol import Dol

HERE = os.path.dirname(os.path.abspath(__file__))
INI = os.path.join(HERE, '..', 'codes', 'RDKE01.ini')
TEXT_ADDRESS = 0x80001800

HOOK_ORDER = [
    0x80247ADC,  # SI poller
    0x80248090,  # buttons
    0x80246588,  # acceleration / drums
    0x8024791C,  # stick
    0x80247500,  # IR pointer
    0x80247BE0,  # synthetic KPAD sample
]
HOOK_PREIMAGE = {
    0x80247ADC: 0x9421FF40,
    0x80248090: 0x70E09FFF,
    0x80246588: 0x80010044,
    0x8024791C: 0x7FC3F378,
    0x80247500: 0x83E1001C,
    0x80247BE0: 0x881F010F,
}
NUNCHUK_CHECK_COMPARES = {
    # CNunchakaCheck::update runs `cmplwi r0,1; bne <fail>` per connected
    # channel, requiring extension type == 1 (Nunchuk) exactly.  Relaxing
    # the immediate to 0 makes the gate "some extension is present" instead
    # (type 0 is the SDK's "no extension" value), which still accepts
    # Nunchuk(1), Classic(2), Bongo(5) and our synthetic GC device type(4)
    # but -- unlike unconditionally skipping the branch -- still REJECTS a
    # bare second Wii Remote with nothing attached. That distinction matters:
    # the earlier unconditional bypass made CNunchakaCheck treat any merely
    # *connected* channel as passing regardless of extension, so a second
    # Wii Remote with no Nunchuk/Classic attached was silently accepted and
    # then read as if it had valid Nunchuk data further downstream --
    # crashing mid-race or blackscreening at boot.
    0x8017998C: 0x28000001,
    0x80179F90: 0x28000001,
}


def branch(src, dst):
    off = dst - src
    if off & 3 or not -0x2000000 <= off < 0x2000000:
        raise ValueError(f'branch 0x{src:08X} -> 0x{dst:08X} is out of range/alignment')
    return 0x48000000 | (off & 0x03FFFFFC)


def parse_gecko_ini(path=INI):
    lines = open(path).read().splitlines()
    result, i = {}, 0
    while i < len(lines):
        match = re.fullmatch(r'C2([0-9A-Fa-f]{6})\s+([0-9A-Fa-f]{8})', lines[i].strip())
        if not match:
            i += 1
            continue
        hook = 0x80000000 | int(match.group(1), 16)
        pairs = int(match.group(2), 16)
        words = []
        for line in lines[i + 1:i + 1 + pairs]:
            fields = line.split()
            if len(fields) != 2:
                raise ValueError(f'malformed C2 body after {lines[i]}')
            words.extend(int(field, 16) for field in fields)
        result[hook] = words
        i += pairs + 1
    missing = [hook for hook in HOOK_ORDER if hook not in result]
    if missing:
        raise ValueError('missing required C2 hooks: ' + ', '.join(f'{x:#x}' for x in missing))
    return result


def repair_multiplayer(codeb):
    """Fix the two known channel-routing bugs in the shipped codeB body."""
    words = list(codeb)
    if words[23] == 0x60A50524:       # ori r5,r5,0x524 (wrong ch1 base)
        words[23] = 0x38A50524        # addi r5,r5,0x524
    elif words[23] != 0x38A50524:
        raise AssertionError(f'codeB channel-1 word changed: {words[23]:#010x}')

    # Channel 3 used to fall straight into the GC path and bypass the appended
    # Classic Controller check.  Give it the same two-instruction stub used by
    # channels 0..2, immediately before the reproduced hook instruction.
    if len(words) == 172:
        assert words[33] == 0x41820020 and words[41] == 0x38600003
        assert words[-2:] == [0x80010044, 0x60000000]
        stub = len(words) - 2
        words[33] = branch(33 * 4, stub * 4)  # relative indices work identically
        words[stub:stub] = [0x38600003, branch((stub + 1) * 4, 133 * 4)]
        words[132] = branch(132 * 4, 172 * 4)  # skip CC block to moved original
    elif len(words) != 174:
        raise AssertionError(f'unexpected codeB size {len(words)} words')
    # The Nunchuk acceleration path is processed less aggressively than the
    # Wii Remote path.  A six-frame left stroke (versus four on the right)
    # makes the physical L trigger/bongo side equally reliable without
    # changing thresholds or turning a single hit into a jump.
    if words[102] == 0x38800004:
        words[102] = 0x38800006
    elif words[102] != 0x38800006:
        raise AssertionError(f'codeB left-stroke duration changed: {words[102]:#010x}')
    return words


def repair_pointer(coded):
    """Keep Classic Controller IR reads away from the left-drum vector."""
    words = list(coded)
    replacements = {0xC03F0074: 0xC03F007C, 0xC05F0078: 0xC05F0080}
    found = {old: 0 for old in replacements}
    for index, word in enumerate(words):
        if word in replacements:
            found[word] += 1
            words[index] = replacements[word]
    if found != {0xC03F0074: 1, 0xC05F0078: 1}:
        raise AssertionError(f'unexpected codeD Classic stick loads: {found}')
    return words


def inject(src, dst):
    d = Dol(src)
    bodies = parse_gecko_ini()
    bodies[0x80246588] = repair_multiplayer(bodies[0x80246588])
    bodies[0x80247500] = repair_pointer(bodies[0x80247500])

    for hook in HOOK_ORDER:
        raw = d.read(hook, 4)
        if raw is None:
            raise AssertionError(f'hook address 0x{hook:08X} is not mapped (wrong DOL)')
        got = struct.unpack('>I', raw)[0]
        expected = HOOK_PREIMAGE[hook]
        if got != expected:
            raise AssertionError(
                f'hook 0x{hook:08X}: expected 0x{expected:08X}, found 0x{got:08X} '
                f'(wrong revision or already patched)')

    # Relax CNunchakaCheck's two extension-type comparisons from "==1" to
    # "!=0" (see NUNCHUK_CHECK_COMPARES). The branch instructions themselves
    # are left untouched -- only the immediate each compares against changes.
    for address, expected in NUNCHUK_CHECK_COMPARES.items():
        got = struct.unpack('>I', d.read(address, 4))[0]
        if got != expected:
            raise AssertionError(
                f'Nunchuk check compare 0x{address:08X}: expected 0x{expected:08X}, '
                f'found 0x{got:08X}')
        d.write(address, struct.pack('>I', expected & 0xFFFF0000))

    blob = bytearray()
    locations = {}
    for hook in HOOK_ORDER:
        while len(blob) & 0x1F:
            blob.extend(struct.pack('>I', 0x60000000))
        location = TEXT_ADDRESS + len(blob)
        words = list(bodies[hook])
        if words[-2] != HOOK_PREIMAGE[hook]:
            raise AssertionError(
                f'C2 {hook:#x} does not end with its reproduced hook instruction')
        words[-1] = branch(location + (len(words) - 1) * 4, hook + 4)
        locations[hook] = location
        blob.extend(struct.pack('>%dI' % len(words), *words))

    section = d.add_text_section(TEXT_ADDRESS, blob)
    for hook, location in locations.items():
        d.write(hook, struct.pack('>I', branch(hook, location)))
    d.save(dst)
    return section, locations, len(blob)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    args = ap.parse_args()
    section, locations, size = inject(args.src, args.dst)
    print(f'injected {len(locations)} hooks into text section {section} at '
          f'0x{TEXT_ADDRESS:08X} ({size} bytes)')
    for hook, body in locations.items():
        print(f'  0x{hook:08X} -> 0x{body:08X}')
