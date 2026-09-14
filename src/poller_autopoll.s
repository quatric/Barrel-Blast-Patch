    # SI poller -- C2 hook at KPADiRead entry 0x80247ADC, which runs once per
    # channel per frame, before the button/motion/stick hooks read INBUFH.
    #
    # Programs SI hardware auto-polling (SICnOUTBUF + the SIPOLL enable /
    # copy-on-vblank bits) so the console's own SI logic fills SICnINBUFH/L.
    # Software cannot write those result registers -- that was the bug that
    # made the earlier build work in Dolphin and nowhere else.
    #
    # SIGetType is deliberately called for the current channel each pass. Its
    # SDK implementation caches/throttles probes, but schedules a fresh type
    # transfer after disconnect/error, which is what makes a reinserted pad
    # come back without rebooting the game.

    stwu    1, -0x20(1)
    stw     0, 0x1c(1)
    stw     3, 0x18(1)
    stw     4, 0x14(1)
    mflr    0
    stw     0, 0x0c(1)

    lis     12, 0x801f
    ori     12, 12, 0x4fa0      # si::SIGetType(channel)
    mtctr   12
    bctrl
    lwz     0, 0x0c(1)
    mtlr    0
    lwz     3, 0x18(1)          # restore channel argument
    lis     3, 0xCD00

    # Acknowledge every channel's latched error status (NOREP/COLL/OVRUN/
    # UNRUN = the low nibble of each channel's SISR byte, write-1-to-clear;
    # writing 0 to a bit leaves it alone). Without this, unplugging a pad
    # latches NOREP forever: ERRSTAT stays set in INBUFH, every hook's error
    # check skips injection, and replugging never recovers until reboot.
    # Masking to the error nibbles before writing back is the game's own SI
    # library idiom -- si::SIInterruptHandler does it a channel at a time at
    # 0x801f4574-0x801f458c (lis 0x0F00; sraw by chan*8; and; store back).
    lwz     4, 0x6438(3)
    lis     0, 0x0F0F
    ori     0, 0, 0x0F0F
    and     4, 4, 0
    stw     4, 0x6438(3)

    # Poll command into all four channels' output buffers.
    lis     0, 0x0040
    ori     0, 0, 0x0300
    stw     0, 0x6400(3)        # SIC0OUTBUF
    stw     0, 0x640C(3)        # SIC1OUTBUF
    stw     0, 0x6418(3)        # SIC2OUTBUF
    stw     0, 0x6424(3)        # SIC3OUTBUF

    lwz     0, 0x6430(3)        # SIPOLL
    andi.   4, 0, 0xff00        # Y field already set?
    bne     ypresent
    ori     0, 0, 0x0100        # Y = 1
ypresent:
    ori     0, 0, 0x00FF        # enable + copy-on-vblank, all four channels
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
    # Recovery: if the flag has read non-idle for ~1 real second (4 channel
    # calls/frame * 60fps), force it back to -1 and reset SICOMCSR to
    # 0x80000000 -- the exact idle value si::SIInit itself writes at boot --
    # bypassing the game's own (timeout-less) completion path entirely.
    # 1 second is far beyond any legitimate transfer's duration, so this
    # can't misfire against a merely-slow transfer, only a truly hung one.
    #
    # Counter lives at channel 0's KPAD +0x100. This is global state (the
    # busy flag itself is global, not per-channel) parked in channel 0's
    # struct for a fixed, always-valid address; +0x100 is confirmed clear
    # of the existing drum edge-trigger scratch at +0x108/+0x10c (see
    # codeB_cc.s) and well short of +0x10e/+0x10f (the real KPAD sample
    # ring index/count).

    lis     5, 0x8033
    ori     5, 5, 0x1538
    lwz     6, 0(5)             # si:: global transfer-busy flag; -1 = idle
    lis     7, 0x803C
    ori     7, 7, 0x9100        # channel-0 KPAD +0x100: watchdog frame counter
    cmpwi   6, -1
    bne     wd_busy
    li      8, 0
    b       wd_store
wd_busy:
    lwz     8, 0(7)
    addi    8, 8, 1
    cmpwi   8, 0xF0             # ~1s at 60fps, 4 channel-calls/frame
    blt     wd_store
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
    addi    1, 1, 0x20
    nop                         # pad -- keeps the reproduced original
                                # instruction at the required 2nd-to-last
                                # word slot for a fixed total word count
    stwu    1, -0xc0(1)         # ORIGINAL INSTRUCTION
