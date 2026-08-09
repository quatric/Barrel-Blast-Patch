"""Build the DKBB GameCube/Bongos controller patch (RDKE01, USA) in two forms:
     1. Gecko code text (Dolphin .ini / .gct source)
     2. a static main.dol patch (writes straight into the baked C2 code body --
        the DOL's own codehandler-installer applies the hook at runtime, same
        as it already does for the codeA-D hooks; no codecave/trampoline needed)

Two poller variants, chosen at patch time -- see README for the tradeoff:

  autopoll (default): programs SI hardware auto-polling (SICnOUTBUF + SIPOLL
    enable bits, all four channels) so the console's own SI logic fills
    SICnINBUFH/L each frame, and acknowledges latched SI error status so a
    replugged controller recovers. Zero changes to codeA-D -- only the
    poller body changes. See src/poller_autopoll.s.

  stash: doesn't touch SIPOLL at all. Issues an SI immediate transfer and
    stashes the response (read from the I/O buffer at 0xCD006480, which
    Dolphin and real hardware implement identically) into codeD's unused
    leading pad words at 0x800027A4/0x800027A8, then repoints codeA-D's
    reads there instead of the hardware-only SIC0INBUFH/L registers.
    Needs 10 extra word patches inside codeA-D.

Both were sized to exactly 27 words + 1 spare (28 total, NN=0xE) so either
drops into the poller's C2 slot without shifting anything after it -- that
matters because the injected code list fills its segment with zero slack.

The root bug this fixes: SIC0INBUFH/L are hardware-written auto-poll result
registers. Software stores to them (what the original v24 poller did) are
silently ignored on real silicon -- Dolphin merely doesn't model that, which
is why the original patches worked in an emulator and nowhere else.
"""
import struct

# ------------------------------------------------------------- poller bodies
# Each is exactly 27 assembled words; a 28th all-zero spare word is appended
# at build time -- the codehandler overwrites that slot with the branch back
# to the original instruction, same convention as every other Gecko C2 code.
POLLER_HOOK = 0x80247adc  # original site: si::__SITransfer's poll-request path
POLLER_BODY_RAM = 0x800022b8  # where the poller's C2 body lives in this DOL

# assembled from src/poller_autopoll.s -- keep the two in step
POLLER_AUTOPOLL = [
    0x9421FFE0, 0x9001001C, 0x90610018, 0x90810014, 0x3C60CD00, 0x80836438,
    0x3C000F0F, 0x60000F0F, 0x7C840038, 0x90836438, 0x3C000040, 0x60000300,
    0x90036400, 0x9003640C, 0x90036418, 0x90036424, 0x80036430, 0x7004FF00,
    0x40820008, 0x60000100, 0x600000FF, 0x90036430, 0x8001001C, 0x80610018,
    0x80810014, 0x38210020, 0x9421FF40,
]

POLLER_STASH = [
    0x9421FFE0, 0x9001001C, 0x90610018, 0x90810014, 0x90A10010, 0x90C1000C,
    0x3C60CD00, 0x80036434, 0x70000001, 0x4082002C, 0x80836480, 0x80A36484,
    0x3CC08000, 0x908627A4, 0x90A627A8, 0x3C004003, 0x90036480, 0x3C008003,
    0x60000801, 0x90036434, 0x8001001C, 0x80610018, 0x80810014, 0x80A10010,
    0x80C1000C, 0x38210020, 0x9421FF40,
]

# ---------------------------------------------------- codeA-D hook rewrites
# Only needed for the stash variant. (addr, old_word, new_word) -- old_word
# is asserted against before writing, so a mismatched base DOL fails loudly
# instead of silently corrupting an unrelated build.
STASH_HOOK_PATCHES = [
    (0x800023c0, 0x3D60CD00, 0x3D608000),  # codeA: SI base -> stash page
    (0x800023c8, 0x398A6404, 0x398A27A4),  # codeA: INBUFH -> stash0
    (0x800024e4, 0x3CA0CD00, 0x3CA08000),  # codeB: SI base -> stash page
    (0x800024e8, 0x398A6404, 0x398A27A4),  # codeB: INBUFH -> stash0
    (0x80002500, 0x398A6408, 0x398A27A8),  # codeB: INBUFL -> stash1
    (0x8000271c, 0x3CC0CD00, 0x3CC08000),  # codeC: SI base -> stash page
    (0x80002720, 0x39496404, 0x394927A4),  # codeC: INBUFH -> stash0
    (0x800028a8, 0x3CA0CD00, 0x3CA08000),  # codeD: SI base -> stash page
    (0x800028ac, 0x398A6404, 0x398A27A4),  # codeD: INBUFH -> stash0
    (0x800028c4, 0x398A6408, 0x398A27A8),  # codeD: INBUFL -> stash1
]

VARIANTS = {
    'autopoll': POLLER_AUTOPOLL,
    'stash': POLLER_STASH,
}


def poller_gecko_lines(variant):
    words = list(VARIANTS[variant]) + [0x60000000]
    assert len(words) == 28
    nn = len(words) // 2
    out = [f'C2{POLLER_HOOK & 0x01FFFFFF:06X} {nn:08X}']
    for i in range(0, len(words), 2):
        out.append(f'{words[i]:08X} {words[i + 1]:08X}')
    return out


def static_patches(variant):
    """(vaddr, bytes) list for a direct main.dol patch."""
    words = list(VARIANTS[variant]) + [0x60000000]
    assert len(words) == 28
    patches = [(POLLER_BODY_RAM, b''.join(struct.pack('>I', w) for w in words))]
    if variant == 'stash':
        for addr, _old, new in STASH_HOOK_PATCHES:
            patches.append((addr, struct.pack('>I', new)))
    return patches


def verify_patches(dol, variant):
    """Check every static_patches() write against its expected pre-image.
    dol is a dol.Dol. Raises AssertionError with the offending address if the
    base DOL doesn't match what these offsets were computed against."""
    poller_pre = dol.read(POLLER_BODY_RAM, 4)
    if poller_pre is None:
        raise AssertionError(f'poller body address 0x{POLLER_BODY_RAM:08X} not mapped in this DOL')
    if variant == 'stash':
        for addr, old, _new in STASH_HOOK_PATCHES:
            cur = dol.read(addr, 4)
            if cur is None:
                raise AssertionError(f'hook patch address 0x{addr:08X} not mapped in this DOL')
            got = struct.unpack('>I', cur)[0]
            if got != old:
                raise AssertionError(
                    f'0x{addr:08X}: expected 0x{old:08X}, found 0x{got:08X} -- '
                    f'this is not the DOL these offsets were computed against')


if __name__ == '__main__':
    import sys
    variant = sys.argv[1] if len(sys.argv) > 1 else 'autopoll'
    print(f'* {variant} poller, hook 0x{POLLER_HOOK:08X}, body 0x{POLLER_BODY_RAM:08X}')
    print('\n'.join(poller_gecko_lines(variant)))
    p = static_patches(variant)
    print(f'\n* static patch: {len(p)} write(s)')
    for addr, data in p:
        print(f'  0x{addr:08X}: {len(data)} bytes')
