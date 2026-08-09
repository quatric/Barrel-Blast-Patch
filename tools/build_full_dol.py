#!/usr/bin/env python3
"""Bake the SI poller fix AND Classic Controller support into one main.dol.

Why this needs more than build.py + ccpatch.py run back to back: ccpatch.py
only edits codes/RDKE01.ini (Gecko code *text*, for a runtime loader), and
build.py's static_patches() only ever writes in place at fixed addresses --
neither grows anything. codeB's new Classic Controller branch is 38 words
bigger than the slot it came from, and this DOL has no runtime Gecko loader
allocating cave space per code: the region at 0x800022b0-0x80002a78 is a
real Gecko codehandler (confirmed against Dolphin's own codehandler.bin --
same code, ~same size, same 0x800021xx self-pointer, same 00D0C0DE 00D0C0DE
GCT magic immediately before the first entry) baked into the DOL as static
data, and it branches each hook DIRECTLY INTO the list in place (see
_hook1 in codehandler.s: the branch target is computed from the scan
pointer's position while reading the list, there is no separate cave copy).
That means the list is fully self-relocating -- grow one entry and every
later entry's *runtime* address moves automatically, with nothing to fix
up -- but the list's *on-disk* size still has to grow to match, and that
means extending the DOL segment that holds it.

Segment 2 (vaddr 0x80001800, the codehandler + list) has zero slack today:
the list ends with an 8-byte F0000000 terminator that exactly fills the
segment, and RAM above it is free until the next segment at 0x80004000
(5512 bytes of headroom). Segment 2 is also the *last* segment in the file
(fileoff + size == filesize), so growing it is a straight append: bump
its size in the DOL header, and the new content just becomes the new end
of the file. No relocation table exists to update, because the DOL header
only describes fixed segments -- there's no separate manifest of what's
*inside* segment 2 for anything else in the file to reference.

Usage: python3 build_full_dol.py [--variant autopoll|stash] SRC_DOL DST_DOL
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build as pollerbuild
from dol import Dol

LIST_START = 0x800022b0
SEGMENT_INDEX = 2

CODEA_HOOK = 0x80248090
CODEB_HOOK = 0x80246588
CODEC_HOOK = 0x8024791C
CODED_HOOK = 0x80247500

CODEA_PATCHES = [(26, 0x75208000, 0x71208000)]
CODEC_PATCHES = [
    (32, 0x895E005C, 0x895EFFFC), (36, 0x915E0108, 0x9141003C),
    (37, 0xC03E0108, 0xC021003C), (38, 0xC05E006C, 0xC05E000C),
    (41, 0xD85E0108, 0xD8410038), (42, 0x809E010C, 0x8081003C),
    (43, 0xC07E0070, 0xC07E0010), (46, 0xD87E0108, 0xD8610038),
    (47, 0x80BE010C, 0x80A1003C),
]
CODEB_ENTRY_BRANCHES = [36, 38, 40]
CODEB_FANIN = 42
CODEB_COMMON = 53
CODEB_ORIG_IDX = 132
CC_ASM = os.path.join(HERE, '..', 'src', 'codeB_cc.s')


def assemble(path):
    with tempfile.TemporaryDirectory() as tmp:
        o, b = os.path.join(tmp, 'a.o'), os.path.join(tmp, 'a.bin')
        dk = '/opt/devkitpro/devkitPPC/bin'
        subprocess.run([f'{dk}/powerpc-eabi-as', '-mbig', '-mgekko', '-o', o, path], check=True)
        subprocess.run([f'{dk}/powerpc-eabi-objcopy', '-O', 'binary', o, b], check=True)
        data = open(b, 'rb').read()
    return list(struct.unpack('>%dI' % (len(data) // 4), data))


def branch(from_idx, to_idx):
    off = (to_idx - from_idx) * 4
    assert -0x2000000 <= off < 0x2000000
    return 0x48000000 | (off & 0x03FFFFFC)


def parse_list(d):
    pos, entries = LIST_START, []
    while True:
        op, val = struct.unpack('>2I', d.read(pos, 8))
        if (op >> 24) == 0xF0:
            entries.append(('TERM', d.read(pos, 8)))
            return entries
        hook, nn = 0x80000000 | (op & 0x01FFFFFF), val
        words = list(struct.unpack('>%dI' % (nn * 2), d.read(pos + 8, nn * 8)))
        entries.append((hook, words))
        pos += 8 + nn * 8


def apply_word_patches(words, patches, label):
    for idx, old, new in patches:
        got = words[idx]
        if got != old:
            raise AssertionError(f'{label}[{idx}]: expected {old:#010X}, found {got:#010X}')
        words[idx] = new


def build_codeb(words, variant):
    assert len(words) == 134, f'codeB entry is {len(words)} words, expected 134'
    assert words[CODEB_ORIG_IDX] == 0x80010044, 'codeB[132] is not the hooked instruction'
    assert words[CODEB_FANIN] == 0x1D43000C, 'codeB fan-in mismatch'
    assert words[CODEB_COMMON] == 0x3CE04120, 'codeB common mismatch'

    cc = assemble(CC_ASM)
    assert cc[-2:] == [0x48000000, 0x48000000], 'CC block must end with two placeholder branches'

    jump_idx = 132
    cc_idx = 133
    orig_idx = cc_idx + len(cc)
    new = words[:132]
    new.append(branch(jump_idx, orig_idx))
    new += cc
    new.append(words[CODEB_ORIG_IDX])
    if len(new) % 2 == 0:
        new.append(0x60000000)
    new.append(0x60000000)

    exit_common, exit_back = cc_idx + len(cc) - 2, cc_idx + len(cc) - 1
    new[exit_common] = branch(exit_common, CODEB_COMMON)
    new[exit_back] = branch(exit_back, CODEB_FANIN)
    for idx in CODEB_ENTRY_BRANCHES:
        assert (new[idx] >> 26) == 18, f'codeB[{idx}] is not a branch'
        new[idx] = branch(idx, cc_idx)
    assert len(new) % 2 == 0
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--variant', choices=['autopoll', 'stash'], default='autopoll')
    args = ap.parse_args()

    d = Dol(args.src)
    entries = parse_list(d)
    by_hook = {e[0]: e[1] for e in entries if e[0] != 'TERM'}
    terminator = next(e[1] for e in entries if e[0] == 'TERM')

    print(f'parsed {len(entries)-1} codes + terminator from {LIST_START:#010x}')

    poller_words = list(pollerbuild.VARIANTS[args.variant]) + [0x60000000]
    assert len(by_hook[pollerbuild.POLLER_HOOK]) == 28
    print(f'poller: replaced with {args.variant} ({len(poller_words)} words)')

    codea_words = list(by_hook[CODEA_HOOK])
    apply_word_patches(codea_words, CODEA_PATCHES, 'codeA')
    print('codeA: applied D-pad RIGHT fix')

    codeb_words = build_codeb(by_hook[CODEB_HOOK], args.variant)
    print(f'codeB: {len(by_hook[CODEB_HOOK])} -> {len(codeb_words)} words (+ Classic Controller branch)')

    codec_words = list(by_hook[CODEC_HOOK])
    apply_word_patches(codec_words, CODEC_PATCHES, 'codeC')
    print('codeC: rebased Classic Controller branch')

    coded_words = list(by_hook[CODED_HOOK])
    print(f'codeD: unchanged ({len(coded_words)} words)')

    if args.variant == 'stash':
        raise SystemExit(
            'stash variant needs its codeA-D word offsets recomputed against the new '
            'CC-grown layout (codeB moved everything after it) -- not done here yet, '
            'use --variant autopoll')

    def emit(hook, words):
        assert len(words) % 2 == 0, f'{hook:#x}: odd word count {len(words)}'
        nn = len(words) // 2
        return struct.pack('>2I', 0xC2000000 | (hook & 0x01FFFFFF), nn) + \
               b''.join(struct.pack('>I', w) for w in words)

    blob = b''
    blob += emit(pollerbuild.POLLER_HOOK, poller_words)
    blob += emit(CODEA_HOOK, codea_words)
    blob += emit(CODEB_HOOK, codeb_words)
    blob += emit(CODEC_HOOK, codec_words)
    blob += emit(CODED_HOOK, coded_words)
    blob += terminator

    old_list_len = sum(8 + len(w) * 4 for h, w in by_hook.items()) + len(terminator)
    growth = len(blob) - old_list_len
    print(f'\nlist: {old_list_len} -> {len(blob)} bytes (growth {growth:+d})')

    new_size = d.size[SEGMENT_INDEX] + growth
    print(f'segment {SEGMENT_INDEX}: size {d.size[SEGMENT_INDEX]:#x} -> {new_size:#x}')
    max_headroom = 0x80004000 - (d.addr[SEGMENT_INDEX] + d.size[SEGMENT_INDEX])
    if growth > max_headroom:
        raise SystemExit(f'growth {growth} exceeds known-free headroom {max_headroom} before the next segment')

    fileoff = d.v2f(LIST_START)
    new_data = bytearray(d.data)
    new_data[fileoff:fileoff + old_list_len] = blob  # this is the tail of the file; safe to resize
    struct.pack_into('>I', new_data, 0x90 + SEGMENT_INDEX * 4, new_size)  # size table at 0x90

    open(args.dst, 'wb').write(new_data)
    print(f'\nwrote {args.dst} ({len(new_data)} bytes)')

    # sanity: reparse the written file with the plain Dol reader
    d2 = Dol(args.dst)
    for o, a, s, i in d2.secs:
        if i == SEGMENT_INDEX:
            print(f'reparsed segment {SEGMENT_INDEX}: fileoff={o:#x} vaddr={a:#x} size={s:#x} end={a+s:#x}')


if __name__ == '__main__':
    main()
