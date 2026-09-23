/* SI state logger over a USB Gecko in memory card slot B -- no debugger hook.
 *
 * The loader's USB Gecko debugger hooks (VBI/WPAD/GCNPad) freeze patched
 * builds, but the Gecko itself is just an EXI serial bridge, so this talks
 * to it directly (the same EXI exchange libogc's usbgecko.c uses) and
 * prints one line of SI state at most every ~0.2 s:
 *
 *   T<tb> B<busy> P<si.poll shadow> t<type0>,<type1>,<type2>,<type3>
 *     S<SISR> L<SIPOLL> C<SICOMCSR> I<C0INBUFH>,<C0INBUFL>
 *
 * Called from a hook stub at KPADRead's entry. Position independent: no
 * globals, only fixed hardware/game addresses; the rate-limit stamp lives
 * in the injected section's scratch words. If no Gecko answers, it returns
 * after one EXI exchange.
 */
typedef unsigned int u32;

#define REG(a)      (*(volatile u32 *)(a))
#define EXI1_CSR    REG(0xCD006814)
#define EXI1_CR     REG(0xCD006820)
#define EXI1_DATA   REG(0xCD006824)
#define STAMP       REG(0x80001830)     /* scratch word 4 (poller uses 0-3) */

static u32 exchange(u32 v)
{
    EXI1_CSR = 0xD0;                    /* device 0, 32 MHz */
    EXI1_DATA = v;
    EXI1_CR = 0x19;                     /* 2 bytes, read/write, start */
    while (EXI1_CR & 1)
        ;
    v = EXI1_DATA;
    EXI1_CSR = 0;
    return v;
}

static void put(u32 c)
{
    int tries = 256;
    while (tries-- && !(exchange(0xB0000000 | (c << 20)) & 0x04000000))
        ;
}

static void hex(u32 v)
{
    int i;
    for (i = 28; i >= 0; i -= 4) {
        u32 n = (v >> i) & 0xF;
        put(n < 10 ? '0' + n : 'a' + n - 10);
    }
}

static void field(u32 tag, u32 v)
{
    put(tag);
    hex(v);
    put(' ');
}

void gecko_log(void)
{
    u32 tb, i;

    __asm__ volatile ("mftb %0" : "=r"(tb));
    if (tb - STAMP < 12150000)          /* 0.2 s at 60.75 MHz */
        return;
    STAMP = tb;

    if (exchange(0x90000000) != 0x04700000)   /* USB Gecko ID check */
        return;

    field('T', tb);
    field('B', REG(0x80331538));        /* si:: busy flag, -1 idle */
    field('P', REG(0x8033153C));        /* si:: SIPOLL shadow */
    put('t');
    for (i = 0; i < 4; i++) {
        hex(REG(0x80331550 + i * 4));   /* si:: cached type */
        put(i < 3 ? ',' : ' ');
    }
    field('S', REG(0xCD006438));
    field('L', REG(0xCD006430));
    field('C', REG(0xCD006434));
    put('I');
    hex(REG(0xCD006404));
    put(',');
    hex(REG(0xCD006408));
    put('\r');
    put('\n');
}
