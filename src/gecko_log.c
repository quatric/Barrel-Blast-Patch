/* SI state logger over a USB Gecko in memory card slot B -- no debugger hook.
 *
 * The loader's USB Gecko debugger hooks (VBI/WPAD/GCNPad) freeze patched
 * builds, but the Gecko itself is just an EXI serial bridge, so this talks
 * to it directly (the same EXI exchange libogc's usbgecko.c uses) and
 * prints one line of SI state at most every ~0.2 s:
 *
 *   t<type0>,<type1>,<type2>,<type3> S<SISR> L<SIPOLL> I<C0INBUFH>
 *     K<ch0 hold>,<ch0 dev_type/wpad_err/dpd_valid/format>
 *      <ch1 hold>,<ch1 dev_type/...>  (KPADStatus words +0x00 and +0x5C)
 *     Q<samples queued on channel 0>/<channel-0 reads> since the last line
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
#define Q_SAMPLES   (*(volatile unsigned short *)0x8000183C)  /* scratch +0x1C */
#define Q_READS     (*(volatile unsigned short *)0x8000183E)  /* scratch +0x1E */

static __attribute__((noinline)) u32 exchange(u32 v)
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

static __attribute__((noinline)) void put(u32 c)
{
    int tries = 256;
    while (tries-- && !(exchange(0xB0000000 | (c << 20)) & 0x04000000))
        ;
}

static __attribute__((noinline)) void hex(u32 v)
{
    int i;
    for (i = 28; i >= 0; i -= 4) {
        u32 n = (v >> i) & 0xF;
        put(n < 10 ? '0' + n : 'a' + n - 10);
    }
}

static __attribute__((noinline)) void field(u32 tag, u32 v)
{
    put(tag);
    hex(v);
    put(' ');
}

void gecko_log(u32 chan)                /* r3 = KPADRead's channel */
{
    u32 tb, i;

    /* How many samples a real Wii Remote queues per read, averaged by
     * the reader: channel 0's queued count (+0x10F) at KPADRead entry. */
    if (chan == 0) {
        Q_SAMPLES += *(volatile unsigned char *)(0x803C91C0 + 0x10F);
        Q_READS += 1;
    }

    __asm__ volatile ("mftb %0" : "=r"(tb));
    if (tb - STAMP < 12150000)          /* 0.2 s at 60.75 MHz */
        return;
    STAMP = tb;

    if (exchange(0x90000000) != 0x04700000)   /* USB Gecko ID check */
        return;

    put('t');
    for (i = 0; i < 4; i++) {
        hex(REG(0x80331550 + i * 4));   /* si:: cached type */
        put(i < 3 ? ',' : ' ');
    }
    field('S', REG(0xCD006438));
    field('L', REG(0xCD006430));
    put('I');
    hex(REG(0xCD006404));
    put(' ');
    put('K');
    for (i = 0; i < 2; i++) {
        u32 k = 0x803C91C0 + i * 0x524;
        hex(REG(k));
        put(',');
        hex(REG(k + 0x5C));
        put(' ');
    }
    put('Q');
    hex(((u32)Q_SAMPLES << 16) | Q_READS);
    Q_SAMPLES = 0;
    Q_READS = 0;
    put('\r');
    put('\n');
}

/* Called from a stub at the entry of the OS's unhandled-exception handler
 * (__OSUnhandledException, 0x801C0B34) before it prints its register dump
 * -- which goes nowhere on retail hardware. Sends the essentials over the
 * Gecko instead:
 *
 *   CRASH <type> SRR0 <pc> SRR1 <msr> LR <lr> R1 <sp> DSISR <x> DAR <x>
 *     BT <saved LR of each stack frame, innermost first>
 */
void gecko_crash(u32 type, u32 *ctx, u32 dsisr, u32 dar)
{
    u32 sp, i;

    if (exchange(0x90000000) != 0x04700000)
        return;
    put('\r'); put('\n');
    put('C'); put('R'); put('A'); put('S'); put('H'); put(' ');
    hex(type); put(' ');
    field('P', ctx[0x198 / 4]);         /* SRR0 */
    field('M', ctx[0x19c / 4]);         /* SRR1 */
    field('L', ctx[0x84 / 4]);          /* LR */
    field('R', ctx[1]);                 /* r1 */
    field('D', dsisr);
    field('A', dar);
    put('B'); put('T');
    sp = ctx[1];
    for (i = 0; i < 10; i++) {
        if (sp < 0x80000000 || sp >= 0x81800000 || (sp & 3))
            break;
        put(' ');
        hex(((u32 *)sp)[1]);            /* LR save word of this frame */
        sp = ((u32 *)sp)[0];            /* back chain */
    }
    put('\r'); put('\n');
}
