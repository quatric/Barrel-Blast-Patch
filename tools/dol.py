import struct, sys

class Dol:
    def __init__(self, path):
        self.path = path
        self.data = bytearray(open(path, 'rb').read())
        h = self.data
        self.off  = list(struct.unpack('>18I', h[0x00:0x48]))
        self.addr = list(struct.unpack('>18I', h[0x48:0x90]))
        self.size = list(struct.unpack('>18I', h[0x90:0xD8]))
        self.bss_addr, self.bss_size, self.entry = struct.unpack('>3I', h[0xD8:0xE4])
        self.secs = [(self.off[i], self.addr[i], self.size[i], i)
                     for i in range(18) if self.size[i] and self.addr[i]]

    def v2f(self, va):
        for o,a,s,i in self.secs:
            if a <= va < a+s:
                return o + (va-a)
        return None

    def f2v(self, fo):
        for o,a,s,i in self.secs:
            if o <= fo < o+s:
                return a + (fo-o)
        return None

    def read(self, va, n):
        f = self.v2f(va)
        if f is None: return None
        return bytes(self.data[f:f+n])

    def write(self, va, data):
        f = self.v2f(va)
        if f is None:
            raise ValueError(f'address 0x{va:08X} not mapped in {self.path}')
        self.data[f:f+len(data)] = data

    def save(self, path):
        open(path, 'wb').write(self.data)

    def add_text_section(self, va, data, alignment=0x20):
        """Append a new executable section and return its DOL section index.

        DOL has seven text-section slots (indices 0..6).  Retail DKBB uses
        only two, so a clean injector can add code without relocating any of
        the game's existing sections.
        """
        index = next((i for i in range(7) if not self.size[i]), None)
        if index is None:
            raise ValueError('DOL has no free text-section slot')
        if va & (alignment - 1):
            raise ValueError(f'text-section address 0x{va:08X} is not {alignment:#x}-aligned')
        end = va + len(data)
        for _o, addr, size, _i in self.secs:
            if va < addr + size and addr < end:
                raise ValueError(
                    f'new text section 0x{va:08X}-0x{end:08X} overlaps '
                    f'0x{addr:08X}-0x{addr + size:08X}')

        fileoff = (len(self.data) + alignment - 1) & -alignment
        self.data.extend(b'\0' * (fileoff - len(self.data)))
        self.data.extend(data)
        self.off[index], self.addr[index], self.size[index] = fileoff, va, len(data)
        struct.pack_into('>I', self.data, index * 4, fileoff)
        struct.pack_into('>I', self.data, 0x48 + index * 4, va)
        struct.pack_into('>I', self.data, 0x90 + index * 4, len(data))
        self.secs.append((fileoff, va, len(data), index))
        return index

    def dump(self):
        for o,a,s,i in self.secs:
            kind = 'text' if i < 7 else 'data'
            print(f'  [{i:2d}] {kind}  file 0x{o:06X}  vaddr 0x{a:08X}  size 0x{s:06X}  end 0x{a+s:08X}')
        print(f'  bss 0x{self.bss_addr:08X} size 0x{self.bss_size:X}  entry 0x{self.entry:08X}')

if __name__ == '__main__':
    for p in sys.argv[1:]:
        print(p)
        Dol(p).dump()
