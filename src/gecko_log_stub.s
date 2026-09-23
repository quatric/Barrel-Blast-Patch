    # Calls gecko_log() (src/gecko_log.c) with every volatile register and
    # CR/LR/CTR/XER preserved, then falls through to whatever follows it:
    # the SI poller body, or (log-only builds) the hooked instruction.
    # inject_dol.py places the compiled logger right after this stub; the
    # `bl` below is patched to point at it.
    stwu    1, -0x50(1)
    stw     0, 0x08(1)
    mflr    0
    stw     0, 0x54(1)
    stw     3, 0xc(1)
    stw     4, 0x10(1)
    stw     5, 0x14(1)
    stw     6, 0x18(1)
    stw     7, 0x1c(1)
    stw     8, 0x20(1)
    stw     9, 0x24(1)
    stw     10, 0x28(1)
    stw     11, 0x2c(1)
    stw     12, 0x30(1)
    mfcr    0
    stw     0, 0x40(1)
    mfctr   0
    stw     0, 0x44(1)
    mfxer   0
    stw     0, 0x48(1)
    bl      .                   # -> gecko_log (patched)
    lwz     0, 0x48(1)
    mtxer   0
    lwz     0, 0x44(1)
    mtctr   0
    lwz     0, 0x40(1)
    mtcrf   0xff, 0
    lwz     3, 0xc(1)
    lwz     4, 0x10(1)
    lwz     5, 0x14(1)
    lwz     6, 0x18(1)
    lwz     7, 0x1c(1)
    lwz     8, 0x20(1)
    lwz     9, 0x24(1)
    lwz     10, 0x28(1)
    lwz     11, 0x2c(1)
    lwz     12, 0x30(1)
    lwz     0, 0x54(1)
    mtlr    0
    lwz     0, 0x08(1)
    addi    1, 1, 0x50
