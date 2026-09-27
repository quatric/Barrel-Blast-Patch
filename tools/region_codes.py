#!/usr/bin/env python3
"""Write codes/RDKP01.ini and codes/RDKJ01.ini from codes/RDKE01.ini.

The European and Japanese Gecko codes are the USA ones with every hook
address, write address and game address inside the code bodies moved to
that region (see regions.py). Run after editing codes/RDKE01.ini; the
results are committed.
"""
import os
import re

from regions import REGIONS

HERE = os.path.dirname(os.path.abspath(__file__))
CODES = os.path.join(HERE, '..', 'codes')


def port(lines, region):
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        m = re.fullmatch(r'(C2|04)([0-9A-Fa-f]{6}) ([0-9A-Fa-f]{8})', line.strip())
        if not m:
            out.append(line)
            i += 1
            continue
        kind, addr, arg = m.group(1), 0x80000000 | int(m.group(2), 16), m.group(3)
        head = f'{kind}{region.addr(addr) & 0x01FFFFFF:06X}'
        if kind == '04':
            out.append(f'{head} {arg}')
            i += 1
            continue
        pairs = int(arg, 16)
        words = [int(f, 16) for l in lines[i + 1:i + 1 + pairs] for f in l.split()]
        words, _ = region.relocate_words(words)
        out.append(f'{head} {arg}')
        out += [f'{words[k]:08X} {words[k + 1]:08X}' for k in range(0, len(words), 2)]
        i += 1 + pairs
    return out


def main():
    lines = open(os.path.join(CODES, 'RDKE01.ini')).read().splitlines()
    for disc_id in ('RDKP01', 'RDKJ01'):
        path = os.path.join(CODES, disc_id + '.ini')
        with open(path, 'w') as f:
            f.write('\n'.join(port(lines, REGIONS[disc_id])) + '\n')
        print(f'wrote {os.path.relpath(path)}')


if __name__ == '__main__':
    main()
