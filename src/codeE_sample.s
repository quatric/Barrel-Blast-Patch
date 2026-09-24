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

    cmplwi  27, 3
    bgt     out                 # channel out of range

    # r7 = this channel's "last sample was ours" flag, in the injected
    # section's scratch bytes (+0x18..+0x1B).
    lis     7, 0x8000
    ori     7, 7, 0x1838
    add     7, 7, 27

    lbz     3, 0x5c(31)         # KPAD dev_type: 0 = bare Wii Remote,
    cmpwi   3, 0                # 0xFD = no Wii Remote on this channel
    beq     dev_ok              # (seen on hardware: a GameCube pad on a
    cmpwi   3, 0xFD             # remote-less channel was ignored because
    beq     dev_ok              # only 0 was accepted)
    # 1 (Nunchuk) is fine only when it's our own synthesised sample from
    # the last read showing through: otherwise dev_type alternated 1/0
    # every read and the game flickered between "Attach the Nunchuk" and
    # OK. A real Classic/Nunchuk/Bongo extension is never touched.
    cmpwi   3, 1
    bne     done
    lbz     3, 0(7)
    cmpwi   3, 0
    beq     done
dev_ok:

    lis     4, 0xCD00
    mulli   5, 27, 12           # SI channel register stride
    addi    5, 5, 0x6404
    lwzx    5, 4, 5             # INBUFH
    andis.  0, 5, 0x8000
    bne     out                 # ERRSTAT: nothing on this port
    andis.  0, 5, 0x0080
    beq     out                 # no valid GameCube response
    # (both keep the flag: with no Wii Remote nothing refreshes dev_type,
    # so it stays at our own 1 while the pad is unplugged, and clearing
    # the flag there made codeE refuse the pad for good once it came back)

    lbz     4, 0x10e(31)        # write index (may be 0x10: wraps on read)
    lbz     3, 0x10f(31)        # queued sample count
    cmpwi   3, 0
    bne     patch_real

    # No Wii Remote sample queued: fabricate three. A real Wii Remote
    # delivers 2-3 samples per read (USB Gecko log), and the drum logic in
    # read_kpad_acc runs once per sample, so a single synthesised sample made
    # a remote-less player far less responsive than one with a real remote.
    # 3,3,3,4 samples per read = 3.25 on average, what a real Wii Remote
    # queued in the USB Gecko log (39 samples / 12 reads). The flag byte
    # doubles as the 1..4 cycle position (nonzero = our samples).
    lbz     3, 0(7)
    addi    3, 3, 1
    cmplwi  3, 4
    ble     cyc_ok
    li      3, 1
cyc_ok:
    stb     3, 0(7)
    li      0, 3                # samples to publish (r0 is dead at this hook)
    cmplwi  3, 4
    bne     synth_loop
    li      0, 4
synth_loop:
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
    addic.  0, 0, -1
    bne     synth_loop

    stb     4, 0x10e(31)        # advance write index (wraps on next read)
    lbz     6, 0(7)
    cmplwi  6, 4
    li      6, 3
    bne     pub
    li      6, 4
pub:
    stb     6, 0x10f(31)        # publish the samples (flag already set)
    b       out

    # Real samples queued with no extension: mark the newest `count` entries,
    # ring[(write_index - k) & 0xF] for k = 1..count.
patch_real:
    li      6, 0
    stb     6, 0(7)             # real samples: dev_type is the remote's own
    cmplwi  3, 0x10
    ble     count_ok
    li      3, 0x10
count_ok:
    subf    3, 3, 4             # oldest queued = write_index - count
patch_loop:
    andi.   5, 3, 0xF
    mulli   5, 5, 0x38
    add     5, 31, 5
    addi    5, 5, 0x110
    bl      mark
    addi    3, 3, 1
    cmpw    3, 4
    blt     patch_loop
    b       out

    # r5 = sample. Clobbers r6 only.
mark:
    li      6, 1
    stb     6, 0x28(5)          # extension valid
    li      6, 0
    stb     6, 0x29(5)          # no extension error
    li      6, 5
    stb     6, 0x36(5)          # data format 5: read_kpad_acc's Wii Remote
                                # and Nunchuk blocks both accept it, and so
                                # does the IR routine (2/5/8) -- with 4 the
                                # IR routine skipped the sample and codeD
                                # never drew a remote-less player's pointer
    blr

done:
    li      3, 0
    stb     3, 0(7)             # not synthesising on this channel
out:
    lwz     3, 0x1c(1)
    lwz     4, 0x18(1)
    lwz     5, 0x14(1)
    lwz     6, 0x10(1)
    lwz     7, 0x0c(1)
    addi    1, 1, 0x20
    lbz     0, 0x10f(31)        # ORIGINAL INSTRUCTION
