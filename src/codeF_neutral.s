    # Code F @ 0x80247FA8 -- one input source at a time for motion.
    #
    # Hook site is `mulli r0,r30,0x84` in KPADiRead, immediately after it
    # recomputes the per-axis accelerometer scale factors at KPAD
    # +0x4dc/+0x4e0/+0x4e4 (Wii Remote) and +0x4e8/+0x4ec/+0x4f0 (Nunchuk)
    # and before read_kpad_acc processes this read's samples with them.
    #
    # Priority is GameCube pad, then Classic Controller, then Wii Remote +
    # Nunchuk. While a GameCube pad (valid SI response on this channel) or a
    # Classic Controller is the active input, zero the scale factors so the
    # physical Wii Remote's own motion contributes nothing: codeB's
    # synthesised drum strokes are then the only motion the game sees,
    # instead of competing with a remote that is lying still or being
    # handled, which made some pad presses fail to register. The factors
    # are recomputed on every read, so unplugging the pad or extension
    # hands motion straight back to the Wii Remote.
    #
    # r27 = channel, r31 = KPAD channel base. r0, r3 and r4 are dead here:
    # the hooked instruction overwrites r0, and the next two load r3/r4.

    cmplwi  27, 3
    bgt     check_cc
    lis     3, 0xCD00
    mulli   4, 27, 12           # SI channel register stride
    addi    4, 4, 0x6404
    lwzx    4, 3, 4             # INBUFH
    andis.  0, 4, 0x8000
    bne     check_cc            # ERRSTAT: no pad on this port
    andis.  0, 4, 0x0080
    bne     neutral             # valid GameCube response
check_cc:
    lbz     4, 0x5c(31)         # extension type
    cmpwi   4, 2                # Classic Controller
    bne     done
neutral:
    li      0, 0
    stw     0, 0x4dc(31)
    stw     0, 0x4e0(31)
    stw     0, 0x4e4(31)
    stw     0, 0x4e8(31)
    stw     0, 0x4ec(31)
    stw     0, 0x4f0(31)
done:
    mulli   0, 30, 0x84         # ORIGINAL INSTRUCTION
