    # SI poller -- C2 hook at KPADiRead entry 0x80247ADC, which runs once per
    # channel per frame, before the button/motion/stick hooks read INBUFH.
    #
    # Programs SI hardware auto-polling (SICnOUTBUF + the SIPOLL enable /
    # copy-on-vblank bits) so the console's own SI logic fills SICnINBUFH/L.
    # Software cannot write those result registers -- that was the bug that
    # made the earlier build work in Dolphin and nowhere else.
    #
    # SIGetType is called only for a channel whose cached type is not a
    # confirmed standard pad (8 = no response, 0x80 = probe pending, or
    # anything else). si:: never learns this game polls, so for a confirmed
    # pad it would re-probe every ~50 ms and flip the type to pending each
    # time; skipping the call keeps confirmed pads stable and polled.

    stwu    1, -0x20(1)
    stw     0, 0x1c(1)
    stw     3, 0x18(1)
    stw     4, 0x14(1)
    stw     5, 0x08(1)          # KPADRead's sample count (it does `mr r14, r5`
                                # right after this hook) -- SIGetType is free to
                                # clobber r5, and a garbage count hangs the game
    mflr    0
    stw     0, 0x0c(1)

    cmplwi  3, 3
    bgt     probe_done
    lis     12, 0x8033
    ori     12, 12, 0x1550      # si:: cached type per channel
    slwi    4, 3, 2
    lwzx    4, 12, 4
    andi.   12, 4, 0x80
    bne     do_probe            # pending: let SIGetType follow it up
    rlwinm  12, 4, 0, 3, 4      # & 0x18000000
    lis     4, 0x0800
    cmpw    12, 4
    beq     probe_done          # confirmed standard pad: leave it alone
do_probe:
    lis     12, 0x801f
    ori     12, 12, 0x4fa0      # si::SIGetType(channel)
    mtctr   12
    bctrl
probe_done:
    lwz     0, 0x0c(1)
    mtlr    0
    # Un-stick a probe that can never finish. SIGetType parks the cached
    # type at 0x80 ("pending") while its type transfer is outstanding, and
    # only that transfer's callback replaces it. After a soft reset the
    # cache can come up pending with no transfer in flight at all (busy
    # flag idle) -- seen in Dolphin right after a HOME Menu Reset -- and
    # then it stays pending forever: the channel is never polled again,
    # the hooks keep reading the stale pre-reset input buffer (a pointer
    # that drifts on its own, Wii Remote input overridden), and only
    # unplugging the pad recovers. If this channel has been pending with
    # the SI idle for over a second, mark it "no response" (8) so the next
    # SIGetType probes it again. Per-channel time-base stamps live in the
    # scratch words inject_dol.py reserves at the start of the injected
    # section (0x80001820), 0 = not pending.
    lwz     3, 0x18(1)          # channel
    cmplwi  3, 3
    bgt     pend_done
    lis     6, 0x8033
    ori     6, 6, 0x1550        # si:: cached type per channel
    slwi    7, 3, 2
    lwzx    8, 6, 7
    lis     9, 0x8000
    ori     9, 9, 0x1820        # pending-since stamps
    cmpwi   8, 0x80
    bne     pend_clear
    lis     10, 0x8033
    lwz     10, 0x1538(10)      # si:: transfer-busy flag, -1 = idle
    cmpwi   10, -1
    bne     pend_clear          # a transfer is in flight; it will resolve
    lwzx    10, 9, 7
    mftb    11
    cmpwi   10, 0
    bne     pend_timing
    ori     11, 11, 1           # first sighting (never store 0)
    stwx    11, 9, 7
    b       pend_done
pend_timing:
    subf    12, 10, 11
    lis     0, 0x039F
    ori     0, 0, 0x8B0         # 60,750,000 ticks = 1 s
    cmplw   12, 0
    blt     pend_done
    li      0, 8
    stwx    0, 6, 7             # "no response": SIGetType re-probes
pend_clear:
    li      0, 0
    stwx    0, 9, 7
pend_done:

    lwz     3, 0x18(1)          # restore channel argument
    lis     3, 0xCD00

    # Re-probe after a disconnect. SIGetType only sends a fresh type command
    # (0x00) when the SDK's cached type for the channel is 8 ("no
    # response"), and the only thing that sets 8 is the SDK reading NOREP
    # (0x08 in the channel's SISR byte) -- which this game never does,
    # because it never uses the PAD library. So copy NOREP into the cache
    # ourselves, *before* acknowledging it below: the next SIGetType for
    # that channel then probes it, and a replugged pad gets the 0x00 it
    # needs instead of being polled with a stale cached type forever.
    lwz     4, 0x6438(3)        # SISR
    lis     6, 0x8033
    ori     6, 6, 0x1550        # si:: cached type per channel (4 words)
    li      8, 0                # channel
    li      9, 8                # "no response" type
norep_loop:
    slwi    10, 8, 3
    lis     11, 0x0800          # channel 0's NOREP bit
    srw     11, 11, 10
    and.    11, 11, 4
    beq     norep_next
    slwi    10, 8, 2
    stwx    9, 6, 10
norep_next:
    addi    8, 8, 1
    cmpwi   8, 4
    blt     norep_loop

    # Poll command into all four channels' output buffers.
    lis     0, 0x0040
    ori     0, 0, 0x0300
    stw     0, 0x6400(3)        # SIC0OUTBUF
    stw     0, 0x640C(3)        # SIC1OUTBUF
    stw     0, 0x6418(3)        # SIC2OUTBUF
    stw     0, 0x6424(3)        # SIC3OUTBUF

    # One SISR write both acknowledges every channel's latched error
    # nibble (NOREP/COLL/OVRUN/UNRUN, write-1-to-clear; the game's own
    # SIInterruptHandler idiom) and sets WR (bit 31), which is what actually
    # transfers the SICnOUTBUF values above to the hardware -- the SDK's
    # SIEnablePolling always writes SISR = 0x80000000 before SIPOLL. Without
    # it the output buffers were never latched, and pads only worked while
    # polled with whatever command something else had latched before the
    # game booted.
    lis     0, 0x0F0F
    ori     0, 0, 0x0F0F
    and     4, 4, 0
    oris    4, 4, 0x8000
    stw     4, 0x6438(3)

    # Enable polling (and copy-on-vblank) only for channels whose cached
    # type is a confirmed standard pad ((type & 0x18000000) == 0x08000000,
    # not the 0x80 pending sentinel). On real hardware a channel's type
    # probe only succeeds while that channel is not being polled -- with
    # all four polled continuously, a replugged pad never came back -- and
    # the SDK likewise disables polling for a channel before probing it.
    # Confirmed pads are no longer re-probed (see SIGetType above), so
    # their polling stays on.
    li      7, 0                # enable mask
    li      8, 0
en_loop:
    slwi    10, 8, 2
    lwzx    10, 6, 10
    andi.   11, 10, 0x80
    bne     en_next             # probe pending
    rlwinm  11, 10, 0, 3, 4     # & 0x18000000
    lis     12, 0x0800
    cmpw    11, 12
    bne     en_next
    li      11, 0x88            # EN + VBCPY bits for channel 0
    srw     11, 11, 8
    or      7, 7, 11
en_next:
    addi    8, 8, 1
    cmpwi   8, 4
    blt     en_loop

    lwz     0, 0x6430(3)        # SIPOLL
    rlwinm  0, 0, 0, 0, 23      # clear the enable/VBCPY byte, keep X/Y
    andi.   4, 0, 0xff00        # Y field already set?
    bne     ypresent
    ori     0, 0, 0x0100        # Y = 1
ypresent:
    or      0, 0, 7
    stw     0, 0x6430(3)

    # Hot-plug watchdog. si::__SITransfer (0x801f48ec) gates every SI
    # transfer -- SIGetType included -- behind ONE global "busy" flag at
    # 0x80331538 (-1 = idle); if it isn't -1, __SITransfer just no-ops.
    # The only place that ever clears it is si::CompleteTransfer, itself
    # only reachable from si::SIInterruptHandler's branch gated on BOTH
    # SICOMCSR TC-complete bits (0xc0000000) being set together. A GameCube
    # pad unplugged mid-transfer signals an SI error (NOREP in SISR)
    # instead of a clean completion, so that gate never opens: the flag
    # stays wedged on the dead channel and every future SIGetType call, for
    # ALL four channels, silently no-ops forever. Confirmed by exhaustive
    # xref search: nothing outside si:: itself ever touches 0x80331538, and
    # nothing inside it times out. This has NOT been confirmed on hardware
    # as the actual cause of the "hot-plug doesn't recover" bug -- it is
    # the leading hypothesis from static analysis of the retail code.
    #
    # Recovery: if the flag has read non-idle for ~1 real second, force it
    # back to -1 and reset SICOMCSR to
    # 0x80000000 -- the exact idle value si::SIInit itself writes at boot --
    # bypassing the game's own (timeout-less) completion path entirely.
    # 1 second is far beyond any legitimate transfer's duration, so this
    # can't misfire against a merely-slow transfer, only a truly hung one.
    #
    # "1 second" is measured with the time base (mftb, 60.75 MHz on Wii),
    # not by counting hook calls: KPADRead is not strictly 4 calls/frame --
    # during boot the game calls it in a tight loop, so a call-count budget
    # elapsed in microseconds, fired mid-way through a legitimate SIGetType
    # transfer, killed it before its callback ran, and left every channel's
    # cached type stuck at the 0x80 pending sentinel -- a black screen at
    # boot (reproduced in Dolphin; removing any one block of this body hid
    # it only by changing how long transfers stayed busy).
    #
    # The low time-base word at which the flag was first seen busy lives at
    # channel 0's KPAD +0x100 (0 = not currently busy). This is global state (the
    # busy flag itself is global, not per-channel) parked in channel 0's
    # struct for a fixed, always-valid address; +0x100 is confirmed clear
    # of the existing drum edge-trigger scratch at +0x108/+0x10c (see
    # codeB_cc.s) and well short of +0x10e/+0x10f (the real KPAD sample
    # ring index/count).

    lis     5, 0x8033
    ori     5, 5, 0x1538
    lwz     6, 0(5)             # si:: global transfer-busy flag; -1 = idle
    lis     7, 0x803C
    ori     7, 7, 0x92C0        # channel-0 KPAD (0x803C91C0) +0x100: time base when busy began
    cmpwi   6, -1
    bne     wd_busy
    li      8, 0
    b       wd_store
wd_busy:
    lwz     8, 0(7)
    mftb    9
    cmpwi   8, 0
    bne     wd_timing
    ori     8, 9, 1             # first busy sighting: stamp it (never store 0)
    b       wd_store
wd_timing:
    subf    10, 8, 9            # ticks busy (unsigned, wrap-safe)
    lis     11, 0x039F
    ori     11, 11, 0x8B0       # 60,750,000 ticks = 1 s
    cmplw   10, 11
    blt     wd_done
    mflr    9
    stw     9, 0x10(1)
    lis     12, 0x801c
    ori     12, 12, 0x3a40      # OSDisableInterrupts
    mtctr   12
    bctrl
    mr      9, 3
    lis     5, 0x8033
    ori     5, 5, 0x1538
    li      6, -1
    stw     6, 0(5)             # unwedge si::'s global busy flag
    lis     6, 0xCD00
    lis     0, 0x8000
    stw     0, 0x6434(6)        # SICOMCSR = 0x80000000 (si::SIInit's own boot-reset value)
    mr      3, 9
    lis     12, 0x801c
    ori     12, 12, 0x3a68      # OSRestoreInterrupts
    mtctr   12
    bctrl
    lwz     9, 0x10(1)
    mtlr    9
    li      8, 0
wd_store:
    stw     8, 0(7)
wd_done:

    lwz     0, 0x1c(1)
    lwz     3, 0x18(1)
    lwz     4, 0x14(1)
    lwz     5, 0x08(1)
    addi    1, 1, 0x20
    nop                         # pad: C2 bodies need an odd word count
    stwu    1, -0xc0(1)         # ORIGINAL INSTRUCTION
