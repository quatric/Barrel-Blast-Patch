    # SI poller -- C2 hook at KPADiRead entry 0x80247ADC, which runs once per
    # channel per frame, before the button/motion/stick hooks read INBUFH.
    #
    # Programs SI hardware auto-polling (SICnOUTBUF + the SIPOLL enable /
    # copy-on-vblank bits) so the console's own SI logic fills SICnINBUFH/L.
    # Software cannot write those result registers -- that was the bug that
    # made the earlier build work in Dolphin and nowhere else.
    #
    # Exactly 27 words: the codehandler overwrites word 28 with the branch
    # back, and the injected code list fills its segment with no slack, so
    # this must not grow. (tools/build_full_dol.py can grow the list, but the
    # plain .ini and static-patch paths cannot.)

    stwu    1, -0x20(1)
    stw     0, 0x1c(1)
    stw     3, 0x18(1)
    stw     4, 0x14(1)
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

    lwz     0, 0x1c(1)
    lwz     3, 0x18(1)
    lwz     4, 0x14(1)
    addi    1, 1, 0x20
    stwu    1, -0xc0(1)         # ORIGINAL INSTRUCTION
