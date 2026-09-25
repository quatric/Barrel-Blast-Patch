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
import sys
import tempfile

from dol import Dol

# In a PyInstaller build the data files (codes/, src/, tools/prebuilt/) are
# bundled under sys._MEIPASS at the same relative layout as the repository.
if getattr(sys, 'frozen', False):
    HERE = os.path.join(sys._MEIPASS, 'tools')
else:
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
#   +0x1C..+0x1F  gecko_log.c: channel-0 queued-sample / read counters
#   +0x20..+0x23  codeA gate: per-channel Classic Controller warm-up counters
#   +0x40..+0x4F  cc_nunchuk.c: per-channel left-drum stroke state
SCRATCH_BYTES = 0x60
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
# Buttons and pointer keep running for a Classic Controller channel (r31 is
# the KPAD base at both hook sites), so it can drive the HOME Menu.
HBM_CC_PASS = (0x80248090, 0x80247500)
# Button hook: skipped for a moment after a Classic Controller is attached
# (it reports every button, HOME included, while it initialises).
CC_WARMUP_HOOK = 0x80248090
CC_WARMUP = TEXT_ADDRESS + 0x20      # 4 per-channel counters in the scratch
CC_WARMUP_READS = 30                 # ~0.5 s at one read per frame
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


def hbm_gate(location, body_location, hook, preimage, cc_ok=False, cc_warmup=False):
    """Stub: if the HOME Menu is open, run the original instruction and
    return to the game; otherwise fall through to the hook body. With
    cc_ok (hooks where r31 is the KPAD channel base), a channel whose
    extension is a Classic Controller keeps its hook even in the HOME Menu,
    so a Classic Controller can drive the menu's pointer and buttons."""
    hi = lambda v: ((v >> 16) + (1 if v & 0x8000 else 0)) & 0xFFFF
    lo = lambda v: v & 0xFFFF
    head = [
        0x9421FFF0,                                   # stwu  r1,-0x10(r1)
        0x91610008,                                   # stw   r11,8(r1)
        0x9181000C,                                   # stw   r12,0xc(r1)
        0x7D800026,                                   # mfcr  r12
        0x91810004,                                   # stw   r12,4(r1)
    ]
    if cc_warmup:
        # A Classic Controller reports garbage (HOME included) for a moment
        # after it's plugged in. Skip this hook for CC_WARMUP_READS reads
        # after the channel's dev_type (r31+0x5C) becomes 2. r27 = channel.
        head += [
            0x3D600000 | (CC_WARMUP >> 16),           # lis   r11,warm@h
            0x616B0000 | (CC_WARMUP & 0xFFFF),        # ori   r11,r11,warm@l
            0x7D6BDA14,                               # add   r11,r11,r27
            0x899F005C,                               # lbz   r12,0x5c(r31)
            0x2C0C0002,                               # cmpwi r12,2
            'bne notcc',
            0x898B0000,                               # lbz   r12,0(r11)
            0x280C0000 | CC_WARMUP_READS,             # cmplwi r12,N
            'bge warmdone',
            0x398C0001,                               # addi  r12,r12,1
            0x998B0000,                               # stb   r12,0(r11)
            'b skip',
            'notcc:',
            0x39800000,                               # li    r12,0
            0x998B0000,                               # stb   r12,0(r11)
            'warmdone:',
        ]
    head += [
        0x3D600000 | hi(HBM_OBJECT),                  # lis   r11,obj@ha
        0x818B0000 | lo(HBM_OBJECT),                  # lwz   r12,obj@l(r11)
        0x3D600000 | (HBM_VTABLE >> 16),              # lis   r11,vt@h
        0x616B0000 | (HBM_VTABLE & 0xFFFF),           # ori   r11,r11,vt@l
        0x7C0C5800,                                   # cmpw  r12,r11
        'bne run',
        0x3D600000 | hi(HBM_OPEN_FLAG),               # lis   r11,flag@ha
        0x898B0000 | lo(HBM_OPEN_FLAG),               # lbz   r12,flag@l(r11)
        0x2C0C0000,                                   # cmpwi r12,0
        'beq run',
    ]
    if cc_ok:
        head += [
            0x899F005C,                               # lbz   r12,0x5c(r31)
            0x2C0C0002,                               # cmpwi r12,2 (Classic)
            'beq run',
        ]
    head += ['b skip']
    restore = [
        0x81810004,                                   # lwz   r12,4(r1)
        0x7D8FF120,                                   # mtcr  r12
        0x81610008,                                   # lwz   r11,8(r1)
        0x8181000C,                                   # lwz   r12,0xc(r1)
        0x38210010,                                   # addi  r1,r1,0x10
    ]
    words = head + ['run:'] + restore + ['b body'] + ['skip:'] + restore + [preimage, 'b back']
    labels, out = {}, []
    for w in words:
        if isinstance(w, str) and w.endswith(':'):
            labels[w[:-1]] = len(out)
        else:
            out.append(w)
    for i, w in enumerate(out):
        if not isinstance(w, str):
            continue
        op, target = w.split()
        pc = location + i * 4
        if target == 'body':
            out[i] = branch(pc, body_location)
        elif target == 'back':
            out[i] = branch(pc, hook + 4)
        else:
            off = (labels[target] - i) * 4
            base = {'b': 0x48000000, 'beq': 0x41820000, 'bne': 0x40820000,
                    'bge': 0x40800000}[op]
            out[i] = base | (off & (0x03FFFFFC if op == 'b' else 0xFFFC))
    return out


# Right after the game's own KPADRead call: rewrite Classic Controller
# entries in its status buffer as Wii Remote + Nunchuk (src/cc_nunchuk.c).
CC_NUNCHUK_HOOK = 0x8003A788
CC_NUNCHUK_PREIMAGE = 0x801E0054      # lwz r0,0x54(r30)

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


PREBUILT = os.path.join(HERE, 'prebuilt')


def compile_c(name, entry_symbol):
    """Position-independent words for src/<name> and the offset of
    entry_symbol. With devkitPPC installed it compiles (and refreshes the
    committed prebuilt copy); without it -- the normal case for anyone just
    running the patcher -- it uses tools/prebuilt/<name>.json, after
    checking that blob was built from exactly this source."""
    import hashlib, json
    src = os.path.join(HERE, '..', 'src', name)
    # Hash with normalised line endings: a Windows checkout turns LF into
    # CRLF, which must not make the prebuilt copy look stale.
    digest = hashlib.sha256(open(src, 'rb').read().replace(b'\r\n', b'\n')).hexdigest()
    cache = os.path.join(PREBUILT, name + '.json')
    if not os.path.exists(DEVKITPPC + 'powerpc-eabi-gcc'):
        if not os.path.exists(cache):
            raise RuntimeError(f'{name}: no devkitPPC and no prebuilt copy at {cache}')
        # Users never need devkitPPC: always use the shipped blob. A hash
        # mismatch only means a developer edited the source without
        # rebuilding, which the release process catches -- not a reason to
        # refuse to patch.
        data = json.load(open(cache))
        if data['sha256'] != digest:
            print(f'note: {name}: prebuilt copy was built from a different revision '
                  'of the source; using it anyway', file=sys.stderr)
        return [int(w, 16) for w in data['words']], data['entry']
    words, entry = _compile_c(src, name, entry_symbol)
    os.makedirs(PREBUILT, exist_ok=True)
    fresh = {'sha256': digest, 'entry': entry, 'words': ['%08X' % w for w in words]}
    if not os.path.exists(cache) or json.load(open(cache)) != fresh:
        with open(cache, 'w') as f:
            json.dump(fresh, f, indent=0)
            f.write('\n')
    return words, entry


def _compile_c(src, name, entry_symbol):
    with tempfile.TemporaryDirectory() as tmp:
        o, b = os.path.join(tmp, 'c.o'), os.path.join(tmp, 'c.bin')
        subprocess.run([DEVKITPPC + 'powerpc-eabi-gcc', '-O2', '-mcpu=750', '-meabi',
                        '-msoft-float', '-mno-sdata', '-ffreestanding', '-fno-builtin',
                        '-fno-common', '-fno-asynchronous-unwind-tables', '-fno-exceptions',
                        '-c', src, '-o', o], check=True)
        relocs = subprocess.run([DEVKITPPC + 'powerpc-eabi-objdump', '-r', o],
                                capture_output=True, text=True, check=True).stdout
        if 'R_PPC' in relocs:
            raise AssertionError(f'{name} must compile without relocations:\n' + relocs)
        syms = subprocess.run([DEVKITPPC + 'powerpc-eabi-nm', o],
                              capture_output=True, text=True, check=True).stdout
        entry = int(next(l.split()[0] for l in syms.splitlines()
                         if l.endswith(' ' + entry_symbol)), 16)
        subprocess.run([DEVKITPPC + 'powerpc-eabi-objcopy', '-O', 'binary',
                        '-j', '.text', o, b], check=True)
        return _words(b), entry


def cc_nunchuk_stub(at, target):
    """Call cc_convert(count=r3, entry=r29 + old_count*0x84, chan=r26) with
    all volatile state preserved, then run the hooked instruction."""
    save = [0x9421FF90, 0x90010008, 0x7C0802A6, 0x90010074]      # stwu -0x70; stw r0; mflr; stw lr
    save += [0x90010000 | (r << 21) | (0x0C + 4 * (r - 3)) for r in range(3, 13)]
    save += [0x7C000026, 0x90010040, 0x7C0902A6, 0x90010044,     # mfcr/stw, mfctr/stw
             0x7C0102A6, 0x90010048]                             # mfxer/stw
    args = [0x809E0054,                                          # lwz   r4,0x54(r30)
            0x1C840084,                                          # mulli r4,r4,0x84
            0x7C84EA14,                                          # add   r4,r4,r29
            0x7F45D378]                                          # mr    r5,r26
    call = [0]                                                   # bl    target
    rest = [0x80010048, 0x7C0103A6, 0x80010044, 0x7C0903A6,      # xer, ctr
            0x80010040, 0x7C0FF120]                              # cr
    rest += [0x80010000 | (r << 21) | (0x0C + 4 * (r - 3)) for r in range(3, 13)]
    rest += [0x80010074, 0x7C0803A6, 0x80010008, 0x38210070,     # lr, r0, pop
             CC_NUNCHUK_PREIMAGE, 0]                             # original; b back
    words = save + args + call + rest
    i = len(save) + len(args)
    words[i] = branch(at + i * 4, target) | 1
    words[-1] = branch(at + (len(words) - 1) * 4, CC_NUNCHUK_HOOK + 4)
    return words


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

    # The left stroke is stored into +0x74/+0x78 (and a sign into +0x6C):
    # Nunchuk acc fields for the GameCube path, but for a Classic Controller
    # they are its right stick (and left stick X) -- and codeB runs before
    # the IR routine, so codeD's Classic pointer read the drum values
    # instead of the stick. The game never used these as a left drum for a
    # Classic Controller anyway. Route the three stores through a cave that
    # skips them when KPAD dev_type (+0x5C) is 2.
    stores = [0x90FE0074, 0x90FE0078, 0x917E006C]
    if words[106:109] != stores:
        raise AssertionError(f'codeB left-stroke stores changed: {words[106:109]}')
    cave = len(words) - 2                  # just before the reproduced instruction

    def rel_target(i, w):
        op = w >> 26
        if op == 18 and not w & 3:             # b (no link, relative)
            off = w & 0x03FFFFFC
            return i + ((off - 0x04000000) if off & 0x02000000 else off) // 4
        if op == 16 and not w & 3:             # bc (relative)
            off = w & 0xFFFC
            return i + ((off - 0x10000) if off & 0x8000 else off) // 4
        return None
    to_orig = [i for i, w in enumerate(words) if rel_target(i, w) == cave]
    body = [0x881E005C,                    # lbz   r0,0x5c(r30)
            0x2C000002,                    # cmpwi r0,2
            0x41820010] + stores + [0]     # beq   +0x10 (skip stores); b back
    body[-1] = branch((cave + 6) * 4, 109 * 4)
    if len(body) % 2:
        body.append(0x60000000)
    words[cave:cave] = body
    orig = cave + len(body)
    for i in to_orig:                          # keep jumps to the original
        w = words[i]                           # instruction pointed at it
        if w >> 26 == 18:
            words[i] = (w & 0xFC000003) | branch(i * 4, orig * 4) & 0x03FFFFFC
        else:
            words[i] = (w & 0xFFFF0003) | (((orig - i) * 4) & 0xFFFC)
    words[106] = branch(106 * 4, cave * 4)
    words[107] = words[108] = 0x60000000
    return words


def _rel_target(i, w):
    op = w >> 26
    if op == 18 and not w & 3:                 # b (relative, no link)
        off = w & 0x03FFFFFC
        return i + ((off - 0x04000000) if off & 0x02000000 else off) // 4
    if op == 16 and not w & 3:                 # bc (relative)
        off = w & 0xFFFC
        return i + ((off - 0x10000) if off & 0x8000 else off) // 4
    return None


def _insert_cave(words, body):
    """Insert `body` just before the reproduced original instruction
    (words[-2]); branches that targeted that instruction are retargeted past
    the cave. Returns the cave's word index."""
    cave = len(words) - 2
    to_orig = [i for i, w in enumerate(words) if _rel_target(i, w) == cave]
    k = cave - 1
    while words[k] == 0x60000000:              # alignment nops
        k -= 1
    prev = words[k]
    if not (prev >> 26 == 18 and not prev & 3):
        # The code before the original instruction falls through into it:
        # hop over the cave, or that path would run the cave's body and
        # its `b back` (codeA's epilogue then looped, popping the stack).
        body = [branch(0, (len(body) + 1) * 4)] + list(body)
        if len(body) % 2:
            body.append(0x60000000)
        words[cave:cave] = body
        orig = cave + len(body)
        words[cave] = branch(cave * 4, orig * 4)
        cave += 1
    else:
        words[cave:cave] = body
        orig = cave + len(body)
    for i in to_orig:
        w = words[i]
        if w >> 26 == 18:
            words[i] = (w & 0xFC000003) | (branch(i * 4, orig * 4) & 0x03FFFFFC)
        else:
            words[i] = (w & 0xFFFF0003) | (((orig - i) * 4) & 0xFFFC)
    return cave


def repair_bongos(bodies):
    """Let DK Bongos (TaruKonga) through, and map their drums.

    The bongos answer SI polls like a GameCube pad but report only A, B, X,
    Y, Start and R (the clap mic), with both stick bytes zero and the
    PAD_USE_ORIGIN bit (in_hi bit 23) clear. Every hook treated that bit as
    "valid GameCube response" (`andis. rX,rY,0x80` + skip), so bongos were
    ignored everywhere. The ERRSTAT check before it still rejects an empty
    port."""
    def check_at(words, name):
        hits = [k for k, w in enumerate(words)
                if w >> 26 == 29 and (w & 0xFFFF) == 0x0080]
        if len(hits) != 1:
            raise AssertionError(f'{name}: use-origin check found {len(hits)} times')
        return hits[0]

    # codeA (buttons), codeE (sample synthesis): drop the requirement.
    # (Skipping bongos in codeA to stop A/B acting as Wii Remote A/B was
    # tried: on hardware the bongos then did nothing in menus.)
    for hook, name in ((0x80248090, 'codeA'), (0x80247BE0, 'codeE')):
        w = list(bodies[hook]); k = check_at(w, name)
        assert w[k + 1] >> 16 == 0x4182, f'{name}: expected beq after the check'
        w[k + 1] = 0x60000000
        bodies[hook] = w
    # codeA (buttons): a bongo's clap (R, 0x0020 once shifted) -> Wii Remote
    # A (attack). Hooked on codeA's `srwi r12,r12,16`, while the stick bytes
    # are still in r12's low half: only a pad with no sticks is a bongo --
    # a GameCube pad's R stays a right drum. (Both drums together already
    # jump: the bongo sets the left and right drum bits on its own.)
    w = list(bodies[0x80248090])
    shift = [i for i, x in enumerate(w) if x == 0x558C843E]     # srwi r12,r12,16
    if len(shift) != 1:
        raise AssertionError(f'codeA: button shift found {len(shift)} times')
    at = shift[0]
    cave = [0x7180FCFC,        # andi.  r0,r12,0xFCFC  stick bytes (in_hi low half)
            0x558C843E,        # srwi   r12,r12,16     (doesn't touch cr0)
            0,                 # bne    back           a real pad: unchanged
            0x71800020,        # andi.  r0,r12,0x0020  R    (clap mic)
            0x41820008,        # beq    +8
            0x60E70800,        # ori    r7,r7,0x0800   -> Wii Remote A
            0]                 # back:  b at+1
    if len(cave) % 2:
        cave.insert(-1, 0x60000000)
    back = len(cave) - 1
    cave[2] = 0x40820000 | (((back - 2) * 4) & 0xFFFC)
    c = _insert_cave(w, cave)
    w[c + back] = branch((c + back) * 4, (at + 1) * 4)
    w[at] = branch(at * 4, c * 4)
    bodies[0x80248090] = w
    # codeF (motion neutralising): any responding pad counts as present.
    w = list(bodies[0x80247FA8]); k = check_at(w, 'codeF')
    assert w[k + 1] >> 16 == 0x4082, 'codeF: expected bne after the check'
    target = _rel_target(k + 1, w[k + 1])
    w[k + 1] = 0x48000000 | (((target - (k + 1)) * 4) & 0x03FFFFFC)
    bodies[0x80247FA8] = w
    # codeC (stick) and codeD (pointer): skip when both stick bytes are zero
    # instead -- a bongo has no sticks, and (0 ^ 0x80) would read as full
    # deflection, pinning the steering / pointer to one side.
    for hook, name in ((0x8024791C, 'codeC'), (0x80247500, 'codeD')):
        w = list(bodies[hook]); k = check_at(w, name)
        rs = (w[k] >> 21) & 31
        assert w[k + 1] >> 16 == 0x4182, f'{name}: expected beq after the check'
        w[k] = 0x70000000 | (rs << 21) | 0xFFFF          # andi. r0,rS,0xFFFF
        bodies[hook] = w
    # codeB (drums): drop the requirement, and remap bongo buttons onto the
    # GameCube drum masks (right = Y 0x0800, left = X 0x0400): A/X (right
    # bongo) -> right, B/Y (left bongo) -> left. R (clap) no longer also
    # fires both drums (jump) -- codeA now sends it as Wiimote A (punch)
    # instead, and a real simultaneous double-drum hit already sets both
    # the right (Y) and left (X) bits on its own, so jump still works.
    w = list(bodies[0x80246588]); k = check_at(w, 'codeB')
    assert w[k + 1] >> 16 == 0x4182
    w[k + 1] = 0x60000000
    shift = [i for i, x in enumerate(w) if x == 0x54C6843E]     # srwi r6,r6,16
    if len(shift) != 1:
        raise AssertionError(f'codeB: button shift found {len(shift)} times')
    at = shift[0]
    cave = [0x70C0FCFC,        # andi.  r0,r6,0xFCFC   stick bytes (in_hi low half)
            0x54C6843E,        # srwi   r6,r6,16       (doesn't touch cr0)
            0,                 # bne    back           a real pad: unchanged
            0x38000000,        # li     r0,0
            0x39800000,        # li     r12,0          ignore the analog word: the
                               #                       clap mic's level lands there
                               #                       and read as the R trigger
            0x70C90500,        # andi.  r9,r6,0x0500   A|X  (right bongo)
            0x41820008,
            0x60000800,        # ori    r0,r0,0x0800   -> right drum
            0x70C90A00,        # andi.  r9,r6,0x0A00   B|Y  (left bongo)
            0x41820008,
            0x60000400,        # ori    r0,r0,0x0400   -> left drum
            0x70C91000,        # andi.  r9,r6,0x1000   Start
            0x7C004B78,        # or     r0,r0,r9
            0x7C060378,        # mr     r6,r0
            0]                 # back:  b at+1
    if len(cave) % 2:
        cave.insert(-1, 0x60000000)        # keep the C2 body's word count even
    back = len(cave) - 1
    cave[2] = 0x40820000 | (((back - 2) * 4) & 0xFFFC)
    c = _insert_cave(w, cave)
    w[c + back] = branch((c + back) * 4, (at + 1) * 4)
    w[at] = branch(at * 4, c * 4)
    bodies[0x80246588] = w


def repair_stick(codec):
    """codeC's channel detect had the same `ori` for `addi` bug: channel 1's
    base+0x60 came out 0x803C9220 | 0x524 = 0x803C9724 instead of 0x803C9744,
    so the stick hook never matched player 2 (Classic or GameCube)."""
    words = list(codec)
    hits = [i for i, w in enumerate(words) if w == 0x60C60524]
    if len(hits) != 1:
        raise AssertionError(f'unexpected codeC channel-1 base words: {hits}')
    words[hits[0]] = 0x38C60524       # addi r6,r6,0x524
    return words


def repair_pointer(coded):
    """Keep Classic Controller IR reads away from the left-drum vector."""
    words = list(coded)
    # codeD reads the Classic right stick from +0x74/+0x78. An earlier repair
    # redirected those loads to +0x7C/+0x80 to dodge codeB's left-stroke
    # stores -- but those are the analog triggers, so the Classic pointer
    # followed the triggers. codeB now leaves +0x74/+0x78 alone on a
    # Classic Controller, so the original loads are right.
    found = {w: sum(1 for x in words if x == w) for w in (0xC03F0074, 0xC05F0078)}
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
    bodies[0x8024791C] = repair_stick(bodies[0x8024791C])
    repair_bongos(bodies)

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
        while len(blob) & 0x3:            # word alignment is all code needs
            blob.extend(struct.pack('>I', 0x60000000))
        entry = TEXT_ADDRESS + len(blob)
        if log and hook == LOG_HOOK:
            stub, logger, log_entry, bl_idx, crash_entry = build_logger()
            log_patch = (len(blob) // 4 + bl_idx, log_entry)
            blob.extend(struct.pack('>%dI' % len(stub), *stub))
        if hook in HBM_GATED:
            cc_ok = hook in HBM_CC_PASS
            warm = hook == CC_WARMUP_HOOK
            gate_len = len(hbm_gate(entry, entry, hook, HOOK_PREIMAGE[hook], cc_ok, warm)) * 4
            body_location = entry + gate_len
            gate = hbm_gate(entry, body_location, hook, HOOK_PREIMAGE[hook], cc_ok, warm)
            blob.extend(struct.pack('>%dI' % len(gate), *gate))
        location = TEXT_ADDRESS + len(blob)
        words = list(bodies[hook])
        if words[-2] != HOOK_PREIMAGE[hook]:
            raise AssertionError(
                f'C2 {hook:#x} does not end with its reproduced hook instruction')
        words[-1] = branch(location + (len(words) - 1) * 4, hook + 4)
        locations[hook] = entry
        blob.extend(struct.pack('>%dI' % len(words), *words))

    got = struct.unpack('>I', d.read(CC_NUNCHUK_HOOK, 4))[0]
    if got != CC_NUNCHUK_PREIMAGE:
        raise AssertionError(f'CC->Nunchuk hook 0x{CC_NUNCHUK_HOOK:08X}: found 0x{got:08X}')
    cc_words, cc_entry = compile_c('cc_nunchuk.c', 'cc_convert')
    while len(blob) & 0x3:            # word alignment is all code needs
        blob.extend(struct.pack('>I', 0x60000000))
    cc_code = TEXT_ADDRESS + len(blob)
    blob.extend(struct.pack('>%dI' % len(cc_words), *cc_words))
    while len(blob) & 0x3:            # word alignment is all code needs
        blob.extend(struct.pack('>I', 0x60000000))
    cc_stub = TEXT_ADDRESS + len(blob)
    words = cc_nunchuk_stub(cc_stub, cc_code + cc_entry)
    blob.extend(struct.pack('>%dI' % len(words), *words))
    locations[CC_NUNCHUK_HOOK] = cc_stub

    if log:
        got = struct.unpack('>I', d.read(CRASH_HOOK, 4))[0]
        if got != CRASH_PREIMAGE:
            raise AssertionError(f'crash hook 0x{CRASH_HOOK:08X}: found 0x{got:08X}')
        while len(blob) & 0x3:            # word alignment is all code needs
            blob.extend(struct.pack('>I', 0x60000000))
        crash_at = TEXT_ADDRESS + len(blob)
        words = stub + [CRASH_PREIMAGE, 0]
        words[-1] = branch(crash_at + (len(words) - 1) * 4, CRASH_HOOK + 4)
        crash_bl = len(blob) // 4 + bl_idx
        blob.extend(struct.pack('>%dI' % len(words), *words))
        while len(blob) & 0x3:            # word alignment is all code needs
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
    while len(blob) & 0x3:            # word alignment is all code needs
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
