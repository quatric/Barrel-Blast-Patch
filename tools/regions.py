"""Per-region addresses for the USA, European and Japanese releases.

Every hook, repair and Gecko code in this repository is written against the
USA main.dol (RDKE01). The European (RDKP01, "Donkey Kong: Jet Race") and
Japanese (RDKJ01, "Donkey Kong Taru Jet Race") DOLs link the same game code
and the same Wii SDK, only placed at different addresses: around every hook
site, and every game function or global the hooks touch, the code is
identical word for word once branch offsets and absolute-address halves are
masked out (checked +-96 instructions around each one). So a USA address is
ported by looking it up here, and the hook bodies only need the absolute
addresses they build with `lis` + `ori`/`addi`/load/store rewritten.

Each table entry was found by matching the USA code around it against the
other DOL with relocatable bits masked, and required to match exactly once.
Data addresses were read back from the matched code that references them.
"""
import struct

# USA address -> (Europe, Japan). Code: hook sites and the game/SDK
# functions the hooks call.
CODE = {
    0x80247ADC: (0x802487BC, 0x8024754C),   # KPADRead entry: SI poller, logger
    0x80248090: (0x80248D70, 0x80247B00),   # KPADiRead button compose (codeA)
    0x80246588: (0x80247268, 0x80245FF8),   # accelerometer read (codeB)
    0x8024791C: (0x802485FC, 0x8024738C),   # Nunchuk stick read (codeC)
    0x80247500: (0x802481E0, 0x80246F70),   # DPD / IR read (codeD)
    0x80247BE0: (0x802488C0, 0x80247650),   # KPADiRead sample count (codeE)
    0x80247FA8: (0x80248C88, 0x80247A18),   # Wii Remote motion (codeF)
    0x8003A788: (0x8003A810, 0x8003A6B8),   # after the game's KPADRead call
    0x801C0B34: (0x801C1820, 0x801C0690),   # __OSUnhandledException
    0x8017998C: (0x8017A160, 0x801791BC),   # CNunchakaCheck: cmplwi r0,1
    0x80179990: (0x8017A164, 0x801791C0),   #   ... and its bne
    0x80179F90: (0x8017A764, 0x801797C0),   # CNunchakaCheck: cmplwi r0,1
    0x80179F94: (0x8017A768, 0x801797C4),   #   ... and its bne
    0x801F4FA0: (0x801F5C8C, 0x801F4A18),   # si::SIGetType
    0x801C3A40: (0x801C472C, 0x801C359C),   # OSDisableInterrupts
    0x801C3A68: (0x801C4754, 0x801C35C4),   # OSRestoreInterrupts
}

# Data: the HOME Menu singleton pointer and the vtable it's checked against.
DATA = {
    0x803E7AD8: (0x803EA858, 0x803F1E80),   # CHomeButtonMenu instance (r13-0x5E48 / -0x5E20)
    0x802E7288: (0x802E8E40, 0x802E6AA0),   # CHomeButtonMenu vtable (at object +0x20)
}

# Whole blocks that moved as one: (USA start, USA end, (Europe, Japan) delta).
BLOCKS = [
    (0x80331538, 0x80331560, (0x2DA0, 0x1CE0)),     # si:: busy flag, SIPOLL shadow, cached types
    (0x803C91C0, 0x803C91C0 + 4 * 0x524, (0x2D80, 0xA380)),   # KPAD channel structs
]


class Region:
    def __init__(self, disc_id, name, column):
        self.disc_id = disc_id
        self.name = name
        self.column = column        # None for USA: every address is its own

    def __repr__(self):
        return f'{self.name} ({self.disc_id})'

    def addr(self, us):
        """The address in this region's DOL of USA address `us`."""
        if self.column is None:
            return us
        for table in (CODE, DATA):
            if us in table:
                return table[us][self.column]
        for start, end, delta in BLOCKS:
            if start <= us < end:
                return us + delta[self.column]
        raise KeyError(f'no {self.name} address known for USA 0x{us:08X}')

    def known(self, us):
        return (us in CODE or us in DATA
                or any(start <= us < end for start, end, _ in BLOCKS))

    def relocate_words(self, words):
        """Rewrite every absolute address a code body builds with `lis`
        followed by `ori`/`addi`/a load/store off that register, when the
        address is one this table knows. Returns (words, relocation count);
        the count lets callers assert nothing was missed."""
        out = list(words)
        count = 0
        for i, w in enumerate(words):
            if w >> 26 != 15 or (w >> 16) & 31:            # lis rD,hi
                continue
            rd, hi = (w >> 21) & 31, (w & 0xFFFF) << 16
            uses = []
            for j in range(i + 1, min(i + 16, len(words))):
                x = words[j]
                op = x >> 26
                if op == 24 and (x >> 21) & 31 == rd:      # ori rA,rD,lo
                    uses.append((j, hi | (x & 0xFFFF), False))
                    if (x >> 16) & 31 == rd:
                        break
                elif (op == 14 or 32 <= op <= 55) and (x >> 16) & 31 == rd:
                    lo = x & 0xFFFF
                    uses.append((j, (hi + lo - (0x10000 if lo & 0x8000 else 0)) & 0xFFFFFFFF, True))
                    if (op == 14 or op < 48 and op not in (36, 37, 38, 39, 44, 45, 47)) \
                            and (x >> 21) & 31 == rd:      # addi / load into rD
                        break
                elif _writes(x, rd) or op in (16, 18, 19):
                    break
            uses = [u for u in uses if self.known(u[1])]
            if not uses:
                continue
            his = set()
            for j, value, signed in uses:
                new = self.addr(value)
                his.add(((new >> 16) + (1 if signed and new & 0x8000 else 0)) & 0xFFFF)
                out[j] = (out[j] & 0xFFFF0000) | (new & 0xFFFF)
            if len(his) != 1:
                raise AssertionError(f'lis at word {i} feeds addresses in different 64K pages')
            out[i] = (w & 0xFFFF0000) | his.pop()
            count += len(uses)
        return out, count


def _writes(x, rd):
    """Conservatively: does instruction x write GPR rd?"""
    op = x >> 26
    if op in (7, 8, 12, 13, 14, 15) or 32 <= op <= 47:
        return (x >> 21) & 31 == rd
    if 20 <= op <= 29:
        return (x >> 16) & 31 == rd
    if op == 31:
        return rd in ((x >> 21) & 31, (x >> 16) & 31)
    return False


REGIONS = {
    'RDKE01': Region('RDKE01', 'USA', None),
    'RDKP01': Region('RDKP01', 'Europe', 0),
    'RDKJ01': Region('RDKJ01', 'Japan', 1),
}


def word(dol, address):
    raw = dol.read(address, 4)
    return None if raw is None else struct.unpack('>I', raw)[0]
