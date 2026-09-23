#!/usr/bin/env python3
"""Inject the DKBB controller hooks into a clean USA (RDKE01) main.dol.

Unlike the historical patcher, this does not require a Gecko codehandler or
an already-baked code list.  It appends the hook bodies as a normal DOL text
section and replaces each verified hook instruction with a direct branch.
"""
import os
import re
import struct
import subprocess
import tempfile

from dol import Dol

HERE = os.path.dirname(os.path.abspath(__file__))
INI = os.path.join(HERE, '..', 'codes', 'RDKE01.ini')
# Not 0x80001800: the first word there is overwritten at runtime shortly
# after boot (seen in Dolphin; not a CPU store -- a write watchpoint on it
# never fires), which replaced the SI poller's opening `stwu` and crashed
# the game into a black screen. Every other word of the section survives, so
# starting 32 bytes in keeps the whole injected section clear of it.
TEXT_ADDRESS = 0x80001820
# The first SCRATCH_BYTES of the section are zeroed data, not code:
#   +0x00..+0x0F  SI poller: per-channel last-probe time base
#   +0x10         gecko_log.c: last log line time base
#   +0x14..+0x17  SI poller: per-channel consecutive-NOREP counters
#   +0x18..+0x1B  codeE: per-channel "last KPAD sample was synthesised" flag
SCRATCH_BYTES = 0x20
# The section must end before the OS's low-memory globals at 0x80003000
# (IPC, boot info, reset state); running into them blackscreens at boot.
TEXT_LIMIT = 0x80003000

# Hooks that stand down while the HOME Menu is open, so it sees exactly what
# the real Wii Remote reports: every hook except the SI poller (which has to
# keep the pad's polling alive). The IR pointer and buttons were the obvious
# ones; codeE's "Nunchuk-class" marking of queued Wii Remote samples also has
# to go, or the HOME Menu sees an extension being plugged in and pulled out
# every read (a flickering pointer and the extension sound, seen on
# hardware). CHomeButtonMenu is a singleton
# allocated once at boot at a fixed heap address; its byte +0x32 is 1 while
# the menu is open. The vtable word is checked first so a different heap
# layout just means the gate never triggers.
HBM_GATED = (0x80248090, 0x80246588, 0x8024791C, 0x80247500, 0x80247BE0, 0x80247FA8)
HBM_OBJECT = 0x80531B80
HBM_VTABLE = 0x802E7288
HBM_OPEN_FLAG = HBM_OBJECT + 0x32

HOOK_ORDER = [
    0x80247ADC,  # SI poller
    0x80248090,  # buttons
    0x80246588,  # acceleration / drums
    0x8024791C,  # stick
    0x80247500,  # IR pointer
    0x80247BE0,  # synthetic KPAD sample
    0x80247FA8,  # neutralize Wii Remote motion while a pad/CC is active
]
HOOK_PREIMAGE = {
    0x80247ADC: 0x9421FF40,
    0x80248090: 0x70E09FFF,
    0x80246588: 0x80010044,
    0x8024791C: 0x7FC3F378,
    0x80247500: 0x83E1001C,
    0x80247BE0: 0x881F010F,
    0x80247FA8: 0x1C1E0084,
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


def hbm_gate(location, body_location, hook, preimage):
    """Stub: if the HOME Menu is open, run the original instruction and
    return to the game; otherwise fall through to the hook body."""
    hi = lambda v: ((v >> 16) + (1 if v & 0x8000 else 0)) & 0xFFFF
    lo = lambda v: v & 0xFFFF
    words = [
        0x9421FFF0,                                   # stwu  r1,-0x10(r1)
        0x91610008,                                   # stw   r11,8(r1)
        0x9181000C,                                   # stw   r12,0xc(r1)
        0x7D800026,                                   # mfcr  r12
        0x91810004,                                   # stw   r12,4(r1)
        0x3D600000 | hi(HBM_OBJECT),                  # lis   r11,obj@ha
        0x818B0000 | lo(HBM_OBJECT),                  # lwz   r12,obj@l(r11)
        0x3D600000 | (HBM_VTABLE >> 16),              # lis   r11,vt@h
        0x616B0000 | (HBM_VTABLE & 0xFFFF),           # ori   r11,r11,vt@l
        0x7C0C5800,                                   # cmpw  r12,r11
        0x40820014,                                   # bne   +0x14 -> run
        0x3D600000 | hi(HBM_OPEN_FLAG),               # lis   r11,flag@ha
        0x898B0000 | lo(HBM_OPEN_FLAG),               # lbz   r12,flag@l(r11)
        0x2C0C0000,                                   # cmpwi r12,0
        0x4082001C,                                   # bne   +0x1c -> skip
        # run:
        0x81810004,                                   # lwz   r12,4(r1)
        0x7D8FF120,                                   # mtcr  r12
        0x81610008,                                   # lwz   r11,8(r1)
        0x8181000C,                                   # lwz   r12,0xc(r1)
        0x38210010,                                   # addi  r1,r1,0x10
        0,                                            # b body
        # skip:
        0x81810004,                                   # lwz   r12,4(r1)
        0x7D8FF120,                                   # mtcr  r12
        0x81610008,                                   # lwz   r11,8(r1)
        0x8181000C,                                   # lwz   r12,0xc(r1)
        0x38210010,                                   # addi  r1,r1,0x10
        preimage,                                     # original instruction
        0,                                            # b hook+4
    ]
    words[20] = branch(location + 20 * 4, body_location)
    words[27] = branch(location + 27 * 4, hook + 4)
    return words


LOG_HOOK = 0x80247ADC      # the logger stub runs first at KPADRead's entry
# __OSUnhandledException(type, context, dsisr, dar): with --log, a stub here
# sends the crash essentials over the Gecko before the OS's own dump, which
# goes nowhere on retail hardware.
CRASH_HOOK = 0x801C0B34
CRASH_PREIMAGE = 0x9421FFD0
DEVKITPPC = os.environ.get('DEVKITPPC', '/opt/devkitpro/devkitPPC') + '/bin/'


def _words(path):
    data = open(path, 'rb').read()
    return list(struct.unpack('>%dI' % (len(data) // 4), data))


def build_logger():
    """Assemble the stub and compile src/gecko_log.c; returns
    (stub_words, logger_words, gecko_log_offset)."""
    src = os.path.join(HERE, '..', 'src')
    with tempfile.TemporaryDirectory() as tmp:
        o, b = os.path.join(tmp, 's.o'), os.path.join(tmp, 's.bin')
        subprocess.run([DEVKITPPC + 'powerpc-eabi-as', '-mbig', '-mgekko', '-o', o,
                        os.path.join(src, 'gecko_log_stub.s')], check=True)
        subprocess.run([DEVKITPPC + 'powerpc-eabi-objcopy', '-O', 'binary', o, b], check=True)
        stub = _words(b)
        subprocess.run([DEVKITPPC + 'powerpc-eabi-gcc', '-O2', '-mcpu=750', '-meabi',
                        '-msoft-float', '-mno-sdata', '-ffreestanding', '-fno-builtin',
                        '-fno-common', '-fno-asynchronous-unwind-tables', '-fno-exceptions',
                        '-c', os.path.join(src, 'gecko_log.c'), '-o', o],
                       check=True)
        relocs = subprocess.run([DEVKITPPC + 'powerpc-eabi-objdump', '-r', o],
                                capture_output=True, text=True, check=True).stdout
        if 'R_PPC' in relocs:
            raise AssertionError('gecko_log.c must compile without relocations:\n' + relocs)
        syms = subprocess.run([DEVKITPPC + 'powerpc-eabi-nm', o],
                              capture_output=True, text=True, check=True).stdout
        entry = int(next(l.split()[0] for l in syms.splitlines()
                         if l.endswith(' gecko_log')), 16)
        crash_entry = int(next(l.split()[0] for l in syms.splitlines()
                               if l.endswith(' gecko_crash')), 16)
        subprocess.run([DEVKITPPC + 'powerpc-eabi-objcopy', '-O', 'binary',
                        '-j', '.text', o, b], check=True)
        logger = _words(b)
    bl = [i for i, w in enumerate(stub) if w == 0x48000001]
    assert len(bl) == 1, 'stub must contain exactly one `bl .` placeholder'
    return stub, logger, entry, bl[0], crash_entry


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
    # Same channel-1 bug codeB had: `ori r5,r5,0x524` computes
    # 0x803C91C0 | 0x524 = 0x803C95E4, not channel 1's KPAD base 0x803C96E4,
    # so the pointer hook never matched player 2 (found from a USB Gecko
    # crash dump taken while codeD ran with r31 = 0x803C96E4).
    ch1 = [i for i, w in enumerate(words) if w == 0x60A50524]
    if len(ch1) != 1:
        raise AssertionError(f'unexpected codeD channel-1 base words: {ch1}')
    words[ch1[0]] = 0x38A50524        # addi r5,r5,0x524

    # codeD keeps each channel's pointer position in the 8 data words after
    # its leading `b code_start` (words 1-8), and finds them with
    # `bl get_pc; get_pc: mflr r11; addi r11,r11,-40`. That -40 matches the
    # source but not this body, where get_pc sits much further in: it
    # pointed r11 into codeD's own channel-detect code, so storing the
    # position (0.0 with the stick centred) overwrote its branches with
    # zeros -- the illegal-instruction crashes caught by the USB Gecko
    # crash dump, which showed those words zeroed in memory. Point it at
    # the real data words.
    assert words[0] >> 26 == 18, 'codeD must start with a branch over its data'
    bl = [i for i, w in enumerate(words) if w == 0x48000005]     # bl +4
    if len(bl) != 1 or words[bl[0] + 1] != 0x7D6802A6:            # mflr r11
        raise AssertionError(f'unexpected codeD get_pc sequence at {bl}')
    addi = bl[0] + 2
    if words[addi] >> 16 != 0x396B:                               # addi r11,r11,x
        raise AssertionError(f'codeD word {addi} is not addi r11,r11: {words[addi]:#010x}')
    offset = (1 - (bl[0] + 1)) * 4                               # get_pc -> word 1
    words[addi] = 0x396B0000 | (offset & 0xFFFF)
    return words


def inject(src, dst, log=False, log_only=False):
    d = Dol(src)
    bodies = parse_gecko_ini()
    bodies[0x80246588] = repair_multiplayer(bodies[0x80246588])
    bodies[0x80247500] = repair_pointer(bodies[0x80247500])

    order = [] if log_only else HOOK_ORDER
    for hook in order or [LOG_HOOK]:
        raw = d.read(hook, 4)
        if raw is None:
            raise AssertionError(f'hook address 0x{hook:08X} is not mapped (wrong DOL)')
        got = struct.unpack('>I', raw)[0]
        expected = HOOK_PREIMAGE[hook]
        if got != expected:
            raise AssertionError(
                f'hook 0x{hook:08X}: expected 0x{expected:08X}, found 0x{got:08X} '
                f'(wrong revision or already patched)')

    if log_only:
        return _inject_log_only(d, dst)

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

    blob = bytearray(SCRATCH_BYTES)
    locations = {}
    for hook in HOOK_ORDER:
        while len(blob) & 0x1F:
            blob.extend(struct.pack('>I', 0x60000000))
        entry = TEXT_ADDRESS + len(blob)
        if log and hook == LOG_HOOK:
            stub, logger, log_entry, bl_idx, crash_entry = build_logger()
            log_patch = (len(blob) // 4 + bl_idx, log_entry)
            blob.extend(struct.pack('>%dI' % len(stub), *stub))
        if hook in HBM_GATED:
            gate_len = 28 * 4
            body_location = entry + gate_len
            blob.extend(struct.pack('>28I', *hbm_gate(entry, body_location, hook,
                                                      HOOK_PREIMAGE[hook])))
        location = TEXT_ADDRESS + len(blob)
        words = list(bodies[hook])
        if words[-2] != HOOK_PREIMAGE[hook]:
            raise AssertionError(
                f'C2 {hook:#x} does not end with its reproduced hook instruction')
        words[-1] = branch(location + (len(words) - 1) * 4, hook + 4)
        locations[hook] = entry
        blob.extend(struct.pack('>%dI' % len(words), *words))

    if log:
        got = struct.unpack('>I', d.read(CRASH_HOOK, 4))[0]
        if got != CRASH_PREIMAGE:
            raise AssertionError(f'crash hook 0x{CRASH_HOOK:08X}: found 0x{got:08X}')
        while len(blob) & 0x1F:
            blob.extend(struct.pack('>I', 0x60000000))
        crash_at = TEXT_ADDRESS + len(blob)
        words = stub + [CRASH_PREIMAGE, 0]
        words[-1] = branch(crash_at + (len(words) - 1) * 4, CRASH_HOOK + 4)
        crash_bl = len(blob) // 4 + bl_idx
        blob.extend(struct.pack('>%dI' % len(words), *words))
        while len(blob) & 0x1F:
            blob.extend(struct.pack('>I', 0x60000000))
        logger_at = TEXT_ADDRESS + len(blob)
        blob.extend(struct.pack('>%dI' % len(logger), *logger))
        idx, entry = log_patch
        blob[idx * 4:idx * 4 + 4] = struct.pack(
            '>I', branch(TEXT_ADDRESS + idx * 4, logger_at + entry) | 1)
        blob[crash_bl * 4:crash_bl * 4 + 4] = struct.pack(
            '>I', branch(TEXT_ADDRESS + crash_bl * 4, logger_at + crash_entry) | 1)
        locations[CRASH_HOOK] = crash_at

    if TEXT_ADDRESS + len(blob) > TEXT_LIMIT:
        raise AssertionError(
            f'injected section ends at 0x{TEXT_ADDRESS + len(blob):08X}, past '
            f'0x{TEXT_LIMIT:08X} (OS low-memory globals)')
    section = d.add_text_section(TEXT_ADDRESS, blob)
    for hook, location in locations.items():
        d.write(hook, struct.pack('>I', branch(hook, location)))
    d.save(dst)
    return section, locations, len(blob)


def _inject_log_only(d, dst):
    """Retail game plus the SI logger only: no controller hooks at all."""
    stub, logger, entry, bl_idx, _ = build_logger()
    blob = bytearray(SCRATCH_BYTES)
    stub_at = TEXT_ADDRESS + len(blob)
    words = stub + [HOOK_PREIMAGE[LOG_HOOK], 0]
    words[-1] = branch(stub_at + (len(words) - 1) * 4, LOG_HOOK + 4)
    blob.extend(struct.pack('>%dI' % len(words), *words))
    while len(blob) & 0x1F:
        blob.extend(struct.pack('>I', 0x60000000))
    logger_at = TEXT_ADDRESS + len(blob)
    blob.extend(struct.pack('>%dI' % len(logger), *logger))
    idx = (stub_at - TEXT_ADDRESS) // 4 + bl_idx
    blob[idx * 4:idx * 4 + 4] = struct.pack(
        '>I', branch(TEXT_ADDRESS + idx * 4, logger_at + entry) | 1)
    assert TEXT_ADDRESS + len(blob) <= TEXT_LIMIT, 'log-only section too large'
    section = d.add_text_section(TEXT_ADDRESS, blob)
    d.write(LOG_HOOK, struct.pack('>I', branch(LOG_HOOK, stub_at)))
    d.save(dst)
    return section, {LOG_HOOK: stub_at}, len(blob)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--log', action='store_true',
                    help='also print SI state over a USB Gecko in slot B (src/gecko_log.c)')
    ap.add_argument('--log-only', action='store_true',
                    help='retail game plus the SI logger only, no controller hooks')
    args = ap.parse_args()
    section, locations, size = inject(args.src, args.dst, args.log, args.log_only)
    print(f'injected {len(locations)} hooks into text section {section} at '
          f'0x{TEXT_ADDRESS:08X} ({size} bytes)')
    for hook, body in locations.items():
        print(f'  0x{hook:08X} -> 0x{body:08X}')
