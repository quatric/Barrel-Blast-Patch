    # Code E @ 0x80247BE0 -- synthesise a KPAD sample for a bare GameCube pad.
    #
    # Hook site is `lbz r0,0x10f(r31)` in KPADiRead, immediately before the
    # early-out at 0x80247BE8 that returns when the channel has no queued
    # samples. With no Wii Remote connected that count is always zero, so
    # KPADiRead returns before codeA-D ever run and there is nothing for them
    # to inject into -- which is why the hack needed a Wii Remote plugged in
    # even though every input came from the GameCube port.
    #
    # This fabricates one zeroed sample per frame when, and only when, the
    # channel has a valid GameCube response on the Serial Interface and no
    # real sample arrived. codeA-D overwrite buttons, motion and stick from
    # SI further down, so the sample's contents barely matter -- but three
    # bytes do, and they gate whether the rest of KPAD will look at it:
    #
    #   +0x28  extension valid, must be 1     (checked at 0x802462C8)
    #   +0x29  extension error, must be 0     (checked at 0x802462BC)
    #   +0x36  device type, must be 4 or 5    (checked at 0x802462D4)
    #
    # +0x36 = 4 satisfies both accelerometer blocks in read_kpad_acc: the Wii
    # Remote one accepts 1, 2, 4, 5, 7 and 8, and the Nunchuk one -- where
    # the left drum's motion vector is read -- accepts only 4 and 5.
    #
    # Registers here: r31 = this channel's KPAD base, r27 = channel index.
    # The sample ring is 16 entries of 0x38 bytes at +0x110, write index at
    # +0x10E, count at +0x10F -- same layout the game's own WPAD callback
    # fills in at 0x802485C8, including its wrap-at-read convention.
    #
    # The same three bytes also gate a *real* Wii Remote sample with nothing
    # plugged into it (it reports device type 0): the Nunchuk block is
    # skipped, and the left drum -- which codeB writes into the Nunchuk
    # motion vector -- never reaches the game. So when a GameCube pad is
    # active and no extension is attached, every queued real sample gets the
    # same three bytes. Wii Remote buttons, pointer and motion are untouched.
    #
    # Incoming r0 is dead (the hooked instruction overwrites it), so only
    # r3-r7 need saving.

    stwu    1, -0x20(1)
    stw     3, 0x1c(1)
    stw     4, 0x18(1)
    stw     5, 0x14(1)
    stw     6, 0x10(1)
    stw     7, 0x0c(1)

    lbz     3, 0x5c(31)         # KPAD dev_type: 0 = bare Wii Remote,
    cmpwi   3, 0                # 0xFD = no Wii Remote on this channel
    beq     dev_ok
    cmpwi   3, 0xFD             # (seen on hardware: a GameCube pad on a
    bne     done                # remote-less channel was ignored because
dev_ok:                         # only 0 was accepted); never touch
                                # Classic/Nunchuk/Bongo state

    cmpwi   27, 4
    bge     done                # channel out of range

    lis     4, 0xCD00
    mulli   5, 27, 12           # SI channel register stride
    addi    5, 5, 0x6404
    lwzx    5, 4, 5             # INBUFH
    andis.  0, 5, 0x8000
    bne     done                # ERRSTAT: nothing on this port
    andis.  0, 5, 0x0080
    beq     done                # no valid GameCube response

    lbz     4, 0x10e(31)        # write index (may be 0x10: wraps on read)
    lbz     3, 0x10f(31)        # queued sample count
    cmpwi   3, 0
    bne     patch_real

    # No Wii Remote sample queued: fabricate one.
    cmplwi  4, 0x10
    blt     index_ok
    li      4, 0
index_ok:
    mulli   5, 4, 0x38
    add     5, 31, 5
    addi    5, 5, 0x110         # r5 = &ring[index]

    li      6, 0                # zero the 0x38-byte sample
    li      3, 0x34
zero_loop:
    stwx    6, 5, 3
    addic.  3, 3, -4
    bge     zero_loop

    bl      mark                # extension valid / no error / Nunchuk-class

    addi    4, 4, 1
    stb     4, 0x10e(31)        # advance write index (wraps on next read)
    li      6, 1
    stb     6, 0x10f(31)        # publish one sample
    b       done

    # Real samples queued with no extension: mark the newest `count` entries,
    # ring[(write_index - k) & 0xF] for k = 1..count.
patch_real:
    cmplwi  3, 0x10
    ble     count_ok
    li      3, 0x10
count_ok:
    subf    7, 3, 4             # oldest queued = write_index - count
patch_loop:
    andi.   5, 7, 0xF
    mulli   5, 5, 0x38
    add     5, 31, 5
    addi    5, 5, 0x110
    bl      mark
    addi    7, 7, 1
    cmpw    7, 4
    blt     patch_loop
    b       done

    # r5 = sample. Clobbers r6 only.
mark:
    li      6, 1
    stb     6, 0x28(5)          # extension valid
    li      6, 0
    stb     6, 0x29(5)          # no extension error
    li      6, 4
    stb     6, 0x36(5)          # device type: Nunchuk-class
    blr

done:
    lwz     3, 0x1c(1)
    lwz     4, 0x18(1)
    lwz     5, 0x14(1)
    lwz     6, 0x10(1)
    lwz     7, 0x0c(1)
    addi    1, 1, 0x20
    lbz     0, 0x10f(31)        # ORIGINAL INSTRUCTION
