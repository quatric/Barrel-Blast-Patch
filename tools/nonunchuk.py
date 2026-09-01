#!/usr/bin/env python3
"""Verify the two real CNunchakaCheck branch sites in the retail DOL.

The old seven-site patch was a false positive: those sites are unrelated
gameplay object-state checks and must never be modified.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import DKBB_DOL

SITES = {0x80179990: 0x40820018, 0x80179F94: 0x40820030}


def verify():
    from dol import Dol
    d = Dol(DKBB_DOL)
    for addr, expected in SITES.items():
        data = d.read(addr, 4)
        if data is None:
            raise AssertionError(f'0x{addr:08X} not mapped in {DKBB_DOL}')
        got = struct.unpack('>I', data)[0]
        if got != expected:
            raise AssertionError(
                f'0x{addr:08X}: expected {expected:#010X}, found {got:#010X} -- '
                f'this is not the DOL these addresses were found against')


def main():
    verify()
    print(f'verified {len(SITES)} CNunchakaCheck branches against {DKBB_DOL}')


if __name__ == '__main__':
    main()
